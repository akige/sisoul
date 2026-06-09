"""Auto-approve micropay borrow requests once the USDT payment lands.

v1.1 of the borrow / lend incentive layer. Closes the manual "lender checks
TronGrid then runs `sisoul lend approve`" loop.

Design
------
- Lender's daemon (mac/wsl/win — never aws, per §10.3 host policy) spawns
  a single LendAutoApprover task on startup.
- Every `poll_interval` seconds:
  1. Read all pending LendRequest rows from LendStore.
  2. For each pending request whose lender perm has `incentive_mode=micropay`
     and a known `usdt_payout_address`:
     a. Compute expected USDT amount from request.amount + perm.usdt_per_1k_tokens.
     b. Call `find_payment_for_borrow(payout_addr, expected, max_age=ttl,
        min_age=30)` (TronGrid public read, 5%% tolerance).
     c. If a matching tx is found AND its from-address matches a per-borrower
        wallet hint (if recorded) AND the tx is at least `min_confirmations`
        old: call `store.approve_lend(request.id)` and publish ACK on the
        borrower's lend-ack topic.
  3. Sleep `poll_interval`.
- Idempotent: re-running approve_lend on already-approved request is a no-op
  per `lend.py:approve_lend`.
- Opt-in: lender runs `sisoul lend auto-approve enable` to flip a flag in
  vault config. Disabled by default so the lender doesn't accidentally
  auto-approve micropay before they're ready to actually share LLM quota.

Security
--------
- Read-only against TronGrid (no signing).
- Approve decisions are persisted to LendStore SQLite + signed by the
  lender's own daemon (the lender is also the one running this task).
- Per §4.10: sisoul never touches USDT. Borrower pays lender directly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("sisoul.lend_auto_approve")


def _vault_config_path() -> Path:
    vault = Path(os.environ.get("SISOUL_VAULT", str(Path.home() / ".sisoul"))).expanduser()
    return vault / "lend_auto_approve.json"


def is_enabled() -> bool:
    """Lender opt-in: has the lender run `sisoul lend auto-approve enable`?"""
    p = _vault_config_path()
    if not p.exists():
        return False
    try:
        return bool(json.loads(p.read_text()).get("enabled"))
    except Exception:
        return False


def set_enabled(value: bool) -> None:
    p = _vault_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"enabled": bool(value), "updated_at": int(time.time())}, indent=2))


# ── core poller ─────────────────────────────────────────────────────────────


class LendAutoApprover:
    """Background coroutine that watches TronGrid + auto-approves micropay borrows.

    Wired into sisoul daemon startup (cloud hosts refuse via host_policy gate).
    """

    def __init__(
        self,
        my_did: str,
        *,
        lend_db: Optional[Path] = None,
        perms_dir: Optional[Path] = None,
        poll_interval_seconds: float = 30.0,
        min_age_seconds: float = 60.0,
        max_age_seconds: float = 3600.0,
        tolerance_pct: float = 5.0,
        transport: Any = None,  # for publishing ACK on lend-ack topic
    ):
        self.my_did = my_did
        self.lend_db = lend_db
        self.perms_dir = perms_dir
        self.poll = float(poll_interval_seconds)
        self.min_age = float(min_age_seconds)
        self.max_age = float(max_age_seconds)
        self.tolerance_pct = float(tolerance_pct)
        self.transport = transport
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._approved_ids: set[str] = set()  # dedupe between polls

    async def start(self) -> None:
        if not is_enabled():
            log.info("auto-approve disabled (run `sisoul lend auto-approve enable`)")
            return
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="sisoul-lend-auto-approve")
        log.info("auto-approve loop started (poll=%.0fs)", self.poll)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    n = await self.poll_once()
                    if n:
                        log.info("auto-approve poll: approved %d request(s)", n)
                except Exception as e:
                    log.warning("auto-approve poll failed: %s", e)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll)
                except asyncio.TimeoutError:
                    pass
        finally:
            log.info("auto-approve loop stopped")

    async def poll_once(self) -> int:
        """Single poll iteration. Returns number of requests auto-approved."""
        from sisoul.friend.lend import LendStore

        try:
            from sisoul.wallet.chain_watcher import find_payment_for_borrow, ChainWatcherError
        except Exception as e:
            log.debug("chain_watcher unavailable: %s", e)
            return 0

        try:
            from sisoul.friend.permissions import load_permissions
            from sisoul.friend.kudos import compute_usdt_required
        except Exception as e:
            log.debug("permissions/kudos unavailable: %s", e)
            return 0

        approved = 0
        with LendStore(db_path=self.lend_db) as store:
            # Scan pending requests addressed to me
            pending = [r for r in store.list_pending() if r.lender_did == self.my_did]
            for req in pending:
                if req.id in self._approved_ids:
                    continue
                try:
                    perm = load_permissions(req.borrower_did, perms_dir=self.perms_dir)
                except Exception:
                    # we may not have a perm for this borrower (stranger micropay)
                    perm = None
                # micropay: look up usdt amount + payout from perm (or fall back to req.note)
                expected_usdt = 0.0
                payout = ""
                if perm is not None:
                    q = perm.llm_quota_share
                    if getattr(q, "incentive_mode", "gift") != "micropay":
                        continue
                    expected_usdt = compute_usdt_required(q, req.amount)
                    payout = getattr(q, "usdt_payout_address", "") or ""
                else:
                    # stranger micropay: req must carry usdt_amount + payout in note
                    try:
                        body = json.loads(req.note or "{}")
                        expected_usdt = float(body.get("usdt_amount", 0))
                        payout = body.get("usdt_payout_address", "")
                    except Exception:
                        continue
                if expected_usdt <= 0 or not payout:
                    continue
                # Query TronGrid
                try:
                    match = find_payment_for_borrow(
                        payout, expected_usdt,
                        max_age_seconds=self.max_age,
                        min_age_seconds=self.min_age,
                        tolerance_pct=self.tolerance_pct,
                    )
                except ChainWatcherError as e:
                    log.debug("TronGrid lookup failed for %s: %s", req.id, e)
                    continue
                if match is None:
                    continue
                # Approve
                try:
                    store.approve_lend(req.id)
                    self._approved_ids.add(req.id)
                    approved += 1
                    log.info(
                        "auto-approved %s (paid %.4f USDT, tx %s, %.0fs old)",
                        req.id, match.value_usdt, match.tx_id, match.age_seconds,
                    )
                    # Publish ACK on borrower's lend-ack topic so their CLI sees it
                    if self.transport is not None:
                        try:
                            from sisoul.friend.lend_gossipsub import publish_lend_ack
                            await publish_lend_ack(
                                self.transport, self.my_did, req.borrower_did,
                                req.id, "approved",
                                reason=f"auto: USDT {match.value_usdt:.4f} via tx {match.tx_id[:16]}...",
                            )
                        except Exception as e:
                            log.warning("publish_lend_ack failed (auto-approve still persisted): %s", e)
                except Exception as e:
                    log.warning("approve_lend(%s) failed: %s", req.id, e)
                    continue
        return approved


__all__ = ["LendAutoApprover", "is_enabled", "set_enabled"]
