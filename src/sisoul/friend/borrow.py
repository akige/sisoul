"""sisoul friend · borrow (Alice 端) (§28 §3.3 §3.5) · Phase 4 W66-W76, 波 5 dev-D.

Alice 借 Bob 资源的完整流程:

    borrow_resource(friend_did, resource_type, amount, model)
        │
        ├─ 1. permissions.check_permission()  ── try dev-C, fallback "allowed if local"
        │      → allowed/denied/per-request
        │
        ├─ 2. 模式分支:
        │     strong-tie-auto → 直接发请求, 默认 approved (LendStore mock 同机模拟)
        │     per-request     → 发请求 + 轮询 LendStore 等 Bob approve (timeout)
        │     emergency-only  → 检查 emergency_flag, 否则 deny
        │
        ├─ 3. 加密 proxy: try dev-B encrypted_proxy.proxy_chat_request()
        │      → 失败 fallback "stub-passthrough" (mock LLM 返 dummy text, integration test 可注 mock)
        │
        ├─ 4. ledger.record_usage(direction="borrow")
        │
        └─ return BorrowSession (含 proxy 元数据 / 完成 ts / ledger entry_id)

sisoul borrow --proxy-to anthropic 模式:
    1. start_proxy_session() 起 127.0.0.1:9876/sisoul/borrow-proxy/{session_id} (daemon route)
    2. Claude Code 等工具 ANTHROPIC_BASE_URL=http://127.0.0.1:9876/sisoul/borrow-proxy/{sid}
    3. 用完 stop_proxy_session() 清理

不强依赖 dev-A/B/C, try/except 优雅 fallback 让本模块独立可测.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from sisoul.friend.ledger import LedgerEntry, ReciprocityLedger
from sisoul.friend.lend import (
    DEFAULT_LEND_DB,
    DEFAULT_PENDING_LENDS_FILE,
    LendRequest,
    LendStore,
    RequestStateError,
)

# v1.0-stable A3 (2026-06-09): primary lend-request transport is GossipSub
# (sisoul.friend.lend_gossipsub.publish_lend_request) — fully decentralised,
# no central directory. Waku _push_notify_friend_sync is kept as a soft
# fallback only and is a no-op when Waku isn't available (default in
# v1.0-stable).
try:
    from sisoul.p2p.push import notify_friend_sync as _push_notify_friend_sync
except Exception:  # noqa: BLE001 — push 模块未就绪绝不阻塞 borrow
    _push_notify_friend_sync = None  # type: ignore[assignment]


def _safe_notify(friend_did: str, kind: str, payload: dict) -> None:
    """v1.0-stable: prefer GossipSub publish_lend_request/ack; fall back to
    legacy Waku push. Never raises (borrow 主流程不许被推送拖死)."""
    # 1. GossipSub (primary path) ─ best-effort schedule on running loop.
    try:
        from sisoul.friend.lend_gossipsub import (
            publish_lend_request,
            publish_lend_ack,
        )
        import asyncio as _aio

        try:
            from sisoul.chat.transport import get_default_transport  # type: ignore
            transport = get_default_transport()
        except Exception:
            transport = None
        if transport is not None:
            async def _publish() -> None:
                try:
                    if kind == "borrow_request":
                        await publish_lend_request(
                            transport,
                            borrower_did=payload.get("borrower_did", ""),
                            lender_did=friend_did,
                            request_body=payload,
                        )
                    elif kind in ("lend_response", "lend_ack"):
                        await publish_lend_ack(
                            transport,
                            lender_did=payload.get("lender_did", "") or "",
                            borrower_did=friend_did,
                            request_id=payload.get("lend_request_id", ""),
                            decision=str(payload.get("status", "approved")),
                            reason=payload.get("reason"),
                        )
                except Exception:
                    pass
            try:
                loop = _aio.get_running_loop()
                loop.create_task(_publish())
            except RuntimeError:
                try:
                    _aio.run(_publish())
                except Exception:
                    pass
    except Exception:  # noqa: BLE001
        pass
    # 2. Waku fallback (only fires if push module is loaded).
    if _push_notify_friend_sync is None:
        return
    try:
        _push_notify_friend_sync(friend_did, kind, payload)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        pass

# ── 类型 ────────────────────────────────────────────────────────────────────

PermissionDecision = Literal["allowed", "denied", "per-request"]
BorrowMode = Literal["strong-tie-auto", "per-request", "emergency-only"]


# ── 异常 ────────────────────────────────────────────────────────────────────


class BorrowError(Exception):
    """borrow 通用异常."""


class PermissionDeniedError(BorrowError):
    """dev-C permissions / anti_abuse 拦截."""


class LendRequestTimeoutError(BorrowError):
    """per-request 模式等 Bob approve 超时."""


class LendRequestDeniedError(BorrowError):
    """Bob 拒绝."""


class ProxyError(BorrowError):
    """加密 proxy / LLM 调用失败."""


# ── 数据 ────────────────────────────────────────────────────────────────────


@dataclass
class ProxyResult:
    """加密 proxy 调用结果 (mock 或 dev-B 真路径)."""

    text: str
    tokens_used: int
    model_used: str
    method: Literal["dev-b-encrypted-proxy", "stub-passthrough", "injected-mock"] = (
        "stub-passthrough"
    )
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class BorrowSession:
    """一次 borrow 完整状态."""

    session_id: str
    borrower_did: str
    lender_did: str
    resource_type: str
    amount: int
    model: str
    mode: BorrowMode
    status: Literal[
        "starting",
        "permission-denied",
        "awaiting-lender",
        "lender-denied",
        "lender-timeout",
        "proxying",
        "proxy-failed",
        "completed",
    ]
    lend_request_id: Optional[str] = None
    proxy_method: Optional[str] = None
    proxy_text: Optional[str] = None
    tokens_used: int = 0
    ledger_entry_id: Optional[str] = None
    error: Optional[str] = None
    started_at: int = 0
    completed_at: Optional[int] = None
    note: Optional[str] = None
    # incentive layer (2026-06-06)
    incentive_mode: str = "gift"  # gift | kudos | micropay
    kudos_cost: float = 0.0  # only set when incentive_mode == "kudos"
    kudos_balance_after: Optional[float] = None
    usdt_cost: float = 0.0  # only set when incentive_mode == "micropay"
    usdt_payout_address: str = ""  # only set when incentive_mode == "micropay"
    incentive_receipt: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.session_id:
            self.session_id = "bs_" + uuid.uuid4().hex[:12]
        if not self.started_at:
            self.started_at = int(time.time())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── permission check (try dev-C 优雅 fallback) ───────────────────────────────


def _check_permission(
    borrower_did: str,
    lender_did: str,
    resource_type: str,
    amount: int,
    model: str,
    *,
    perms_dir: Optional[Path] = None,
) -> tuple[PermissionDecision, BorrowMode, str]:
    """调 dev-C permissions.check_permission(). 不可用时 fallback.

    波 7 dev-A bug-2 修复: 加 perms_dir 透传, 让 multi-vault test/生产 双场景可直接指定
    `~/<vault>/friends/` 目录, 不再硬走 `~/.sisoul/friends/`. qa-E/qa-C 之前必须用
    `force_mode='strong-tie-auto'` 绕过, 修复后可直接走真 permission check.

    返 (decision, mode, reason).
    fallback: ("allowed", "per-request", "permissions-module-unavailable, fallback per-request")
    """
    try:
        # dev-C 接口约定 (波 5 dev-C 任务卡): check_permission(borrower, lender, ...) → dict
        from sisoul.friend.permissions import check_permission  # type: ignore[attr-defined]
    except Exception as e:
        return (
            "per-request",
            "per-request",
            f"permissions module unavailable ({type(e).__name__}): fallback per-request",
        )

    # dev-C 接口: check_permission(friend_did, resource_type, amount, model, ...) -> tuple[bool, str]
    # 老接口(假设): check_permission(borrower_did=, lender_did=, ...) -> dict
    # 兼容两种签名.
    try:
        # 优先 dev-C 实际签名 (positional + perms_dir kwarg)
        try:
            if perms_dir is not None:
                result = check_permission(
                    lender_did, resource_type, amount, model, perms_dir=perms_dir
                )
            else:
                result = check_permission(lender_did, resource_type, amount, model)
        except TypeError:
            # 旧 kwargs 接口
            kwargs: dict[str, Any] = dict(
                borrower_did=borrower_did,
                lender_did=lender_did,
                resource_type=resource_type,
                amount=amount,
                model=model,
            )
            if perms_dir is not None:
                kwargs["perms_dir"] = perms_dir
            result = check_permission(**kwargs)
    except Exception as e:
        return (
            "per-request",
            "per-request",
            f"check_permission raised ({type(e).__name__}: {e}): fallback per-request",
        )

    # 兼容多种返回结构:
    # 1. tuple[bool, str] (dev-C)
    # 2. dict {"decision": ..., "mode": ...}
    # 3. dataclass with .decision / .mode
    if isinstance(result, tuple) and len(result) >= 2:
        allowed_bool, reason_str = bool(result[0]), str(result[1])
        decision_raw = allowed_bool
        mode_raw = "per-request"  # dev-C 不返 mode, 由 cli/caller 显式选
        reason = reason_str
    elif isinstance(result, dict):
        decision_raw = (
            result.get("decision")
            or result.get("status")
            or result.get("allowed")
            or "per-request"
        )
        mode_raw = result.get("mode") or "per-request"
        reason = result.get("reason") or ""
    else:
        decision_raw = getattr(result, "decision", "per-request")
        mode_raw = getattr(result, "mode", "per-request")
        reason = getattr(result, "reason", "")

    # 归一
    if decision_raw is True:
        decision: PermissionDecision = "allowed"
    elif decision_raw is False:
        decision = "denied"
    elif str(decision_raw) in ("allowed", "denied", "per-request"):
        decision = str(decision_raw)  # type: ignore[assignment]
    else:
        decision = "per-request"

    if str(mode_raw) in ("strong-tie-auto", "per-request", "emergency-only"):
        mode: BorrowMode = str(mode_raw)  # type: ignore[assignment]
    else:
        mode = "per-request"

    return decision, mode, str(reason)


# ── proxy 调用 (try dev-B 优雅 fallback) ─────────────────────────────────────


# 测试可注入 mock proxy: 设置 borrow.set_mock_proxy(fn)
_INJECTED_MOCK: Optional[Callable[..., ProxyResult]] = None


def set_mock_proxy(fn: Optional[Callable[..., ProxyResult]]) -> None:
    """integration test 注入 mock LLM 响应 (dev-B encrypted_proxy 不可达时)."""
    global _INJECTED_MOCK
    _INJECTED_MOCK = fn


def _proxy_call(
    borrower_did: str,
    lender_did: str,
    resource_type: str,
    amount: int,
    model: str,
    prompt: str,
) -> ProxyResult:
    # 1. 优先 injected mock (test only)
    if _INJECTED_MOCK is not None:
        try:
            return _INJECTED_MOCK(
                borrower_did=borrower_did,
                lender_did=lender_did,
                resource_type=resource_type,
                amount=amount,
                model=model,
                prompt=prompt,
            )
        except Exception as e:
            raise ProxyError(f"injected mock raised: {type(e).__name__}: {e}") from e

    # 2. 尝试 dev-B encrypted_proxy
    try:
        from sisoul.friend.encrypted_proxy import (  # type: ignore[attr-defined]
            proxy_chat_request,
        )
    except Exception:
        return ProxyResult(
            text=(
                f"[stub-passthrough] borrow {amount} {resource_type} from {lender_did} "
                f"via model={model}; prompt 长度={len(prompt)}; "
                f"dev-B encrypted_proxy 未 ship, 用 stub."
            ),
            tokens_used=max(1, amount // 10) if resource_type == "llm_quota" else 1,
            model_used=model,
            method="stub-passthrough",
        )

    try:
        r = proxy_chat_request(
            borrower_did=borrower_did,
            lender_did=lender_did,
            model=model,
            prompt=prompt,
            amount=amount,
        )
    except Exception as e:
        raise ProxyError(
            f"dev-B encrypted_proxy raised: {type(e).__name__}: {e}"
        ) from e

    # 兼容 dict / dataclass 返回
    if isinstance(r, dict):
        return ProxyResult(
            text=str(r.get("text", "")),
            tokens_used=int(r.get("tokens_used", 0)),
            model_used=str(r.get("model_used", model)),
            method="dev-b-encrypted-proxy",
            extra={k: v for k, v in r.items() if k not in ("text", "tokens_used", "model_used")},
        )
    return ProxyResult(
        text=str(getattr(r, "text", "")),
        tokens_used=int(getattr(r, "tokens_used", 0)),
        model_used=str(getattr(r, "model_used", model)),
        method="dev-b-encrypted-proxy",
    )


# ── lender 端互动 (同机模拟 / 跨机 daemon 调) ────────────────────────────────


def _wait_for_lender_decision(
    store: LendStore,
    request_id: str,
    timeout_sec: float,
    poll_interval: float = 0.1,
) -> LendRequest:
    """轮询 LendStore 直到 status != pending. 超时返最后状态."""
    deadline = time.monotonic() + timeout_sec
    last: LendRequest = store.get(request_id)
    while time.monotonic() < deadline:
        last = store.get(request_id)
        if last.status != "pending":
            return last
        if last.is_expired():
            return last
        time.sleep(poll_interval)
    # 最后再 check 一次
    return store.get(request_id)


# ── 主入口 ──────────────────────────────────────────────────────────────────


def _evaluate_incentive(
    lender_did: str,
    amount: int,
    perms_dir: Optional[Path],
) -> tuple[str, float, float, str, dict]:
    """Look up the lender's perm, return the incentive shape.

    Returns (mode, kudos_cost, usdt_cost, usdt_address, details).
    mode is one of "gift" / "kudos" / "micropay".

    If perm load fails (file missing, etc), defaults to "gift" with 0 cost —
    callers can still see the BorrowSession.note explaining this.
    """
    try:
        from sisoul.friend.permissions import load_permissions, PermissionNotFoundError
        from sisoul.friend.kudos import compute_kudos_required, compute_usdt_required
    except Exception:
        return ("gift", 0.0, 0.0, "", {"reason": "permissions/kudos imports unavailable"})
    try:
        perm = load_permissions(lender_did, perms_dir=perms_dir)
    except Exception as e:
        return ("gift", 0.0, 0.0, "", {"reason": f"perm not found: {type(e).__name__}"})
    q = perm.llm_quota_share
    mode = getattr(q, "incentive_mode", "gift")
    kudos = compute_kudos_required(q, amount)
    usdt = compute_usdt_required(q, amount)
    payout = getattr(q, "usdt_payout_address", "") or ""
    return (mode, kudos, usdt, payout, {})


def _enforce_kudos(
    lender_did: str, kudos_cost: float, dry_run: bool, kudos_store_path: Optional[Path] = None,
) -> tuple[Optional[float], Optional[str]]:
    """Try to spend kudos_cost from this user's ledger to lender_did.

    Returns (new_balance, error_msg). In dry_run mode, only checks affordability.
    """
    if kudos_cost <= 0:
        return (None, None)
    try:
        from sisoul.friend.kudos import KudosStore, KudosInsufficient
    except Exception as e:
        return (None, f"kudos module unavailable: {e}")
    ks = KudosStore(kudos_store_path)
    try:
        current = ks.balance(lender_did)
        new = current - kudos_cost
        if new < -1000.0:
            return (current, (
                f"kudos insufficient: would push balance to {new:.1f} "
                f"(floor -1000); earn kudos by lending first"
            ))
        if dry_run:
            return (current, None)
        new = ks.spend(lender_did, kudos_cost, f"borrow {kudos_cost:.2f} kudos")
        return (new, None)
    finally:
        ks.close()


def borrow_resource(
    borrower_did: str,
    lender_did: str,
    resource_type: str,
    amount: int,
    model: str,
    *,
    prompt: str = "",
    force_mode: Optional[BorrowMode] = None,
    emergency_flag: bool = False,
    per_request_timeout_sec: float = 30.0,
    lend_db: Path | str | None = None,
    pending_file: Path | str | None = None,
    ledger_db: Path | str | None = None,
    enqueue_onchain: bool = True,
    perms_dir: Optional[Path] = None,
    dry_run: bool = False,
    kudos_store_path: Optional[Path] = None,
) -> BorrowSession:
    """完整 borrow 流程.

    `force_mode`: 跳过 permissions check 强制走某模式 (用于 test / strong-tie 快路径).
    `prompt`: 给 LLM 的 prompt 文本 (proxy stub 也会处理).
    `perms_dir`: 波 7 dev-A bug-2 修复 — 显式指定 permissions yaml 目录 (multi-vault
        test/生产场景), None 走 dev-C 默认 `~/.sisoul/friends/`.
    """
    session = BorrowSession(
        session_id="",
        borrower_did=borrower_did,
        lender_did=lender_did,
        resource_type=resource_type,
        amount=int(amount),
        model=model,
        mode=force_mode or "per-request",
        status="starting",
    )

    # 1. permission check (skipped in dry_run so the borrower can see a quote
    # even if the lender's policy requires per-request approval)
    if force_mode is None and not dry_run:
        decision, mode, reason = _check_permission(
            borrower_did, lender_did, resource_type, amount, model,
            perms_dir=perms_dir,
        )
        session.mode = mode
        if decision == "denied":
            session.status = "permission-denied"
            session.error = reason or "permission denied (dev-C policy)"
            session.completed_at = int(time.time())
            return session
        # decision allowed / per-request → 继续 (per-request 仍走 LendStore 流程)
    else:
        session.mode = force_mode or "per-request"

    # 1b. incentive evaluation (kudos / micropay) — added 2026-06-06
    inc_mode, kudos_cost, usdt_cost, payout_addr, details = _evaluate_incentive(
        lender_did, amount, perms_dir
    )
    session.incentive_mode = inc_mode
    session.kudos_cost = kudos_cost
    session.usdt_cost = usdt_cost
    session.usdt_payout_address = payout_addr
    if inc_mode == "kudos":
        new_bal, kerr = _enforce_kudos(lender_did, kudos_cost, dry_run, kudos_store_path)
        session.kudos_balance_after = new_bal
        if kerr:
            session.status = "permission-denied"
            session.error = f"kudos check failed: {kerr}"
            session.completed_at = int(time.time())
            return session
        session.incentive_receipt = {
            "mode": "kudos",
            "kudos_cost": kudos_cost,
            "balance_after": new_bal,
            "lender_did": lender_did,
        }
        if dry_run:
            session.status = "completed"  # kudos dry-run is a quote preview
            session.note = f"dry-run: kudos quote {kudos_cost:.2f} (current balance {new_bal:.2f}; no kudos actually spent)"
            session.completed_at = int(time.time())
            return session
    elif inc_mode == "micropay":
        session.incentive_receipt = {
            "mode": "micropay",
            "usdt_amount": usdt_cost,
            "payout_address": payout_addr,
            "network": "TRC20",
            "tronscan": f"https://tronscan.org/#/address/{payout_addr}",
            "lender_did": lender_did,
            "instruction": (
                f"Pay {usdt_cost:.4f} USDT (TRC20) to {payout_addr} BEFORE the "
                f"lender approves. Send the tx hash to the lender out-of-band; "
                f"automated chain-watching is alpha v1.1 (T+1m)."
            ),
        }
        if dry_run:
            session.status = "permission-denied"  # dry-run shows quote without sending request
            session.note = "dry-run: micropay quote shown, no LendRequest sent"
            session.completed_at = int(time.time())
            return session
    else:
        session.incentive_receipt = {"mode": "gift", "kudos_cost": 0, "usdt_cost": 0}
        if dry_run:
            session.status = "completed"  # gift dry-run is a no-cost preview
            session.note = "dry-run: gift mode, no cost"
            session.completed_at = int(time.time())
            return session

    # 2. 发 LendRequest 给对端 LendStore (同机模拟; 真 P2P 走 dev-B encrypted channel)
    store = LendStore(db_path=lend_db, pending_file=pending_file)
    try:
        req = store.request_lend(
            borrower_did=borrower_did,
            lender_did=lender_did,
            resource_type=resource_type,
            amount=amount,
            model=model,
            mode=session.mode,
            ttl_sec=int(per_request_timeout_sec),
            emergency_flag=emergency_flag,
        )
        session.lend_request_id = req.id

        # Wave B' P1-1: 推 lender 一条 borrow_request notification
        # (Waku store-and-forward; lender 离线则上线 catchup 取走).
        _safe_notify(
            lender_did,
            "borrow_request",
            {
                "lend_request_id": req.id,
                "borrower_did": borrower_did,
                "resource_type": resource_type,
                "amount": int(amount),
                "model": model,
                "mode": session.mode,
                "emergency_flag": emergency_flag,
            },
        )

        # 3. 按模式等结果
        if req.status == "approved":
            # strong-tie-auto / emergency-only(+flag) 直接 approved
            final = req
        elif req.status == "denied":
            session.status = "lender-denied"
            session.error = req.denied_reason or "lender denied (mode=emergency-only no flag)"
            session.completed_at = int(time.time())
            return session
        else:
            # per-request: poll
            final = _wait_for_lender_decision(
                store, req.id, timeout_sec=per_request_timeout_sec
            )

        if final.status == "approved":
            pass  # 进 4
        elif final.status == "denied":
            session.status = "lender-denied"
            session.error = final.denied_reason or "lender denied"
            session.completed_at = int(time.time())
            return session
        elif final.status in ("expired", "pending"):
            # pending 是超时退出仍未决 → 标 expired
            if final.status == "pending":
                try:
                    store.expire_stale()
                    final = store.get(req.id)
                except Exception:
                    pass
            session.status = "lender-timeout"
            session.error = (
                f"per-request 模式 {per_request_timeout_sec}s 内无 lender 响应"
            )
            session.completed_at = int(time.time())
            return session
        else:
            session.status = "lender-denied"
            session.error = f"lender state 异常: {final.status}"
            session.completed_at = int(time.time())
            return session

        # 4. proxy 调用
        session.status = "proxying"
        try:
            pr = _proxy_call(
                borrower_did=borrower_did,
                lender_did=lender_did,
                resource_type=resource_type,
                amount=amount,
                model=model,
                prompt=prompt,
            )
        except ProxyError as e:
            session.status = "proxy-failed"
            session.error = str(e)
            session.completed_at = int(time.time())
            return session

        session.proxy_method = pr.method
        session.proxy_text = pr.text
        # tokens_used: ai_skill 视 1 次 use, llm_quota 用实际 tokens
        if resource_type == "llm_quota":
            session.tokens_used = pr.tokens_used or amount
        else:
            session.tokens_used = 1

        # 5. ledger 写 (双向写: borrower 本机 borrow + 同机模拟 lender 本机 lend mirror).
        # 真跨机时 borrower 写 borrow; 对端 borrow daemon 调时各自写自己视角.
        led = ReciprocityLedger(db_path=ledger_db, self_did=borrower_did)
        try:
            entry = led.record_usage(
                borrower_did=borrower_did,
                lender_did=lender_did,
                resource_type=resource_type,
                amount=session.tokens_used,
                model_or_skill_id=model,
                direction="borrow",
                enqueue_onchain=enqueue_onchain,
                actor_did=borrower_did,
                tool_name="sisoul-friend-borrow",
            )
            session.ledger_entry_id = entry.entry_id
        finally:
            led.close()

        # 6. mark lend completed
        try:
            store.mark_completed(req.id)
        except RequestStateError:
            pass

        session.status = "completed"
        session.completed_at = int(time.time())

        # Wave B' P1-1: 通知 lender 这次 borrow 完成 (ledger 已写, lender 可对账)
        _safe_notify(
            lender_did,
            "lend_response",
            {
                "lend_request_id": req.id,
                "borrower_did": borrower_did,
                "status": "completed",
                "tokens_used": session.tokens_used,
                "model": model,
                "ledger_entry_id": session.ledger_entry_id,
            },
        )

        return session
    finally:
        store.close()


# ── proxy session API (sisoul borrow --proxy-to anthropic 模式) ──────────────


@dataclass
class ProxySession:
    """长寿命 proxy session: Claude Code 等工具走 sisoul daemon 透明转 Bob."""

    session_id: str
    borrower_did: str
    lender_did: str
    model: str
    started_at: int
    endpoint: str  # e.g. http://127.0.0.1:9876/sisoul/borrow-proxy/{sid}
    status: Literal["active", "stopped", "expired"] = "active"
    requests_count: int = 0
    tokens_used_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# 进程内 session registry (daemon 单进程, 不持久化 — 重启失效但简单可靠).
_PROXY_SESSIONS: dict[str, ProxySession] = {}


def start_proxy_session(
    borrower_did: str,
    lender_did: str,
    model: str,
    *,
    base_url: str = "http://127.0.0.1:9876",
) -> ProxySession:
    """起 proxy session, 返 ProxySession (endpoint 给 ANTHROPIC_BASE_URL 用)."""
    sid = "ps_" + uuid.uuid4().hex[:12]
    sess = ProxySession(
        session_id=sid,
        borrower_did=borrower_did,
        lender_did=lender_did,
        model=model,
        started_at=int(time.time()),
        endpoint=f"{base_url}/sisoul/borrow-proxy/{sid}",
    )
    _PROXY_SESSIONS[sid] = sess
    return sess


def get_proxy_session(session_id: str) -> Optional[ProxySession]:
    return _PROXY_SESSIONS.get(session_id)


def list_proxy_sessions() -> list[ProxySession]:
    return list(_PROXY_SESSIONS.values())


def stop_proxy_session(session_id: str) -> Optional[ProxySession]:
    sess = _PROXY_SESSIONS.get(session_id)
    if not sess:
        return None
    sess.status = "stopped"
    return sess


def _reset_proxy_sessions_for_test() -> None:
    """tests only."""
    _PROXY_SESSIONS.clear()


__all__ = [
    # 类型
    "PermissionDecision",
    "BorrowMode",
    # 异常
    "BorrowError",
    "PermissionDeniedError",
    "LendRequestTimeoutError",
    "LendRequestDeniedError",
    "ProxyError",
    # 数据
    "ProxyResult",
    "BorrowSession",
    "ProxySession",
    # 主入口
    "borrow_resource",
    # proxy session
    "start_proxy_session",
    "get_proxy_session",
    "list_proxy_sessions",
    "stop_proxy_session",
    # test helpers
    "set_mock_proxy",
]
