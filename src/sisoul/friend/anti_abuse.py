"""sisoul friend anti_abuse · 5 层滥用防御 (Phase 4 W63-W65, 波 5 dev-C).

§28 §3.7 滥用防御:
  L1 · 月度配额    Bob 设的 monthly_token_cap, Alice 当月借量 > cap 自动 deny
  L2 · rate limit  Bob 设的 rate_limit (N/min), Alice 超过频率 deny (滑动窗口)
  L3 · revoke      Bob 随时撤销 Alice 授权 (写 perm.revoked=True + 链上 REVOKE attestation)
  L4 · 链上 reputation  sisoul 累积公开 reputation 分 (滥用/spam/高度不平衡), 写链上
  L5 · daemon 安全扫描  daemon 收到 request 时自动扫描 (不读 prompt 内容, 但检测 token 量/
                       频率/模式异常), 异常 block + 通知 Bob

reputation 算法 (compute_reputation):
  base = 100
  滥用历史 → −20 per incident
  spam 投诉 → −10 per complaint
  借入/借出不平衡 (ratio > 2:1 或 < 0.5:1) → −15
  长期均衡互惠 (interactions > 10, ratio ∈ [0.66, 1.5]) → +20
  clamped to [0, 200]
  公开 grade: A(>=150) / B(>=100) / C(>=50) / D(<50)

L4 上链复用 dev-B 波 4 EAS 基础设施 (AttestQueue + AuditAttestation), 用
action_type="REPUTATION_PUBLISH" target=did, prompt=json(score) 走现有 schema; 无需新 schema 注册.
同理 L3 revoke 上链 action_type="PERMISSION_REVOKE".

L5 扫描: 不读 prompt 内容 (dev-B 加密 proxy 保密), 只看 metadata (token 量/频率/重复 hash 模式).

模块边界 (波 5 dev-C 严格约束): 见 permissions.py 头注释.

测试: tests/test_friend_anti_abuse.py + tests/test_anti_abuse_integration.py.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Optional

from sisoul.friend.permissions import (
    DEFAULT_PERMISSIONS_DIR,
    FriendPermission,
    PermissionError,
    PermissionNotFoundError,
    count_monthly_usage,
    load_permissions,
    mark_revoked,
)

# ── 常量 ─────────────────────────────────────────────────────────────────────

DEFAULT_SCAN_DB = Path.home() / ".sisoul" / "anti_abuse_scan.db"

# L4 reputation 算法常量
REPUTATION_BASE = 100
ABUSE_PENALTY = 20
SPAM_PENALTY = 10
IMBALANCE_PENALTY = 15
BALANCED_BONUS = 20
REPUTATION_MIN = 0
REPUTATION_MAX = 200

# L5 daemon scan 阈值默认
DEFAULT_SCAN_TOKEN_BURST = 200_000  # 单 request > 200k tokens → 疑似 abuse
DEFAULT_SCAN_RATE_BURST_PER_10S = 20  # 10s 内 > 20 request → 异常频率
DEFAULT_SCAN_REPEAT_HASH_THRESHOLD = 10  # 相同 prompt_hash 重复 > 10 次 → spam


# ── 异常 ─────────────────────────────────────────────────────────────────────


class AntiAbuseError(Exception):
    """anti_abuse 通用异常."""


# ── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class ReputationScore:
    """链上 reputation (公开)."""

    did: str
    score: int  # 0..200
    grade: str  # A/B/C/D
    borrows: int = 0
    lends: int = 0
    abuse_incidents: int = 0
    spam_complaints: int = 0
    balance_ratio: float = 1.0  # borrows / lends (>0)
    computed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScanResult:
    """L5 scan 单次结果."""

    allowed: bool
    reason: str
    details: dict[str, Any] = field(default_factory=dict)
    scanned_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecentRequest:
    """L2 rate limit 用 (滑动窗口元素)."""

    ts: float  # unix epoch sec
    amount: int = 0
    request_id: str = ""


# ── L1 月度配额 ──────────────────────────────────────────────────────────────


def enforce_monthly_cap(
    perm: FriendPermission,
    current_usage: int,
    new_amount: int,
    resource_type: str = "llm_quota",
) -> bool:
    """L1: 月度配额. 返 True=通过, False=超限.

    resource_type=llm_quota 用 perm.llm_quota_share.monthly_token_cap.
    其他 resource 当前无月度 cap → 直接 True (后续 ai_skill/compute 可扩展).
    cap=0 → 不限.
    """
    if resource_type != "llm_quota":
        return True
    cap = perm.llm_quota_share.monthly_token_cap
    if cap <= 0:
        return True
    return (current_usage + new_amount) <= cap


# ── L2 rate limit (滑动窗口) ─────────────────────────────────────────────────


def enforce_rate_limit(
    perm: FriendPermission,
    recent_requests: list[RecentRequest],
    window_sec: int = 60,
    now: Optional[float] = None,
) -> bool:
    """L2: rate limit. 返 True=通过, False=超限.

    rate_limit 单位: N requests / min (滑动窗口 window_sec 内).
    recent_requests 由 daemon 维护 (内存或 SQLite). 这里只判.

    rate_limit=0 → 不限.
    """
    rl = perm.llm_quota_share.rate_limit
    if rl <= 0:
        return True
    now_ts = now if now is not None else time.time()
    threshold = now_ts - window_sec
    # 滑动窗口内 count
    in_window = sum(1 for r in recent_requests if r.ts >= threshold)
    # 新请求加上后是否超限
    return (in_window + 1) <= rl


class RateLimiter:
    """内存 rate limiter (per-friend 滑动窗口). daemon 内常驻."""

    def __init__(self, max_records_per_friend: int = 1000) -> None:
        self._buckets: dict[str, Deque[RecentRequest]] = defaultdict(
            lambda: deque(maxlen=max_records_per_friend)
        )

    def record(self, friend_did: str, amount: int = 0, request_id: str = "") -> None:
        self._buckets[friend_did].append(
            RecentRequest(ts=time.time(), amount=amount, request_id=request_id)
        )

    def recent(self, friend_did: str, window_sec: int = 60) -> list[RecentRequest]:
        if friend_did not in self._buckets:
            return []
        now = time.time()
        threshold = now - window_sec
        return [r for r in self._buckets[friend_did] if r.ts >= threshold]

    def check(
        self,
        perm: FriendPermission,
        friend_did: str,
        window_sec: int = 60,
    ) -> bool:
        return enforce_rate_limit(perm, self.recent(friend_did, window_sec), window_sec)


# ── L3 revoke ────────────────────────────────────────────────────────────────


def revoke_friend_permission(
    friend_did: str,
    reason: str = "",
    perms_dir: Optional[Path] = None,
    *,
    onchain_publisher: Optional[Callable[[str, str], Optional[str]]] = None,
) -> dict[str, Any]:
    """L3: 即时撤销 + 链上 REVOKE attestation.

    步骤:
      1. mark_revoked() → perm.revoked=True (即时生效, check_permission 立即拒)
      2. 调 onchain_publisher (默认调 _publish_revoke_attestation 走 EAS queue)
         返回 attestation queue_id (或 tx_hash) 用于审计.

    onchain_publisher 签名: (friend_did, reason) -> Optional[queue_id].
    省略时用 _publish_revoke_attestation (走 EAS AuditAttestation).

    返 dict: {revoked: True, revoked_at, reason, attestation_queue_id, perm}
    """
    perm = mark_revoked(friend_did, reason=reason, perms_dir=perms_dir)
    publisher = onchain_publisher or _publish_revoke_attestation
    try:
        att_id = publisher(friend_did, reason)
    except Exception as e:
        # fail-open: 即使链上失败, 本地 revoke 仍生效.
        att_id = None
        _log_scan_event(
            event="revoke_attestation_publish_failed",
            details={"friend_did": friend_did, "error": str(e)},
        )

    return {
        "revoked": True,
        "friend_did": friend_did,
        "revoked_at": perm.revoked_at,
        "reason": reason,
        "attestation_queue_id": att_id,
        "perm": perm.to_dict(),
    }


def _publish_revoke_attestation(friend_did: str, reason: str) -> Optional[str]:
    """走 EAS AuditAttestation 发 REVOKE event. 复用 dev-B 波 4 基础设施.

    action_type="PERMISSION_REVOKE", target=friend_did, prompt=reason.
    不强制 attester DID (走 EAS resolve_attester_did 默认).
    """
    try:
        from sisoul.onchain.eas import (
            AttestQueue,
            AuditAttestation,
            load_config,
            resolve_attester_did,
        )
    except ImportError:
        return None

    try:
        cfg = load_config()
        try:
            attester = resolve_attester_did(cfg)
        except Exception:
            attester = "did:sisoul:unknown"
        att = AuditAttestation.from_audit_payload(
            actor_did=attester,
            action_type="PERMISSION_REVOKE",
            target=friend_did,
            prompt=json.dumps({"reason": reason}, ensure_ascii=False),
            tool_name="sisoul-anti-abuse",
        )
        with AttestQueue() as q:
            q.enqueue(att)
        return att.queue_id
    except Exception:
        return None


# ── L4 链上 reputation ───────────────────────────────────────────────────────


def compute_reputation(
    did: str,
    *,
    borrows: int = 0,
    lends: int = 0,
    abuse_incidents: int = 0,
    spam_complaints: int = 0,
    interactions_for_balance_floor: int = 10,
) -> ReputationScore:
    """L4: 算 reputation (本地公式 + 公开上链).

    base 100; 滥用 −20/次; spam −10/次; 不平衡 (>2:1 或 <0.5:1) −15;
    长期均衡 (interactions > 10, ratio ∈ [0.66, 1.5]) +20.

    clamp 到 [0, 200]. grade: A>=150 / B>=100 / C>=50 / D<50.
    """
    score = REPUTATION_BASE
    score -= abuse_incidents * ABUSE_PENALTY
    score -= spam_complaints * SPAM_PENALTY

    total = borrows + lends
    if lends > 0:
        ratio = borrows / lends
    else:
        # 没借出: 视为不平衡 (除非 borrows 也 0)
        ratio = float("inf") if borrows > 0 else 1.0

    if total >= interactions_for_balance_floor:
        if 0.66 <= ratio <= 1.5:
            score += BALANCED_BONUS
        elif ratio > 2.0 or ratio < 0.5:
            score -= IMBALANCE_PENALTY

    score = max(REPUTATION_MIN, min(REPUTATION_MAX, score))

    if score >= 150:
        grade = "A"
    elif score >= 100:
        grade = "B"
    elif score >= 50:
        grade = "C"
    else:
        grade = "D"

    # ratio inf 不能序列化 — 用大数代替
    safe_ratio = ratio if ratio != float("inf") else 999.0

    return ReputationScore(
        did=did,
        score=score,
        grade=grade,
        borrows=borrows,
        lends=lends,
        abuse_incidents=abuse_incidents,
        spam_complaints=spam_complaints,
        balance_ratio=round(safe_ratio, 3),
        computed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def publish_reputation_attestation(
    rep: ReputationScore,
    *,
    onchain_publisher: Optional[Callable[[ReputationScore], Optional[str]]] = None,
) -> Optional[str]:
    """L4: 公开 reputation 写链上 (REPUTATION_PUBLISH attestation).

    走 EAS AuditAttestation 复用 schema (action_type="REPUTATION_PUBLISH",
    target=did, prompt=json(score/grade/...)).
    """
    if onchain_publisher is not None:
        try:
            return onchain_publisher(rep)
        except Exception:
            return None

    try:
        from sisoul.onchain.eas import (
            AttestQueue,
            AuditAttestation,
            load_config,
            resolve_attester_did,
        )
    except ImportError:
        return None

    try:
        cfg = load_config()
        try:
            attester = resolve_attester_did(cfg)
        except Exception:
            attester = "did:sisoul:unknown"
        att = AuditAttestation.from_audit_payload(
            actor_did=attester,
            action_type="REPUTATION_PUBLISH",
            target=rep.did,
            prompt=json.dumps(rep.to_dict(), ensure_ascii=False),
            tool_name="sisoul-anti-abuse",
        )
        with AttestQueue() as q:
            q.enqueue(att)
        return att.queue_id
    except Exception:
        return None


# ── L5 daemon 安全扫描 ──────────────────────────────────────────────────────


@dataclass
class ScanThresholds:
    token_burst: int = DEFAULT_SCAN_TOKEN_BURST
    rate_burst_per_10s: int = DEFAULT_SCAN_RATE_BURST_PER_10S
    repeat_hash_threshold: int = DEFAULT_SCAN_REPEAT_HASH_THRESHOLD


def scan_request_pattern(
    request_metadata: dict[str, Any],
    *,
    recent_history: Optional[list[dict[str, Any]]] = None,
    thresholds: Optional[ScanThresholds] = None,
    persist_db: Optional[Path] = None,
) -> tuple[bool, str]:
    """L5: 扫 request metadata. 返 (allowed, reason).

    metadata 字段 (dev-B 加密 proxy 透传):
      - friend_did   : str (借方)
      - amount       : int (tokens, 不是 prompt 长度)
      - prompt_hash  : str (sha256, 不暴露 prompt 内容)
      - model        : str
      - ts           : float (epoch, 省略=now)

    recent_history: list of past metadata (按 ts asc). 省略时 scan 只用单条规则.

    扫描规则:
      A. amount > token_burst → block
      B. recent_history 中 10s 内 from same friend_did > rate_burst_per_10s → block
      C. 同 prompt_hash 重复次数 > repeat_hash_threshold → block (疑似 spam)
      D. metadata 缺 friend_did/amount → block (input 校验)

    不读 prompt 内容. metadata 不入参 prompt 文本字段 (dev-B 保证).
    扫描事件 (含 block) 落 SQLite (DEFAULT_SCAN_DB) 供 perms scan-log 查.
    """
    th = thresholds or ScanThresholds()
    now = float(request_metadata.get("ts") or time.time())
    friend_did = request_metadata.get("friend_did")
    amount = request_metadata.get("amount")
    prompt_hash = request_metadata.get("prompt_hash") or ""

    # D · input 校验
    if not friend_did:
        result = (False, "scan:missing_friend_did")
        _persist_scan_event(request_metadata, result, persist_db)
        return result
    if amount is None or not isinstance(amount, int) or amount < 0:
        result = (False, "scan:invalid_amount")
        _persist_scan_event(request_metadata, result, persist_db)
        return result

    # A · token burst
    if amount > th.token_burst:
        result = (False, f"scan:token_burst:{amount} > {th.token_burst}")
        _persist_scan_event(request_metadata, result, persist_db)
        return result

    # B · 10s rate burst
    if recent_history:
        threshold_ts = now - 10.0
        in_10s = sum(
            1
            for h in recent_history
            if h.get("friend_did") == friend_did
            and float(h.get("ts") or 0) >= threshold_ts
        )
        if in_10s + 1 > th.rate_burst_per_10s:
            result = (
                False,
                f"scan:rate_burst_10s:{in_10s + 1} > {th.rate_burst_per_10s}",
            )
            _persist_scan_event(request_metadata, result, persist_db)
            return result

    # C · 同 prompt_hash 重复
    if recent_history and prompt_hash:
        same = sum(
            1
            for h in recent_history
            if h.get("prompt_hash") == prompt_hash
            and h.get("friend_did") == friend_did
        )
        if same + 1 > th.repeat_hash_threshold:
            result = (
                False,
                f"scan:repeat_hash:{same + 1} > {th.repeat_hash_threshold}",
            )
            _persist_scan_event(request_metadata, result, persist_db)
            return result

    # 通过
    result = (True, "scan:ok")
    # 通过事件不入 scan log (减噪), 但保留 hook 供 caller 自决.
    return result


# ── 持久化 (scan log) ────────────────────────────────────────────────────────


_SCAN_SQL = """
CREATE TABLE IF NOT EXISTS scan_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    friend_did TEXT NOT NULL,
    allowed INTEGER NOT NULL,
    reason TEXT NOT NULL,
    amount INTEGER,
    model TEXT,
    prompt_hash TEXT,
    ts TEXT NOT NULL,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_scan_events_friend ON scan_events(friend_did);
CREATE INDEX IF NOT EXISTS idx_scan_events_ts ON scan_events(ts);
"""


def _scan_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_SCAN_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCAN_SQL)
    conn.commit()
    return conn


def _persist_scan_event(
    metadata: dict[str, Any],
    result: tuple[bool, str],
    db_path: Optional[Path] = None,
) -> None:
    try:
        conn = _scan_conn(db_path)
        try:
            conn.execute(
                "INSERT INTO scan_events (friend_did, allowed, reason, amount, model, "
                "prompt_hash, ts, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(metadata.get("friend_did") or ""),
                    1 if result[0] else 0,
                    result[1],
                    metadata.get("amount") if isinstance(metadata.get("amount"), int) else None,
                    metadata.get("model"),
                    metadata.get("prompt_hash"),
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    json.dumps(
                        {k: v for k, v in metadata.items() if k not in ("prompt",)},
                        ensure_ascii=False,
                        default=str,
                    ),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # 不让 scan log 失败阻断业务路径.
        pass


def _log_scan_event(event: str, details: dict[str, Any]) -> None:
    """通用事件 log (非 request scan, 例 revoke attestation 失败).

    写到 scan_events, friend_did=details.friend_did or "(system)".
    """
    metadata = {
        "friend_did": details.get("friend_did", "(system)"),
        "amount": None,
        "model": None,
        "prompt_hash": None,
        "event": event,
        **details,
    }
    _persist_scan_event(metadata, (False, f"system:{event}"))


def list_scan_log(
    limit: int = 50,
    friend_did: Optional[str] = None,
    only_blocked: bool = True,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """列最近 N 条 scan event (默认只列被 block 的)."""
    conn = _scan_conn(db_path)
    try:
        q = "SELECT * FROM scan_events WHERE 1=1"
        args: list[Any] = []
        if friend_did:
            q += " AND friend_did = ?"
            args.append(friend_did)
        if only_blocked:
            q += " AND allowed = 0"
        q += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        rows = conn.execute(q, args).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            details_raw = r["details"] or "{}"
            try:
                details = json.loads(details_raw)
            except json.JSONDecodeError:
                details = {"raw": details_raw}
            out.append(
                {
                    "id": r["id"],
                    "friend_did": r["friend_did"],
                    "allowed": bool(r["allowed"]),
                    "reason": r["reason"],
                    "amount": r["amount"],
                    "model": r["model"],
                    "prompt_hash": r["prompt_hash"],
                    "ts": r["ts"],
                    "details": details,
                }
            )
        return out
    finally:
        conn.close()


def clear_scan_log(db_path: Optional[Path] = None) -> int:
    """清 scan log. 返删除条数. 测试 / 维护用."""
    conn = _scan_conn(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM scan_events").fetchone()[0]
        conn.execute("DELETE FROM scan_events")
        conn.commit()
        return int(n)
    finally:
        conn.close()


# ── 综合 enforce (集成入口, dev-D borrow 调) ─────────────────────────────────


def enforce_all_layers(
    friend_did: str,
    request_metadata: dict[str, Any],
    *,
    perm: Optional[FriendPermission] = None,
    perms_dir: Optional[Path] = None,
    rate_limiter: Optional[RateLimiter] = None,
    recent_scan_history: Optional[list[dict[str, Any]]] = None,
    current_usage: Optional[int] = None,
    scan_db: Optional[Path] = None,
) -> tuple[bool, str, dict[str, Any]]:
    """5 层综合 enforce. dev-D borrow 路径调.

    顺序: L3 revoke → L1 cap → L2 rate → L5 scan.
    L4 reputation 不在每次 request 强阻断 (仅写公开 score), 故综合 enforce 不调 L4.

    返 (allowed, reason, breakdown).
    """
    breakdown: dict[str, Any] = {
        "L1_cap": None,
        "L2_rate": None,
        "L3_revoke": None,
        "L5_scan": None,
    }
    amount = int(request_metadata.get("amount") or 0)
    resource_type = request_metadata.get("resource_type") or "llm_quota"

    # load perm
    if perm is None:
        try:
            perm = load_permissions(friend_did, perms_dir)
        except PermissionNotFoundError:
            return False, "no_permission_config", breakdown

    # L3
    if perm.revoked:
        breakdown["L3_revoke"] = "revoked"
        return False, f"L3_revoke:{perm.revoked_reason or 'no_reason'}", breakdown

    # L1
    usage = (
        current_usage
        if current_usage is not None
        else count_monthly_usage(friend_did, resource_type)
    )
    if not enforce_monthly_cap(perm, usage, amount, resource_type):
        breakdown["L1_cap"] = f"exceeded:{usage}+{amount}"
        return False, "L1_monthly_cap_exceeded", breakdown
    breakdown["L1_cap"] = f"ok:{usage}+{amount}"

    # L2
    if rate_limiter is not None:
        if not rate_limiter.check(perm, friend_did):
            breakdown["L2_rate"] = "exceeded"
            return False, "L2_rate_limit_exceeded", breakdown
        breakdown["L2_rate"] = "ok"

    # L5
    allowed, reason = scan_request_pattern(
        request_metadata,
        recent_history=recent_scan_history,
        persist_db=scan_db,
    )
    breakdown["L5_scan"] = reason
    if not allowed:
        return False, f"L5_{reason}", breakdown

    # 通过 → 记 rate limiter
    if rate_limiter is not None:
        rate_limiter.record(
            friend_did,
            amount=amount,
            request_id=str(request_metadata.get("request_id") or ""),
        )

    return True, "approved", breakdown


__all__ = [
    # 类型
    "ReputationScore",
    "ScanResult",
    "RecentRequest",
    "ScanThresholds",
    "RateLimiter",
    "AntiAbuseError",
    # 常量
    "DEFAULT_SCAN_DB",
    "REPUTATION_BASE",
    "ABUSE_PENALTY",
    "SPAM_PENALTY",
    "IMBALANCE_PENALTY",
    "BALANCED_BONUS",
    # L1-L5
    "enforce_monthly_cap",
    "enforce_rate_limit",
    "revoke_friend_permission",
    "compute_reputation",
    "publish_reputation_attestation",
    "scan_request_pattern",
    # 集成 + 维护
    "enforce_all_layers",
    "list_scan_log",
    "clear_scan_log",
]
