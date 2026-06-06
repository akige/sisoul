"""sisoul friend kudos — non-transferable reciprocity counter (§4.10-compatible).

Per docs/INCENTIVE-DESIGN.md:

- Lender earns kudos when they let a borrower use their LLM quota.
- Borrower spends kudos when they borrow.
- Kudos cannot be transferred to a third party, cannot be sold, cannot be
  withdrawn for fiat. They are a Stack-Overflow-rep-style counter, not a security.
- Decays at 5% per calendar month if unused (prevents hoarding).

Storage: SQLite at $SISOUL_VAULT/kudos.db. Schema:
  ledger(id, ts, peer_did, delta, reason, balance_after_for_peer)
  balances(peer_did, amount, last_decay_at)

API:
  KudosStore(path).balance(did) -> float
  KudosStore(path).earn(peer_did, amount, reason)
  KudosStore(path).spend(peer_did, amount, reason) -> raises KudosInsufficient
  KudosStore(path).grant(peer_did, amount, reason)   # admin/test seed
  KudosStore(path).apply_decay(now=None)            # called by nightly job
  KudosStore(path).history(peer_did=None, limit=100)

All amounts are stored as float (lenders may charge fractional kudos / 1k tokens).
"""
from __future__ import annotations
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


class KudosError(Exception):
    """Base."""


class KudosInsufficient(KudosError):
    """Borrower does not have enough kudos to spend."""


@dataclass(frozen=True)
class KudosEntry:
    id: int
    ts: float
    peer_did: str
    delta: float
    reason: str
    balance_after: float


_SCHEMA = """
CREATE TABLE IF NOT EXISTS kudos_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    peer_did TEXT NOT NULL,
    delta REAL NOT NULL,
    reason TEXT NOT NULL,
    balance_after REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kudos_ledger_peer ON kudos_ledger(peer_did);
CREATE INDEX IF NOT EXISTS idx_kudos_ledger_ts ON kudos_ledger(ts);

CREATE TABLE IF NOT EXISTS kudos_balances (
    peer_did TEXT PRIMARY KEY,
    amount REAL NOT NULL DEFAULT 0.0,
    last_decay_at REAL NOT NULL DEFAULT 0.0
);
"""


def default_kudos_path() -> Path:
    vault = Path(os.environ.get("SISOUL_VAULT", str(Path.home() / ".sisoul"))).expanduser()
    vault.mkdir(parents=True, exist_ok=True)
    return vault / "kudos.db"


class KudosStore:
    """Local SQLite kudos store. Per-vault, per-user.

    The kudos number stored is always FROM THE LENS OF THIS USER — for each
    peer_did, balance is "how many kudos this peer owes me (positive) or I owe
    them (negative)". This keeps it symmetric: when you lend to bob 100 kudos,
    your store says bob: +100; bob's store says alice: -100.
    """

    DECAY_PER_MONTH = 0.05  # 5% / month decay if positive and unused
    SECONDS_PER_MONTH = 30.0 * 86400

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_kudos_path()
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "KudosStore":
        return self

    def __exit__(self, *a) -> None:
        self.close()

    # ── core ops ─────────────────────────────────────────────────────────────

    def balance(self, peer_did: str) -> float:
        cur = self._conn.execute(
            "SELECT amount FROM kudos_balances WHERE peer_did = ?", (peer_did,)
        )
        row = cur.fetchone()
        return float(row["amount"]) if row else 0.0

    def earn(self, peer_did: str, amount: float, reason: str) -> float:
        """This user just lent to peer_did → peer_did owes us `amount` kudos.

        Returns new balance.
        """
        if amount <= 0:
            raise KudosError(f"earn amount must be > 0, got {amount}")
        return self._adjust(peer_did, +amount, reason)

    def spend(self, peer_did: str, amount: float, reason: str) -> float:
        """This user just borrowed from peer_did → we owe peer_did `amount` kudos.

        Decreases peer_did's positive balance, or goes negative if it would
        exceed.

        Raises KudosInsufficient if the resulting balance would be more
        negative than -1000 (a hard floor — borrowers can go a little into
        the red but not unboundedly).
        """
        if amount <= 0:
            raise KudosError(f"spend amount must be > 0, got {amount}")
        current = self.balance(peer_did)
        new = current - amount
        if new < -1000.0:
            raise KudosInsufficient(
                f"spending {amount} kudos to {peer_did[:16]}... would push "
                f"balance to {new:.1f}, below floor -1000. "
                f"Earn kudos by lending to friends first."
            )
        return self._adjust(peer_did, -amount, reason)

    def grant(self, peer_did: str, amount: float, reason: str) -> float:
        """Admin / test seed: directly set balance change without lending."""
        return self._adjust(peer_did, amount, f"grant: {reason}")

    def _adjust(self, peer_did: str, delta: float, reason: str) -> float:
        cur = self._conn.execute(
            "SELECT amount, last_decay_at FROM kudos_balances WHERE peer_did = ?",
            (peer_did,),
        )
        row = cur.fetchone()
        if row:
            current = float(row["amount"])
            last_decay = float(row["last_decay_at"])
        else:
            current = 0.0
            last_decay = time.time()
        new_balance = current + delta
        now = time.time()
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO kudos_balances (peer_did, amount, last_decay_at) "
                "VALUES (?, ?, ?)",
                (peer_did, new_balance, last_decay or now),
            )
            self._conn.execute(
                "INSERT INTO kudos_ledger (ts, peer_did, delta, reason, balance_after) "
                "VALUES (?, ?, ?, ?, ?)",
                (now, peer_did, delta, reason, new_balance),
            )
        return new_balance

    # ── decay ────────────────────────────────────────────────────────────────

    def apply_decay(self, now: Optional[float] = None) -> dict:
        """Apply 5%/month decay to all positive balances based on time elapsed since
        last_decay_at. Negative balances do not decay (they stay as debt).

        Returns dict {peer_did: (old_balance, new_balance, decay_factor)}.
        Idempotent: running it again with no time passing is a no-op.
        """
        ts = float(now) if now is not None else time.time()
        out: dict[str, tuple] = {}
        rows = self._conn.execute(
            "SELECT peer_did, amount, last_decay_at FROM kudos_balances"
        ).fetchall()
        for row in rows:
            peer = row["peer_did"]
            old = float(row["amount"])
            last = float(row["last_decay_at"])
            if old <= 0:
                # only positive balances decay
                with self._conn:
                    self._conn.execute(
                        "UPDATE kudos_balances SET last_decay_at = ? WHERE peer_did = ?",
                        (ts, peer),
                    )
                continue
            elapsed_months = max(0.0, (ts - last) / self.SECONDS_PER_MONTH)
            if elapsed_months < 1e-6:
                continue
            decay_factor = (1.0 - self.DECAY_PER_MONTH) ** elapsed_months
            new = old * decay_factor
            with self._conn:
                self._conn.execute(
                    "UPDATE kudos_balances SET amount = ?, last_decay_at = ? "
                    "WHERE peer_did = ?",
                    (new, ts, peer),
                )
                self._conn.execute(
                    "INSERT INTO kudos_ledger (ts, peer_did, delta, reason, balance_after) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ts, peer, new - old, f"decay {elapsed_months:.3f} months", new),
                )
            out[peer] = (old, new, decay_factor)
        return out

    # ── queries ──────────────────────────────────────────────────────────────

    def history(self, peer_did: Optional[str] = None, limit: int = 100) -> list[KudosEntry]:
        if peer_did:
            cur = self._conn.execute(
                "SELECT * FROM kudos_ledger WHERE peer_did = ? ORDER BY ts DESC LIMIT ?",
                (peer_did, limit),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM kudos_ledger ORDER BY ts DESC LIMIT ?", (limit,)
            )
        return [
            KudosEntry(
                id=r["id"], ts=r["ts"], peer_did=r["peer_did"],
                delta=r["delta"], reason=r["reason"], balance_after=r["balance_after"],
            )
            for r in cur.fetchall()
        ]

    def all_balances(self) -> dict[str, float]:
        cur = self._conn.execute("SELECT peer_did, amount FROM kudos_balances")
        return {r["peer_did"]: float(r["amount"]) for r in cur.fetchall()}


def compute_kudos_required(
    quota_share: "LLMQuotaShare", amount_tokens: int
) -> float:
    """Helper: how many kudos does a borrow cost given the lender's perm?

    Returns 0.0 if incentive_mode != 'kudos'.
    """
    if quota_share.incentive_mode != "kudos":
        return 0.0
    rate = float(quota_share.kudos_required_per_1k_tokens or 0.0)
    return rate * (amount_tokens / 1000.0)


def compute_usdt_required(
    quota_share: "LLMQuotaShare", amount_tokens: int
) -> float:
    """Helper: how many USDT does a borrow cost given the lender's perm?

    Returns 0.0 if incentive_mode != 'micropay'.
    """
    if quota_share.incentive_mode != "micropay":
        return 0.0
    rate = float(quota_share.usdt_per_1k_tokens or 0.0)
    return rate * (amount_tokens / 1000.0)


__all__ = [
    "KudosStore", "KudosEntry", "KudosError", "KudosInsufficient",
    "default_kudos_path", "compute_kudos_required", "compute_usdt_required",
]
