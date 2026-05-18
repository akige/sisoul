"""sisoul friend · 互惠 ledger (§28 §3.4) · Phase 4 W66-W76, 波 5 dev-D.

不发币的经济模型: 链上 EAS RESOURCE_USAGE attestation 累积 borrow/lend.
PWA 显示 Alice ↔ Bob 比率 + 不平衡警告 (默认 2:1 阈值).

数据流:
    record_usage(borrower, lender, resource_type, amount, model)
        → 本地 SQLite (~/.sisoul/ledger.db, 即时可查)
        → AttestQueue (波 4 dev-B onchain.eas) batched 上链 (异步, ~10 条/批)

跨 dev 边界:
- 复用 sisoul.onchain.eas.AuditAttestation + AttestQueue (action_type='resource-usage').
  RESOURCE_USAGE schema 字段 mapping 到 audit schema 字段 (避免改 dev-B 的 SISOUL_AUDIT_SCHEMA).
  详 _to_audit_attestation() docstring.
- 不强依赖 dev-A relationship (查不到 Friend 元数据 fallback 直接用 DID 字符串).

模块边界 (波 5 dev-D 严格约束):
- 不动: sisoul.{vault, llm, sync, identity, p2p, onchain, daemon.py 主, cli.py 主}
- 不动: sisoul.friend.{__init__, relationship(dev-A), encrypted_proxy(dev-B), permissions(dev-C),
       anti_abuse(dev-C), proxy_audit(dev-B)}
- 本文件独立 (不 import sisoul.friend 顶层, 防 __init__ 触发 dev-A 未 ship import error)
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

# ── 公共常量 ─────────────────────────────────────────────────────────────────

DEFAULT_LEDGER_DB = Path.home() / ".sisoul" / "ledger.db"

# RESOURCE_USAGE schema (§28 §3.4) — 概念 schema, 落到 EAS 时映射 audit schema.
RESOURCE_USAGE_SCHEMA = (
    "string borrower_did,"
    "string lender_did,"
    "string resource_type,"
    "uint256 amount,"
    "string model_or_skill_id,"
    "uint64 ts,"
    "string direction"
)

ResourceType = Literal["llm_quota", "ai_skill", "compute"]
Direction = Literal["borrow", "lend"]

DEFAULT_IMBALANCE_THRESHOLD = 2.0  # 比 > 2:1 → ⚠️ 告警 (§28 §3.4)


# ── 异常 ─────────────────────────────────────────────────────────────────────


class LedgerError(Exception):
    """ledger 通用异常."""


# ── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class LedgerEntry:
    """单条 ledger 记录 (一笔 borrow 或 lend usage)."""

    borrower_did: str
    lender_did: str
    resource_type: ResourceType
    amount: int
    model_or_skill_id: str
    direction: Direction  # borrow=Alice 借入 (本机 Alice) | lend=Alice 借出 (本机 Bob)
    ts: int = 0  # unix epoch
    entry_id: str = ""  # uuid4
    onchain_status: Literal["pending", "queued", "confirmed", "failed", "off-chain"] = "pending"
    attest_queue_id: Optional[str] = None  # 关联到 AttestQueue.queue_id (dev-B onchain)
    attestation_uid: Optional[str] = None  # 链上返回
    ledger_tx_hash: Optional[str] = None
    note: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.entry_id:
            self.entry_id = str(uuid.uuid4())
        if not self.ts:
            self.ts = int(time.time())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FriendBalance:
    """Alice 跟某朋友的 ledger 余额视图 (§28 §3.4 PWA 显示)."""

    friend_did: str
    borrowed_from_friend: dict[str, int]  # resource_type → cumulative amount
    lent_to_friend: dict[str, int]
    borrowed_total: int  # 简单加总 (跨 resource_type, 不归一; PWA 仍按 resource 分别显示)
    lent_total: int
    ratio: float  # borrowed_total / lent_total (Alice 借的 vs Alice 借出). >1 = Alice 欠
    ratio_inverted: float  # lent / borrowed
    imbalance_warning: bool
    threshold: float
    direction_imbalance: Literal["borrower-heavy", "lender-heavy", "balanced"]
    entry_count: int
    last_activity_ts: Optional[int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImbalanceWarning:
    """单条不平衡警告."""

    friend_did: str
    ratio: float
    threshold: float
    direction: Literal["borrower-heavy", "lender-heavy"]
    borrowed_total: int
    lent_total: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── DB schema ────────────────────────────────────────────────────────────────


_LEDGER_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ledger_entries (
    entry_id TEXT PRIMARY KEY,
    borrower_did TEXT NOT NULL,
    lender_did TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    amount INTEGER NOT NULL,
    model_or_skill_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    ts INTEGER NOT NULL,
    onchain_status TEXT NOT NULL DEFAULT 'pending',
    attest_queue_id TEXT,
    attestation_uid TEXT,
    ledger_tx_hash TEXT,
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_ledger_borrower ON ledger_entries(borrower_did);
CREATE INDEX IF NOT EXISTS idx_ledger_lender ON ledger_entries(lender_did);
CREATE INDEX IF NOT EXISTS idx_ledger_status ON ledger_entries(onchain_status);
CREATE INDEX IF NOT EXISTS idx_ledger_ts ON ledger_entries(ts);

CREATE TABLE IF NOT EXISTS ledger_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ── ReciprocityLedger ────────────────────────────────────────────────────────


class ReciprocityLedger:
    """互惠 ledger · 本地 SQLite + EAS attestation queue 上链.

    使用模式:
        led = ReciprocityLedger()
        led.record_usage("alice.sisoul.eth", "bob.sisoul.eth",
                         "llm_quota", 1234, "claude-opus-4-7", direction="borrow")
        bal = led.query_balance("bob.sisoul.eth", self_did="alice.sisoul.eth")
        warnings = led.list_imbalance_warnings(self_did="alice.sisoul.eth")
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        self_did: str | None = None,
    ) -> None:
        # 注: DEFAULT_LEDGER_DB 在 import 时 freeze 自 Path.home(); 这里 lazy 重算
        # 以兼容 monkeypatch HOME (test isolation 必需).
        self.db_path = (
            Path(db_path) if db_path else (Path.home() / ".sisoul" / "ledger.db")
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_LEDGER_SCHEMA_SQL)
        self._conn.commit()
        self._self_did = self_did  # 可选, query/imbalance 时省力

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> "ReciprocityLedger":
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()

    # ── 写 ──────────────────────────────────────────────────────────────────
    def record_usage(
        self,
        borrower_did: str,
        lender_did: str,
        resource_type: ResourceType,
        amount: int,
        model_or_skill_id: str,
        *,
        direction: Direction = "borrow",
        ts: int | None = None,
        note: str | None = None,
        enqueue_onchain: bool = True,
        attest_queue_db: Path | str | None = None,
        actor_did: str | None = None,
        tool_name: str = "sisoul-friend",
    ) -> LedgerEntry:
        """记一笔 usage. 默认入 EAS attest queue (异步 batched 上链).

        enqueue_onchain=False → 仅本地 (test / 离线 mode).
        """
        if amount < 0:
            raise LedgerError(f"amount 不能为负: {amount}")
        if direction not in ("borrow", "lend"):
            raise LedgerError(f"direction 必须 borrow/lend, got {direction}")
        if not borrower_did or not lender_did:
            raise LedgerError("borrower_did / lender_did 都不能为空")
        if borrower_did == lender_did:
            raise LedgerError("borrower_did == lender_did, 自借自不合法")

        entry = LedgerEntry(
            borrower_did=borrower_did,
            lender_did=lender_did,
            resource_type=resource_type,
            amount=int(amount),
            model_or_skill_id=model_or_skill_id,
            direction=direction,
            ts=ts or int(time.time()),
            note=note,
        )

        # 异步上链 (best-effort, 失败不阻塞本地写)
        if enqueue_onchain:
            try:
                qid = _enqueue_resource_usage_attestation(
                    entry,
                    actor_did=actor_did,
                    tool_name=tool_name,
                    queue_db=attest_queue_db,
                )
                entry.attest_queue_id = qid
                entry.onchain_status = "queued"
            except Exception as e:
                # fail-open: 上链失败标 off-chain, 不阻塞 borrow 流程
                entry.onchain_status = "off-chain"
                entry.note = (entry.note or "") + f" [enqueue_failed: {type(e).__name__}: {e}]"
        else:
            entry.onchain_status = "off-chain"

        self._insert_entry(entry)
        return entry

    def _insert_entry(self, e: LedgerEntry) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO ledger_entries "
            "(entry_id, borrower_did, lender_did, resource_type, amount, model_or_skill_id, "
            " direction, ts, onchain_status, attest_queue_id, attestation_uid, "
            " ledger_tx_hash, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                e.entry_id,
                e.borrower_did,
                e.lender_did,
                e.resource_type,
                e.amount,
                e.model_or_skill_id,
                e.direction,
                e.ts,
                e.onchain_status,
                e.attest_queue_id,
                e.attestation_uid,
                e.ledger_tx_hash,
                e.note,
            ),
        )
        self._conn.commit()

    def mark_confirmed(
        self,
        entry_id: str,
        attestation_uid: str,
        ledger_tx_hash: str | None = None,
    ) -> None:
        """上链 batched 完成后回调."""
        self._conn.execute(
            "UPDATE ledger_entries SET onchain_status='confirmed', "
            "attestation_uid=?, ledger_tx_hash=? WHERE entry_id=?",
            (attestation_uid, ledger_tx_hash, entry_id),
        )
        self._conn.commit()

    # ── 读 ──────────────────────────────────────────────────────────────────
    def list_entries(
        self,
        *,
        borrower_did: str | None = None,
        lender_did: str | None = None,
        friend_did: str | None = None,
        self_did: str | None = None,
        limit: int = 200,
    ) -> list[LedgerEntry]:
        """列 entries. friend_did + self_did → 双向 (self 跟某 friend 全部交互)."""
        clauses: list[str] = []
        params: list[Any] = []
        if borrower_did:
            clauses.append("borrower_did=?")
            params.append(borrower_did)
        if lender_did:
            clauses.append("lender_did=?")
            params.append(lender_did)
        if friend_did and self_did:
            clauses.append(
                "((borrower_did=? AND lender_did=?) OR (borrower_did=? AND lender_did=?))"
            )
            params.extend([self_did, friend_did, friend_did, self_did])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        q = f"SELECT * FROM ledger_entries{where} ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(q, params).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def query_balance(
        self,
        friend_did: str,
        *,
        self_did: str | None = None,
        threshold: float = DEFAULT_IMBALANCE_THRESHOLD,
        since_ts: int | None = None,
    ) -> FriendBalance:
        """Alice ↔ friend ledger 视图. self_did 默认走 __init__ 时给的."""
        me = self_did or self._self_did
        if not me:
            raise LedgerError(
                "query_balance 需要 self_did (ReciprocityLedger 构造时传或本函数传)"
            )
        if me == friend_did:
            raise LedgerError("self_did == friend_did, 不合法")

        # 同时统计跨 direction × 两侧 (避免 direction 半填漏算).
        # borrowed_from_friend = 本机 me 借入 = (borrower=me AND lender=friend) 不管 direction
        # 但 direction='lend' 由对端写的 lend mirror 不入本机 ledger; 本机 ledger 只统计本机视角.
        # 所以这里只看 borrower/lender 配对.
        time_clause = ""
        params: list[Any] = [me, friend_did, friend_did, me]
        if since_ts:
            time_clause = " AND ts >= ?"
            params.extend([since_ts, since_ts])

        # 本机 me 借入 friend
        sql_borrow = (
            "SELECT resource_type, COALESCE(SUM(amount), 0) AS total, "
            "       COALESCE(MAX(ts), 0) AS last_ts, COUNT(*) AS n "
            "FROM ledger_entries WHERE borrower_did=? AND lender_did=?" + time_clause +
            " GROUP BY resource_type"
        )
        b_params = [me, friend_did] + ([since_ts] if since_ts else [])
        borrow_rows = self._conn.execute(sql_borrow, b_params).fetchall()

        # 本机 me 借出 friend (me=lender, friend=borrower)
        sql_lend = (
            "SELECT resource_type, COALESCE(SUM(amount), 0) AS total, "
            "       COALESCE(MAX(ts), 0) AS last_ts, COUNT(*) AS n "
            "FROM ledger_entries WHERE borrower_did=? AND lender_did=?" + time_clause +
            " GROUP BY resource_type"
        )
        l_params = [friend_did, me] + ([since_ts] if since_ts else [])
        lend_rows = self._conn.execute(sql_lend, l_params).fetchall()

        borrowed: dict[str, int] = {r["resource_type"]: int(r["total"]) for r in borrow_rows}
        lent: dict[str, int] = {r["resource_type"]: int(r["total"]) for r in lend_rows}

        b_total = sum(borrowed.values())
        l_total = sum(lent.values())

        # ratio: borrowed / lent (>1 表示 Alice 欠多)
        if l_total == 0 and b_total == 0:
            ratio = 0.0
            ratio_inv = 0.0
        elif l_total == 0:
            ratio = float("inf")
            ratio_inv = 0.0
        elif b_total == 0:
            ratio = 0.0
            ratio_inv = float("inf")
        else:
            ratio = b_total / l_total
            ratio_inv = l_total / b_total

        if ratio == 0.0 and ratio_inv == 0.0:
            direction_imbalance: Literal["borrower-heavy", "lender-heavy", "balanced"] = "balanced"
        elif ratio > threshold:
            direction_imbalance = "borrower-heavy"
        elif ratio_inv > threshold:
            direction_imbalance = "lender-heavy"
        else:
            direction_imbalance = "balanced"

        imbalance_warning = direction_imbalance != "balanced"

        last_ts_b = max((int(r["last_ts"]) for r in borrow_rows), default=0)
        last_ts_l = max((int(r["last_ts"]) for r in lend_rows), default=0)
        last_ts = max(last_ts_b, last_ts_l) or None

        entry_count = sum(int(r["n"]) for r in borrow_rows) + sum(int(r["n"]) for r in lend_rows)

        return FriendBalance(
            friend_did=friend_did,
            borrowed_from_friend=borrowed,
            lent_to_friend=lent,
            borrowed_total=b_total,
            lent_total=l_total,
            ratio=ratio,
            ratio_inverted=ratio_inv,
            imbalance_warning=imbalance_warning,
            threshold=threshold,
            direction_imbalance=direction_imbalance,
            entry_count=entry_count,
            last_activity_ts=last_ts,
        )

    def list_friends(self, *, self_did: str | None = None) -> list[str]:
        """列所有本机 ledger 涉及的对端 DID (去重)."""
        me = self_did or self._self_did
        if not me:
            # 没 self_did → 列全部出现过的 DID
            rows = self._conn.execute(
                "SELECT DISTINCT did FROM ("
                "SELECT borrower_did AS did FROM ledger_entries "
                "UNION SELECT lender_did AS did FROM ledger_entries) "
                "WHERE did IS NOT NULL ORDER BY did"
            ).fetchall()
            return [r["did"] for r in rows if r["did"]]
        rows = self._conn.execute(
            "SELECT DISTINCT friend_did FROM ("
            "SELECT lender_did AS friend_did FROM ledger_entries WHERE borrower_did=? "
            "UNION SELECT borrower_did AS friend_did FROM ledger_entries WHERE lender_did=?"
            ") WHERE friend_did != ? ORDER BY friend_did",
            (me, me, me),
        ).fetchall()
        return [r["friend_did"] for r in rows]

    def check_imbalance_warning(
        self,
        friend_did: str,
        *,
        self_did: str | None = None,
        threshold: float = DEFAULT_IMBALANCE_THRESHOLD,
    ) -> bool:
        return self.query_balance(
            friend_did, self_did=self_did, threshold=threshold
        ).imbalance_warning

    def list_imbalance_warnings(
        self,
        *,
        self_did: str | None = None,
        threshold: float = DEFAULT_IMBALANCE_THRESHOLD,
    ) -> list[ImbalanceWarning]:
        """扫所有 friend, 返 imbalance 列表."""
        me = self_did or self._self_did
        if not me:
            raise LedgerError(
                "list_imbalance_warnings 需要 self_did (ReciprocityLedger 构造时传或本函数传)"
            )
        out: list[ImbalanceWarning] = []
        for fid in self.list_friends(self_did=me):
            bal = self.query_balance(fid, self_did=me, threshold=threshold)
            if bal.imbalance_warning:
                out.append(
                    ImbalanceWarning(
                        friend_did=fid,
                        ratio=bal.ratio if bal.direction_imbalance == "borrower-heavy"
                              else bal.ratio_inverted,
                        threshold=threshold,
                        direction="borrower-heavy"
                                  if bal.direction_imbalance == "borrower-heavy"
                                  else "lender-heavy",
                        borrowed_total=bal.borrowed_total,
                        lent_total=bal.lent_total,
                    )
                )
        return out

    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for status in ("pending", "queued", "confirmed", "failed", "off-chain"):
            n = self._conn.execute(
                "SELECT COUNT(*) FROM ledger_entries WHERE onchain_status=?", (status,)
            ).fetchone()[0]
            out[status] = int(n)
        out["total"] = int(
            self._conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
        )
        return out

    # ── 内部 ────────────────────────────────────────────────────────────────
    @staticmethod
    def _row_to_entry(r: sqlite3.Row) -> LedgerEntry:
        return LedgerEntry(
            borrower_did=r["borrower_did"],
            lender_did=r["lender_did"],
            resource_type=r["resource_type"],
            amount=int(r["amount"]),
            model_or_skill_id=r["model_or_skill_id"],
            direction=r["direction"],
            ts=int(r["ts"]),
            entry_id=r["entry_id"],
            onchain_status=r["onchain_status"],
            attest_queue_id=r["attest_queue_id"],
            attestation_uid=r["attestation_uid"],
            ledger_tx_hash=r["ledger_tx_hash"],
            note=r["note"],
        )


# ── EAS 集成: 复用波 4 dev-B AttestQueue ─────────────────────────────────────


def _enqueue_resource_usage_attestation(
    entry: LedgerEntry,
    *,
    actor_did: str | None,
    tool_name: str,
    queue_db: Path | str | None,
) -> str:
    """把 LedgerEntry 入 dev-B AttestQueue (action_type='resource-usage').

    映射策略 (避免改 dev-B 的 SISOUL_AUDIT_SCHEMA):
      action_type  = "resource-usage"
      target       = f"{direction}:{resource_type}:{model_or_skill_id}:{lender_did}:{amount}"
      prompt_hash  = sha256(canonical JSON of LedgerEntry) (空 prompt fallback)
      actor_did    = 调用方 (默认 borrower_did) — 谁付 gas 谁是 attester

    返 attest_queue queue_id.
    """
    from sisoul.onchain.eas import AttestQueue, AuditAttestation

    payload = {
        "borrower_did": entry.borrower_did,
        "lender_did": entry.lender_did,
        "resource_type": entry.resource_type,
        "amount": entry.amount,
        "model_or_skill_id": entry.model_or_skill_id,
        "direction": entry.direction,
        "ts": entry.ts,
        "entry_id": entry.entry_id,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    att = AuditAttestation.from_audit_payload(
        actor_did=actor_did or entry.borrower_did,
        action_type="resource-usage",
        target=(
            f"{entry.direction}:{entry.resource_type}:"
            f"{entry.model_or_skill_id}:{entry.lender_did}:{entry.amount}"
        ),
        prompt=canonical,
        tool_name=tool_name,
        timestamp=entry.ts,
    )
    q = AttestQueue(db_path=Path(queue_db) if queue_db else None)
    try:
        return q.enqueue(att)
    finally:
        q.close()


# ── dev-A 集成接口 (info endpoint 调) ────────────────────────────────────────


def summarize_friend_ledger(
    own_did: str,
    friend_did: str,
    *,
    threshold: float = DEFAULT_IMBALANCE_THRESHOLD,
    ledger_db: Path | str | None = None,
) -> dict[str, Any]:
    """dev-A friend info endpoint 调的 summary 接口.

    返简化 dict, 不需要 caller import ReciprocityLedger.
    """
    led = ReciprocityLedger(db_path=ledger_db, self_did=own_did)
    try:
        bal = led.query_balance(friend_did, threshold=threshold)
        return {
            "available": True,
            "borrowed_total": bal.borrowed_total,
            "lent_total": bal.lent_total,
            "ratio": bal.ratio,
            "ratio_inverted": bal.ratio_inverted,
            "imbalance_warning": bal.imbalance_warning,
            "direction_imbalance": bal.direction_imbalance,
            "entry_count": bal.entry_count,
            "last_activity_ts": bal.last_activity_ts,
            "threshold": threshold,
        }
    finally:
        led.close()


__all__ = [
    # 常量
    "DEFAULT_LEDGER_DB",
    "RESOURCE_USAGE_SCHEMA",
    "DEFAULT_IMBALANCE_THRESHOLD",
    # 类型
    "ResourceType",
    "Direction",
    # 异常
    "LedgerError",
    # 数据
    "LedgerEntry",
    "FriendBalance",
    "ImbalanceWarning",
    # 主类
    "ReciprocityLedger",
    # dev-A 集成
    "summarize_friend_ledger",
]
