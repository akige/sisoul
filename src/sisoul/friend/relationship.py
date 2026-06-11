"""sisoul 朋友关系层 (Phase 4 W51-W53 · 波 5 dev-A).

§28 §3.1 朋友关系层:
    Alice 通过 PWA / CLI 输入 Bob 的 DID
        ↓ FRIEND_REQUEST attestation (走波 4 P2P 传输 / 直 EAS queue)
    Bob 接受
        ↓ 双向 EAS attestation 上链 (Alice attest Bob, Bob attest Alice)
        ↓ 双向 mutual = 朋友关系链上成立

强连接评分 (§28 §3.1):
- 双向 mutual (基础): 1 分
- 朋友时长 (每月 +0.5 分, 上限 6 分)
- 互动次数 (每 10 次 +0.5 分, 上限 5 分)
- 总分 < 5 = 弱连接, ≥ 5 = 强连接

设计要点:
- DID: 复用波 3 dev-B sisoul.identity.did.{DID, validate_handle, resolve_did}
- EAS attestation: 复用波 4 dev-B sisoul.onchain.eas.{AttestQueue, AttestConfig, upload_batch}
- 新 schema FRIEND_RELATIONSHIP 跟 audit schema 并列, schema_uid 独立
- 本地 SQLite ~/.sisoul/friends.db 是 cache, 链上 attestation 是真相源
- P2P 传输: 复用波 4 dev-A sisoul.p2p (本 wave 接 stub 路径, 不强依赖 P2P 真启动)

模块边界 (波 5 严格约束):
- 不动 sisoul.{vault,llm,sync,identity,onchain,p2p} 主
- 不动 friend/{encrypted_proxy,permissions,anti_abuse,lend,borrow,ledger}.py (其他 dev)
- __init__.py 只 export 本文件类型 (其他 dev 各自 import 自己模块)

测试见 tests/test_friend_relationship.py + tests/test_friend_eas_schema.py
       + tests/test_friend_two_did_integration.py.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

# ── 公开常量 ─────────────────────────────────────────────────────────────────

# 默认 friend SQLite cache 路径 (import 时快照, 仅向后兼容; 运行时取 _default_friend_db()).
DEFAULT_FRIEND_DB = Path.home() / ".sisoul" / "friends.db"


def _default_friend_db() -> Path:
    """运行时解析默认 friends.db — 尊重 SISOUL_VAULT env (daemon/test 隔离 vault 必须)."""
    import os as _os

    vault = _os.environ.get("SISOUL_VAULT")
    if vault:
        return Path(vault).expanduser() / "friends.db"
    return DEFAULT_FRIEND_DB

# FRIEND_RELATIONSHIP EAS schema (跟 SISOUL_AUDIT_SCHEMA 并列, 独立 schema_uid).
# 真上链 schema_uid 由 SchemaRegistry.register() 链上返; mock 用 sha256 占位.
FRIEND_RELATIONSHIP_SCHEMA = (
    "string requester_did,"
    "string target_did,"
    "string relationship_type,"  # request / accept / revoke
    "uint64 timestamp,"
    "string message"
)

FRIEND_RELATIONSHIP_SCHEMA_UID = "0x" + hashlib.sha256(
    f"sisoul-friend-relationship-v1::{FRIEND_RELATIONSHIP_SCHEMA}".encode("utf-8")
).hexdigest()

# 强连接评分常量 (§28 §3.1, 总分 0-15 上限).
STRONG_TIE_BASE_SCORE = 1.0  # mutual 基础
STRONG_TIE_PER_MONTH = 0.5
STRONG_TIE_MONTH_CAP = 6.0
STRONG_TIE_PER_10_INTERACTIONS = 0.5
STRONG_TIE_INTERACTION_CAP = 5.0
STRONG_TIE_THRESHOLD = 5.0  # ≥5 强连接
STRONG_TIE_MAX = (
    STRONG_TIE_BASE_SCORE + STRONG_TIE_MONTH_CAP + STRONG_TIE_INTERACTION_CAP
)  # 12, manual override 可到 15 (§28 §3.1 上限 15)
STRONG_TIE_MANUAL_OVERRIDE_MAX = 15.0

FriendStatus = Literal["pending", "active", "revoked"]
RelationshipType = Literal["request", "accept", "revoke"]


# ── 异常 ─────────────────────────────────────────────────────────────────────


class FriendError(Exception):
    """朋友关系通用异常."""


class FriendNotFoundError(FriendError):
    """本地 / 链上找不到指定 friend."""


class FriendRequestError(FriendError):
    """friend request 处理错误."""


class FriendRequestNotFoundError(FriendError):
    """accept 时 request_id 找不到."""


# ── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class Friend:
    """单个朋友记录 (本地 cache).

    字段:
    - did:               did:sisoul:<handle> (或 <handle>.sisoul.eth)
    - handle:            派生自 did (alice / bob / ...)
    - status:            pending / active / revoked
    - strong_tie_score:  最近一次算的分数 (0-15)
    - manual_score_override: 用户手动覆盖 (None = 用算的)
    - created_at:        本地建关系时间 (ISO)
    - became_active_at:  双向 accept 完成时间 (ISO; pending 时为 None)
    - last_interaction:  最近一次互动时间 (ISO; None = 从未)
    - interaction_count: 累计互动次数 (借/借出/共同 collab)
    - request_attestation_uid: 对方/本端发的 FRIEND_REQUEST attestation UID
    - accept_attestation_uid:  本端发的 FRIEND_ACCEPT attestation UID
    - mutual_attestation_uid:  对方发的 FRIEND_ACCEPT attestation UID (verify mutual)
    - revoke_attestation_uid:  revoke 之后的 attestation UID
    - notes:             用户自由备注
    """

    did: str
    handle: str = ""
    status: FriendStatus = "pending"
    strong_tie_score: float = 0.0
    manual_score_override: Optional[float] = None
    created_at: str = ""
    became_active_at: Optional[str] = None
    last_interaction: Optional[str] = None
    interaction_count: int = 0
    request_attestation_uid: Optional[str] = None
    accept_attestation_uid: Optional[str] = None
    mutual_attestation_uid: Optional[str] = None
    revoke_attestation_uid: Optional[str] = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not self.handle:
            self.handle = _did_to_handle(self.did)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Friend:
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})

    @property
    def is_mutual(self) -> bool:
        """链上双向 attestation 都齐 = mutual."""
        return bool(self.accept_attestation_uid and self.mutual_attestation_uid)


@dataclass
class FriendRequest:
    """单条 pending friend request (Alice → Bob 入站 / 出站均用此).

    direction:
    - outbound: 我发给 target_did 的, 等对方 accept
    - inbound:  对方 (requester_did) 发给我, 等我 accept
    """

    request_id: str  # uuid4
    requester_did: str
    target_did: str
    direction: Literal["inbound", "outbound"]
    message: str = ""
    created_at: str = ""
    attestation_uid: Optional[str] = None  # FRIEND_REQUEST attestation 上链后写回
    status: Literal["pending", "accepted", "rejected", "revoked"] = "pending"

    def __post_init__(self) -> None:
        if not self.request_id:
            self.request_id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FriendRequest:
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class StrongTieScore:
    """强连接评分细分 (输出 + 调试用)."""

    total: float
    is_strong: bool
    base: float
    months_score: float
    interactions_score: float
    months_elapsed: float
    interaction_count: int
    manual_override: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── handle 提取 ──────────────────────────────────────────────────────────────


def _did_to_handle(did: str) -> str:
    """从 ``did:sisoul:<handle>`` 或 ``<handle>.sisoul.eth`` 抽 handle.

    复用波 3 dev-B identity/did.py 字符集 (不强校验, 容错)."""
    if did.startswith("did:sisoul:"):
        return did[len("did:sisoul:"):]
    if did.endswith(".sisoul.eth"):
        return did[: -len(".sisoul.eth")]
    return did  # 已是 handle 形式


def _normalize_did(did_or_handle: str) -> str:
    """归一到 ``did:sisoul:<handle>`` 形式.

    接受:
    - did:sisoul:alice  → did:sisoul:alice
    - alice.sisoul.eth  → did:sisoul:alice
    - alice             → did:sisoul:alice (光 handle)
    """
    s = did_or_handle.strip()
    # 修复 2026-06-10: 历史 bug 会把完整 did:key:… 再包一层 did:sisoul: →
    # "did:sisoul:did:key:z6LS…" (PWA 拿这个去 borrow 必失败). 先解残留双前缀.
    while s.startswith("did:sisoul:did:"):
        s = s[len("did:sisoul:"):]
    if s.startswith("did:"):
        return s  # 已是完整 DID (did:key / did:sisoul / did:web …), 不再包
    if s.endswith(".sisoul.eth"):
        return f"did:sisoul:{s[: -len('.sisoul.eth')]}"
    return f"did:sisoul:{s}"


# ── SQLite cache ─────────────────────────────────────────────────────────────


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS friends (
    did TEXT PRIMARY KEY,
    handle TEXT NOT NULL,
    status TEXT NOT NULL,
    strong_tie_score REAL NOT NULL DEFAULT 0.0,
    manual_score_override REAL,
    created_at TEXT NOT NULL,
    became_active_at TEXT,
    last_interaction TEXT,
    interaction_count INTEGER NOT NULL DEFAULT 0,
    request_attestation_uid TEXT,
    accept_attestation_uid TEXT,
    mutual_attestation_uid TEXT,
    revoke_attestation_uid TEXT,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_friends_status ON friends(status);

CREATE TABLE IF NOT EXISTS friend_requests (
    request_id TEXT PRIMARY KEY,
    requester_did TEXT NOT NULL,
    target_did TEXT NOT NULL,
    direction TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    attestation_uid TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS idx_friend_requests_status ON friend_requests(status);
CREATE INDEX IF NOT EXISTS idx_friend_requests_target ON friend_requests(target_did);
"""


class FriendDB:
    """本地 SQLite friends cache (链上 attestation 是真相源).

    使用模式:
        with FriendDB() as db:
            db.upsert_friend(friend)
            db.list_friends()
    """

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self.db_path = Path(db_path) if db_path else _default_friend_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> FriendDB:
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()

    # ── friends ──
    def upsert_friend(self, friend: Friend) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO friends "
            "(did, handle, status, strong_tie_score, manual_score_override, "
            " created_at, became_active_at, last_interaction, interaction_count, "
            " request_attestation_uid, accept_attestation_uid, mutual_attestation_uid, "
            " revoke_attestation_uid, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                friend.did,
                friend.handle,
                friend.status,
                friend.strong_tie_score,
                friend.manual_score_override,
                friend.created_at,
                friend.became_active_at,
                friend.last_interaction,
                friend.interaction_count,
                friend.request_attestation_uid,
                friend.accept_attestation_uid,
                friend.mutual_attestation_uid,
                friend.revoke_attestation_uid,
                friend.notes,
            ),
        )
        self._conn.commit()

    def get_friend(self, did: str) -> Optional[Friend]:
        did = _normalize_did(did)
        r = self._conn.execute(
            "SELECT * FROM friends WHERE did=?", (did,)
        ).fetchone()
        return self._row_to_friend(r) if r else None

    def list_friends(
        self, status: Optional[FriendStatus] = None, limit: int = 500
    ) -> list[Friend]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM friends WHERE status=? ORDER BY created_at ASC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM friends ORDER BY created_at ASC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_friend(r) for r in rows]

    def delete_friend(self, did: str) -> bool:
        did = _normalize_did(did)
        cur = self._conn.execute("DELETE FROM friends WHERE did=?", (did,))
        self._conn.commit()
        return cur.rowcount > 0

    # ── friend_requests ──
    def upsert_request(self, req: FriendRequest) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO friend_requests "
            "(request_id, requester_did, target_did, direction, message, "
            " created_at, attestation_uid, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                req.request_id,
                req.requester_did,
                req.target_did,
                req.direction,
                req.message,
                req.created_at,
                req.attestation_uid,
                req.status,
            ),
        )
        self._conn.commit()

    def get_request(self, request_id: str) -> Optional[FriendRequest]:
        r = self._conn.execute(
            "SELECT * FROM friend_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        return self._row_to_request(r) if r else None

    def list_requests(
        self,
        direction: Optional[Literal["inbound", "outbound"]] = None,
        status: Optional[str] = None,
        limit: int = 200,
    ) -> list[FriendRequest]:
        q = "SELECT * FROM friend_requests WHERE 1=1"
        params: list[Any] = []
        if direction:
            q += " AND direction=?"
            params.append(direction)
        if status:
            q += " AND status=?"
            params.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(q, tuple(params)).fetchall()
        return [self._row_to_request(r) for r in rows]

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in ("pending", "active", "revoked"):
            n = self._conn.execute(
                "SELECT COUNT(*) FROM friends WHERE status=?", (s,)
            ).fetchone()[0]
            out[f"friends_{s}"] = int(n)
        for rs in ("pending", "accepted", "rejected", "revoked"):
            n = self._conn.execute(
                "SELECT COUNT(*) FROM friend_requests WHERE status=?", (rs,)
            ).fetchone()[0]
            out[f"requests_{rs}"] = int(n)
        return out

    # ── 内部 row → 对象 ──
    @staticmethod
    def _row_to_friend(r: sqlite3.Row) -> Friend:
        return Friend(
            did=r["did"],
            handle=r["handle"],
            status=r["status"],
            strong_tie_score=float(r["strong_tie_score"]),
            manual_score_override=(
                float(r["manual_score_override"])
                if r["manual_score_override"] is not None
                else None
            ),
            created_at=r["created_at"],
            became_active_at=r["became_active_at"],
            last_interaction=r["last_interaction"],
            interaction_count=int(r["interaction_count"]),
            request_attestation_uid=r["request_attestation_uid"],
            accept_attestation_uid=r["accept_attestation_uid"],
            mutual_attestation_uid=r["mutual_attestation_uid"],
            revoke_attestation_uid=r["revoke_attestation_uid"],
            notes=r["notes"] or "",
        )

    @staticmethod
    def _row_to_request(r: sqlite3.Row) -> FriendRequest:
        return FriendRequest(
            request_id=r["request_id"],
            requester_did=r["requester_did"],
            target_did=r["target_did"],
            direction=r["direction"],
            message=r["message"] or "",
            created_at=r["created_at"],
            attestation_uid=r["attestation_uid"],
            status=r["status"],
        )


# ── EAS attestation 集成 (复用波 4 dev-B AttestQueue) ───────────────────────


def encode_friend_attestation_data(
    requester_did: str,
    target_did: str,
    relationship_type: RelationshipType,
    timestamp: int,
    message: str = "",
) -> bytes:
    """FRIEND_RELATIONSHIP schema 数据 canonical 编码 (JSON, mock).

    真上链 (Phase 5 GA) 切 eth_abi.encode 按 schema 类型编码:
        encode(["string","string","string","uint64","string"],
               [requester_did, target_did, relationship_type, timestamp, message])
    """
    payload = json.dumps(
        {
            "requester_did": requester_did,
            "target_did": target_did,
            "relationship_type": relationship_type,
            "timestamp": timestamp,
            "message": message,
        },
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return payload


def _compute_friend_attestation_uid(
    requester_did: str,
    target_did: str,
    relationship_type: RelationshipType,
    timestamp: int,
    message: str,
    nonce: str,
) -> str:
    """确定性 mock attestation UID (本地 verify 自洽).

    真链上 UID 由 EAS contract 算 keccak256.
    """
    h = hashlib.sha256()
    h.update(
        encode_friend_attestation_data(
            requester_did, target_did, relationship_type, timestamp, message
        )
    )
    h.update(b"::")
    h.update(FRIEND_RELATIONSHIP_SCHEMA_UID.encode("utf-8"))
    h.update(b"::")
    h.update(nonce.encode("utf-8"))
    return "0x" + h.hexdigest()


def enqueue_friend_attestation(
    requester_did: str,
    target_did: str,
    relationship_type: RelationshipType,
    *,
    message: str = "",
    timestamp: Optional[int] = None,
    attest_queue_db: Optional[Path | str] = None,
) -> dict[str, Any]:
    """把 FRIEND_RELATIONSHIP attestation 入波 4 dev-B AttestQueue.

    本 wave 实现策略 (跟 dev-B 协调):
    - dev-B AttestQueue 当前只接 AuditAttestation (sisoul-audit-v1 schema).
    - 我们把 friend attestation 包成 AuditAttestation, action_type="friend-<type>",
      target=target_did, tool_name="sisoul-friend", prompt=canonical FRIEND_RELATIONSHIP payload.
      这样 dev-B 的 batch 上链路径完全复用, 不需要 dev-B 改代码.
    - Phase 5 GA 时, dev-B 改 AttestQueue 支持 multi-schema → 我们改这里走 native schema.
    - schema_uid 用 FRIEND_RELATIONSHIP_SCHEMA_UID (mock) 记到 queue_id 的 metadata
      (借 prompt 字段 canonical encode 保留).

    返:
        {"queue_id":..., "attestation_uid":..., "schema_uid":..., "attestation_payload":...}
    """
    try:
        from sisoul.onchain.eas import AttestQueue, AuditAttestation
    except Exception as e:  # noqa: BLE001
        raise FriendError(
            f"无法 import sisoul.onchain.eas (波 4 dev-B EAS 模块): {e}"
        ) from e

    ts = timestamp if timestamp is not None else int(time.time())
    requester_did = _normalize_did(requester_did)
    target_did = _normalize_did(target_did)
    payload_bytes = encode_friend_attestation_data(
        requester_did, target_did, relationship_type, ts, message
    )
    # 算确定性 attestation UID (mock 本地 verify 自洽; 链上真 UID 等真发 tx).
    nonce = uuid.uuid4().hex
    attestation_uid = _compute_friend_attestation_uid(
        requester_did, target_did, relationship_type, ts, message, nonce
    )

    # 包成 AuditAttestation 入波 4 queue (dev-B 现接 audit schema, 不需要改 EAS 模块).
    att = AuditAttestation.from_audit_payload(
        actor_did=requester_did,
        action_type=f"friend-{relationship_type}",
        target=target_did,
        prompt=payload_bytes.decode("utf-8"),  # canonical 编码进 prompt, sha256 入 prompt_hash
        tool_name="sisoul-friend",
        timestamp=ts,
    )

    with AttestQueue(
        db_path=Path(attest_queue_db) if attest_queue_db else None
    ) as q:
        queue_id = q.enqueue(att)

    return {
        "queue_id": queue_id,
        "attestation_uid": attestation_uid,
        "schema_uid": FRIEND_RELATIONSHIP_SCHEMA_UID,
        "attestation_payload": json.loads(payload_bytes.decode("utf-8")),
        "queued_at": att.queued_at,
    }


def verify_mutual_attestation(friend: Friend) -> dict[str, Any]:
    """verify 双向 attestation 是否齐 (本地 cache 校验).

    真 GA: 查链上 EAS GraphQL 看双向 attestation 真在链上 (复用 dev-B
    sisoul.onchain.eas.verify_attestation_onchain).
    本 wave: 看本地 Friend 字段 accept_attestation_uid + mutual_attestation_uid 都有.
    """
    return {
        "did": friend.did,
        "is_mutual": friend.is_mutual,
        "accept_attestation_uid": friend.accept_attestation_uid,
        "mutual_attestation_uid": friend.mutual_attestation_uid,
        "status": friend.status,
        "method": "local-cache",
        "note": (
            "本地 cache verify (Phase 5 GA 切真链上 EAS GraphQL "
            "verify_attestation_onchain)."
        ),
    }


# ── 强连接评分 ──────────────────────────────────────────────────────────────


def _months_between(iso_start: str, iso_now: Optional[str] = None) -> float:
    """简化版月份差 (按 30 天 = 1 月). 用 ISO 字符串差秒数除 30 天.

    不用 dateutil, 避免新依赖.
    """
    if not iso_start:
        return 0.0
    try:
        start = datetime.fromisoformat(iso_start)
    except ValueError:
        return 0.0
    if iso_now:
        try:
            now = datetime.fromisoformat(iso_now)
        except ValueError:
            now = datetime.now(timezone.utc)
    else:
        now = datetime.now(timezone.utc)
    # 容错 naive datetime
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta_sec = (now - start).total_seconds()
    if delta_sec <= 0:
        return 0.0
    return delta_sec / (30 * 86400.0)


def compute_strong_tie_score(
    friend: Friend, *, now_iso: Optional[str] = None
) -> StrongTieScore:
    """计算强连接评分 (§28 §3.1).

    评分组成:
    - mutual 基础: 1 分 (只在 is_mutual=True 才给)
    - 时长: 从 became_active_at 起每月 +0.5, 上限 6
    - 互动次数: 每 10 次 +0.5, 上限 5
    - manual_override 覆盖: 用户手动设的分数直接用 (上限 15)

    总分 < 5 = 弱连接, ≥ 5 = 强连接.

    "互动次数" 定义 (本 wave dev-A 决策):
        - lend / borrow 完成各算 +1 (dev-D 接 ledger 后调 record_interaction)
        - friend accept 触发后初始 0 (mutual 本身不算互动, 是基础)
        - manual record_interaction(friend.did) 也算 (CLI / API 显式调)
        - rate limit: 同 friend 每分钟最多 1 次自动 increment (anti gaming, dev-C anti_abuse
          后会加全局拦截)
    """
    # manual override 直接返
    if friend.manual_score_override is not None:
        m = float(friend.manual_score_override)
        m = max(0.0, min(m, STRONG_TIE_MANUAL_OVERRIDE_MAX))
        return StrongTieScore(
            total=m,
            is_strong=m >= STRONG_TIE_THRESHOLD,
            base=0.0,
            months_score=0.0,
            interactions_score=0.0,
            months_elapsed=0.0,
            interaction_count=friend.interaction_count,
            manual_override=m,
        )

    # 非 mutual = 全部 0
    if not friend.is_mutual:
        return StrongTieScore(
            total=0.0,
            is_strong=False,
            base=0.0,
            months_score=0.0,
            interactions_score=0.0,
            months_elapsed=0.0,
            interaction_count=friend.interaction_count,
        )

    base = STRONG_TIE_BASE_SCORE
    months = _months_between(friend.became_active_at or friend.created_at, now_iso)
    months_score = min(months * STRONG_TIE_PER_MONTH, STRONG_TIE_MONTH_CAP)
    interactions_score = min(
        (friend.interaction_count // 10) * STRONG_TIE_PER_10_INTERACTIONS,
        STRONG_TIE_INTERACTION_CAP,
    )
    total = base + months_score + interactions_score
    return StrongTieScore(
        total=round(total, 4),
        is_strong=total >= STRONG_TIE_THRESHOLD,
        base=base,
        months_score=round(months_score, 4),
        interactions_score=round(interactions_score, 4),
        months_elapsed=round(months, 4),
        interaction_count=friend.interaction_count,
    )


def record_interaction(
    did: str,
    *,
    increment: int = 1,
    db_path: Optional[Path | str] = None,
    now_iso: Optional[str] = None,
) -> Friend:
    """记录一次互动 (lend/borrow/collab/manual), 更新 last_interaction + count + score.

    返更新后的 Friend.
    dev-D 接 ledger 后会调这, dev-C anti_abuse 后会做 rate limit 拦截.
    """
    if increment < 0:
        raise FriendError(f"increment 必须 ≥ 0, 拿到 {increment}")
    with FriendDB(db_path=db_path) as db:
        friend = db.get_friend(did)
        if not friend:
            raise FriendNotFoundError(
                f"friend 不在本地 cache: {did} (先 sisoul friend request / accept)"
            )
        friend.interaction_count += increment
        friend.last_interaction = now_iso or datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        score = compute_strong_tie_score(friend, now_iso=now_iso)
        friend.strong_tie_score = score.total
        db.upsert_friend(friend)
        return friend


# ── 高层 API: FriendRelationship ─────────────────────────────────────────────


class FriendRelationship:
    """双向 mutual attestation 朋友关系管理.

    own_did: 本 sisoul 实例的 DID (从 sisoul.identity.did.list_local_dids() 第一条拿).

    使用模式:
        rel = FriendRelationship(own_did="did:sisoul:alice")
        rel.send_friend_request("did:sisoul:bob", message="加个好友")
        # ...对方 sisoul daemon 收到 inbound request...
        rel.accept_friend_request(request_id)
        rel.list_friends()
        rel.revoke_friend("did:sisoul:bob")
    """

    def __init__(
        self,
        own_did: str,
        *,
        db_path: Optional[Path | str] = None,
        attest_queue_db: Optional[Path | str] = None,
    ) -> None:
        self.own_did = _normalize_did(own_did)
        self.db_path = Path(db_path) if db_path else _default_friend_db()
        self.attest_queue_db = (
            Path(attest_queue_db) if attest_queue_db else None
        )

    # ── send / accept / revoke ──
    def send_friend_request(
        self, target_did: str, *, message: str = ""
    ) -> FriendRequest:
        """发 FRIEND_REQUEST attestation, 本地落 outbound request, 创建/更新 Friend(status=pending).

        真 GA 走 P2P (波 4 dev-A sisoul.p2p) 传 attestation 给对方 daemon, 对方落 inbound.
        本 wave 只把 attestation 入 EAS queue, P2P 传输 stub (Phase 5 接).
        """
        target_did = _normalize_did(target_did)
        if target_did == self.own_did:
            raise FriendRequestError("不能加自己为朋友")

        # EAS attestation 入 queue
        att_result = enqueue_friend_attestation(
            requester_did=self.own_did,
            target_did=target_did,
            relationship_type="request",
            message=message,
            attest_queue_db=self.attest_queue_db,
        )

        req = FriendRequest(
            request_id=str(uuid.uuid4()),
            requester_did=self.own_did,
            target_did=target_did,
            direction="outbound",
            message=message,
            attestation_uid=att_result["attestation_uid"],
            status="pending",
        )

        with FriendDB(db_path=self.db_path) as db:
            db.upsert_request(req)
            # 把对方加进 friends cache, status=pending
            existing = db.get_friend(target_did)
            if existing is None:
                friend = Friend(
                    did=target_did,
                    status="pending",
                    request_attestation_uid=att_result["attestation_uid"],
                )
                db.upsert_friend(friend)
            else:
                existing.request_attestation_uid = att_result["attestation_uid"]
                if existing.status == "revoked":
                    existing.status = "pending"
                db.upsert_friend(existing)

        # TODO Phase 5: P2P 传输 → 调 sisoul.p2p 把 FRIEND_REQUEST 发给对方 daemon
        return req

    def receive_friend_request(
        self,
        requester_did: str,
        *,
        message: str = "",
        attestation_uid: Optional[str] = None,
    ) -> FriendRequest:
        """对方 daemon 调本端 daemon 入站 FRIEND_REQUEST.

        本 wave: 直接落 inbound request (本地). 真 GA: 走 P2P recv hook.
        """
        requester_did = _normalize_did(requester_did)
        if requester_did == self.own_did:
            raise FriendRequestError("不能收自己发的 friend request")

        req = FriendRequest(
            request_id=str(uuid.uuid4()),
            requester_did=requester_did,
            target_did=self.own_did,
            direction="inbound",
            message=message,
            attestation_uid=attestation_uid,
            status="pending",
        )
        with FriendDB(db_path=self.db_path) as db:
            db.upsert_request(req)
            # 对方加进 friends cache, status=pending
            existing = db.get_friend(requester_did)
            if existing is None:
                friend = Friend(
                    did=requester_did,
                    status="pending",
                    request_attestation_uid=attestation_uid,
                )
                db.upsert_friend(friend)
            else:
                existing.request_attestation_uid = (
                    attestation_uid or existing.request_attestation_uid
                )
                if existing.status == "revoked":
                    existing.status = "pending"
                db.upsert_friend(existing)
        return req

    def accept_friend_request(self, request_id: str) -> Friend:
        """accept inbound FRIEND_REQUEST → 上链 FRIEND_ACCEPT (双向 attestation 完成).

        逻辑:
        1. 查本地 request (必须 direction=inbound, status=pending)
        2. enqueue FRIEND_ACCEPT attestation (本端 attest 对方是朋友)
        3. 标 request status=accepted
        4. 标对应 Friend status=active + accept_attestation_uid
        5. (Phase 5) P2P 通知对方: 对方收到后也 enqueue FRIEND_ACCEPT
           → 对方那边的 mutual_attestation_uid = 本端的 accept_attestation_uid
           本 wave: 同机 2 实例集成测试里手工模拟双向 accept.
        """
        with FriendDB(db_path=self.db_path) as db:
            req = db.get_request(request_id)
            if not req:
                raise FriendRequestNotFoundError(f"request_id 不存在: {request_id}")
            if req.direction != "inbound":
                raise FriendRequestError(
                    f"只能 accept inbound request (本 request direction={req.direction})"
                )
            if req.status != "pending":
                raise FriendRequestError(
                    f"request 已 {req.status}, 不能再 accept"
                )

            # 上链 FRIEND_ACCEPT
            att_result = enqueue_friend_attestation(
                requester_did=self.own_did,
                target_did=req.requester_did,
                relationship_type="accept",
                message="",
                attest_queue_db=self.attest_queue_db,
            )

            req.status = "accepted"
            db.upsert_request(req)

            friend = db.get_friend(req.requester_did) or Friend(
                did=req.requester_did, status="pending"
            )
            friend.status = "active"
            friend.accept_attestation_uid = att_result["attestation_uid"]
            friend.became_active_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            )
            # 重算 score (此时 interaction_count=0, score=base 1.0)
            score = compute_strong_tie_score(friend)
            friend.strong_tie_score = score.total
            db.upsert_friend(friend)
            return friend

    def confirm_mutual_attestation(
        self, friend_did: str, mutual_attestation_uid: str
    ) -> Friend:
        """收到对方 FRIEND_ACCEPT attestation 后, 标本端 friend.mutual_attestation_uid.

        本 wave: 集成测试里手工调; Phase 5 P2P 收到对方 attest 自动调.
        """
        friend_did = _normalize_did(friend_did)
        with FriendDB(db_path=self.db_path) as db:
            friend = db.get_friend(friend_did)
            if not friend:
                raise FriendNotFoundError(
                    f"friend 不在本地: {friend_did}"
                )
            friend.mutual_attestation_uid = mutual_attestation_uid
            if friend.is_mutual and friend.status != "active":
                friend.status = "active"
                if not friend.became_active_at:
                    friend.became_active_at = datetime.now(
                        timezone.utc
                    ).isoformat(timespec="seconds")
            score = compute_strong_tie_score(friend)
            friend.strong_tie_score = score.total
            db.upsert_friend(friend)
            return friend

    def revoke_friend(self, did: str) -> Friend:
        """revoke FRIEND_RELATIONSHIP → 上链 REVOKE attestation + 本端标 revoked."""
        did = _normalize_did(did)
        with FriendDB(db_path=self.db_path) as db:
            friend = db.get_friend(did)
            if not friend:
                raise FriendNotFoundError(f"friend 不在本地: {did}")

            att_result = enqueue_friend_attestation(
                requester_did=self.own_did,
                target_did=did,
                relationship_type="revoke",
                message="",
                attest_queue_db=self.attest_queue_db,
            )
            friend.status = "revoked"
            friend.revoke_attestation_uid = att_result["attestation_uid"]
            friend.strong_tie_score = 0.0
            db.upsert_friend(friend)
            return friend

    # ── 读 ──
    def list_friends(
        self, status: Optional[FriendStatus] = None, recompute_score: bool = False
    ) -> list[Friend]:
        """列本地 friends. recompute_score=True 时每个都重新算 score (慢)."""
        with FriendDB(db_path=self.db_path) as db:
            items = db.list_friends(status=status)
            if recompute_score:
                for fr in items:
                    fr.strong_tie_score = compute_strong_tie_score(fr).total
                    db.upsert_friend(fr)
            return items

    def list_requests(
        self,
        direction: Optional[Literal["inbound", "outbound"]] = None,
        status: Optional[str] = None,
    ) -> list[FriendRequest]:
        with FriendDB(db_path=self.db_path) as db:
            return db.list_requests(direction=direction, status=status)

    def get_friend(self, did: str) -> Friend:
        with FriendDB(db_path=self.db_path) as db:
            friend = db.get_friend(did)
            if not friend:
                raise FriendNotFoundError(f"friend 不在本地: {did}")
            return friend

    def get_score(self, did: str, *, now_iso: Optional[str] = None) -> StrongTieScore:
        friend = self.get_friend(did)
        return compute_strong_tie_score(friend, now_iso=now_iso)

    def set_manual_score(self, did: str, score: Optional[float]) -> Friend:
        """手动覆盖某 friend 的强连接评分. score=None 取消覆盖."""
        with FriendDB(db_path=self.db_path) as db:
            friend = db.get_friend(did)
            if not friend:
                raise FriendNotFoundError(f"friend 不在本地: {did}")
            friend.manual_score_override = score
            friend.strong_tie_score = compute_strong_tie_score(friend).total
            db.upsert_friend(friend)
            return friend


# ── attester DID 解析 helper ──────────────────────────────────────────────────


def resolve_own_did(
    *, registry_path: Optional[Path] = None, fallback: Optional[str] = None
) -> str:
    """从波 3 dev-B DID registry 拿本机第一条 DID 作为 own_did.

    fallback: registry 空时返这个 (test/CI 用), None 时 raise.
    """
    try:
        from sisoul.identity.did import list_local_dids
    except Exception as e:  # noqa: BLE001
        raise FriendError(
            f"无法 import sisoul.identity.did (波 3 dev-B): {e}"
        ) from e

    dids = list_local_dids(registry_path=registry_path)
    if not dids:
        if fallback:
            return _normalize_did(fallback)
        raise FriendError(
            "本机无 DID. 先 `sisoul did register <handle>` 或显式传 own_did."
        )
    return dids[0].did_string


__all__ = [
    # 常量
    "DEFAULT_FRIEND_DB",
    "FRIEND_RELATIONSHIP_SCHEMA",
    "FRIEND_RELATIONSHIP_SCHEMA_UID",
    "STRONG_TIE_BASE_SCORE",
    "STRONG_TIE_PER_MONTH",
    "STRONG_TIE_MONTH_CAP",
    "STRONG_TIE_PER_10_INTERACTIONS",
    "STRONG_TIE_INTERACTION_CAP",
    "STRONG_TIE_THRESHOLD",
    "STRONG_TIE_MAX",
    "STRONG_TIE_MANUAL_OVERRIDE_MAX",
    "FriendStatus",
    "RelationshipType",
    # 异常
    "FriendError",
    "FriendNotFoundError",
    "FriendRequestError",
    "FriendRequestNotFoundError",
    # 数据
    "Friend",
    "FriendRequest",
    "StrongTieScore",
    # DB
    "FriendDB",
    # EAS
    "encode_friend_attestation_data",
    "enqueue_friend_attestation",
    "verify_mutual_attestation",
    # 评分
    "compute_strong_tie_score",
    "record_interaction",
    # 高层
    "FriendRelationship",
    "resolve_own_did",
]
