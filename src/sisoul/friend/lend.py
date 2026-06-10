"""sisoul friend · lend (Bob 端) (§28 §3.3 §3.5) · Phase 4 W66-W76, 波 5 dev-D.

Bob 接收 Alice 发来的 LendRequest, per-request 模式弹 PWA 通知 → Bob approve/deny.
strong-tie-auto 模式直接 approve, emergency-only 仅 emergency flag 时通过.

LendRequest 生命周期:
    pending  ─ Alice 调 request_lend() 写入
       │
       ├── approve_lend()  → approved  → 触发 dev-B encrypted_proxy + 写 ledger
       ├── deny_lend()     → denied
       └── (TTL 30s expire) → expired

PWA 集成:
- 每次 pending request 写 ~/.sisoul/pending_lends.json (atomic write, daemon-shared file)
- PWA 每 3s poll /sisoul/lend/pending 看新 request, 弹通知 + 一键 approve/deny

不强依赖 dev-A/B/C (try/except 优雅 fallback), 保证本模块独立可测.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

# ── 公共常量 ─────────────────────────────────────────────────────────────────

DEFAULT_LEND_DB = Path.home() / ".sisoul" / "lend.db"
DEFAULT_PENDING_LENDS_FILE = Path.home() / ".sisoul" / "pending_lends.json"
DEFAULT_REQUEST_TTL_SEC = 30  # per-request 弹窗 timeout (§28 §3.5)

LendStatus = Literal["pending", "approved", "denied", "expired", "completed"]


# ── 异常 ─────────────────────────────────────────────────────────────────────


class LendError(Exception):
    """lend 通用异常."""


class RequestNotFoundError(LendError):
    pass


class RequestStateError(LendError):
    """状态机非法迁移 (e.g. approve 已 denied request)."""


# ── 数据 ─────────────────────────────────────────────────────────────────────


@dataclass
class LendRequest:
    """Alice → Bob 借资源的请求 (本机 Bob 视角写在本机 lend.db)."""

    borrower_did: str
    lender_did: str
    resource_type: str   # llm_quota / ai_skill / compute
    amount: int
    model: str           # claude-opus-4-7 / gpt-5 / <skill-id>
    id: str = ""
    status: LendStatus = "pending"
    created_at: int = 0
    ttl_sec: int = DEFAULT_REQUEST_TTL_SEC
    decided_at: Optional[int] = None
    denied_reason: Optional[str] = None
    note: Optional[str] = None
    # per-request 模式 PWA 集成元数据
    mode: Literal["strong-tie-auto", "per-request", "emergency-only"] = "per-request"
    emergency_flag: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            self.id = "lr_" + uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = int(time.time())

    @property
    def expires_at(self) -> int:
        return self.created_at + self.ttl_sec

    def is_expired(self, now: int | None = None) -> bool:
        if self.status != "pending":
            return False
        return (now or int(time.time())) >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["expires_at"] = self.expires_at
        return d


# ── DB schema ────────────────────────────────────────────────────────────────


_LEND_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS lend_requests (
    id TEXT PRIMARY KEY,
    borrower_did TEXT NOT NULL,
    lender_did TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    amount INTEGER NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    ttl_sec INTEGER NOT NULL,
    decided_at INTEGER,
    denied_reason TEXT,
    note TEXT,
    mode TEXT NOT NULL DEFAULT 'per-request',
    emergency_flag INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_lend_status ON lend_requests(status);
CREATE INDEX IF NOT EXISTS idx_lend_borrower ON lend_requests(borrower_did);
"""


# ── LendStore ────────────────────────────────────────────────────────────────


class LendStore:
    """本机 lend.db 操作 (Bob 端). per-request 模式同时写 pending_lends.json 给 PWA poll."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        pending_file: Path | str | None = None,
    ) -> None:
        # 注: DEFAULT_* 在 import 时 freeze 自 Path.home(); 这里 lazy 重算
        # 以兼容 monkeypatch HOME (test isolation 必需).
        # P1 2026-06-10: 默认路径认 SISOUL_VAULT env — 多 vault 同机 (e.g.
        # 第二个 daemon 用 SISOUL_VAULT=… 起) 各用各的 lend.db, 不再串库.
        import os as _os
        _vault = Path(
            _os.environ.get("SISOUL_VAULT", str(Path.home() / ".sisoul"))
        ).expanduser()
        self.db_path = Path(db_path) if db_path else (_vault / "lend.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.pending_file = (
            Path(pending_file) if pending_file
            else (_vault / "pending_lends.json")
        )
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_LEND_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> "LendStore":
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()

    # ── 写 ──
    def request_lend(
        self,
        borrower_did: str,
        lender_did: str,
        resource_type: str,
        amount: int,
        model: str,
        *,
        mode: Literal["strong-tie-auto", "per-request", "emergency-only"] = "per-request",
        ttl_sec: int = DEFAULT_REQUEST_TTL_SEC,
        emergency_flag: bool = False,
        note: str | None = None,
    ) -> LendRequest:
        if not borrower_did or not lender_did:
            raise LendError("borrower_did / lender_did 不能为空")
        if borrower_did == lender_did:
            raise LendError("borrower 不能等于 lender")
        if amount < 0:
            raise LendError(f"amount 不能为负: {amount}")
        req = LendRequest(
            borrower_did=borrower_did,
            lender_did=lender_did,
            resource_type=resource_type,
            amount=int(amount),
            model=model,
            mode=mode,
            ttl_sec=ttl_sec,
            emergency_flag=emergency_flag,
            note=note,
        )
        # strong-tie-auto / emergency-only(+flag) → 直接 approve 不走 PWA 弹窗
        if mode == "strong-tie-auto":
            req.status = "approved"
            req.decided_at = int(time.time())
        elif mode == "emergency-only":
            if emergency_flag:
                req.status = "approved"
                req.decided_at = int(time.time())
            else:
                req.status = "denied"
                req.decided_at = int(time.time())
                req.denied_reason = "emergency-only 模式但无 emergency_flag"
        # per-request 维持 pending
        self._insert(req)
        if req.status == "pending":
            self._sync_pending_file()
        return req

    def approve_lend(self, request_id: str) -> LendRequest:
        req = self.get(request_id)
        if req.status == "approved":
            return req  # idempotent
        if req.status != "pending":
            raise RequestStateError(
                f"approve_lend: request {request_id} status={req.status}, 不允许 approve"
            )
        if req.is_expired():
            self._update_status(req.id, "expired")
            req.status = "expired"
            raise RequestStateError(f"approve_lend: request {request_id} 已过期")
        now = int(time.time())
        self._conn.execute(
            "UPDATE lend_requests SET status='approved', decided_at=? WHERE id=?",
            (now, request_id),
        )
        self._conn.commit()
        req.status = "approved"
        req.decided_at = now
        self._sync_pending_file()
        return req

    def deny_lend(self, request_id: str, reason: str | None = None) -> LendRequest:
        req = self.get(request_id)
        if req.status == "denied":
            return req
        if req.status != "pending":
            raise RequestStateError(
                f"deny_lend: request {request_id} status={req.status}, 不允许 deny"
            )
        now = int(time.time())
        self._conn.execute(
            "UPDATE lend_requests SET status='denied', decided_at=?, denied_reason=? "
            "WHERE id=?",
            (now, reason, request_id),
        )
        self._conn.commit()
        req.status = "denied"
        req.decided_at = now
        req.denied_reason = reason
        self._sync_pending_file()
        return req

    def mark_completed(self, request_id: str) -> LendRequest:
        """borrow 流程结束 (ledger 写完) 标 completed."""
        req = self.get(request_id)
        if req.status not in ("approved",):
            raise RequestStateError(
                f"mark_completed: request {request_id} status={req.status}, 必 approved"
            )
        self._conn.execute(
            "UPDATE lend_requests SET status='completed' WHERE id=?", (request_id,)
        )
        self._conn.commit()
        req.status = "completed"
        return req

    def expire_stale(self, now: int | None = None) -> int:
        """扫所有 pending, 过期的标 expired. 返过期条数."""
        now = now or int(time.time())
        rows = self._conn.execute(
            "SELECT id, created_at, ttl_sec FROM lend_requests WHERE status='pending'"
        ).fetchall()
        n = 0
        for r in rows:
            if now >= int(r["created_at"]) + int(r["ttl_sec"]):
                self._conn.execute(
                    "UPDATE lend_requests SET status='expired', decided_at=? WHERE id=?",
                    (now, r["id"]),
                )
                n += 1
        if n:
            self._conn.commit()
            self._sync_pending_file()
        return n

    # ── 读 ──
    def get(self, request_id: str) -> LendRequest:
        r = self._conn.execute(
            "SELECT * FROM lend_requests WHERE id=?", (request_id,)
        ).fetchone()
        if not r:
            raise RequestNotFoundError(f"lend request {request_id} 不存在")
        return self._row_to_req(r)

    def list_pending(self) -> list[LendRequest]:
        rows = self._conn.execute(
            "SELECT * FROM lend_requests WHERE status='pending' ORDER BY created_at ASC"
        ).fetchall()
        return [self._row_to_req(r) for r in rows]

    def list_all(self, *, limit: int = 200, status: str | None = None) -> list[LendRequest]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM lend_requests WHERE status=? "
                "ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM lend_requests ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_req(r) for r in rows]

    # ── 内部 ──
    def _insert(self, req: LendRequest) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO lend_requests "
            "(id, borrower_did, lender_did, resource_type, amount, model, status, "
            " created_at, ttl_sec, decided_at, denied_reason, note, mode, emergency_flag) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                req.id,
                req.borrower_did,
                req.lender_did,
                req.resource_type,
                req.amount,
                req.model,
                req.status,
                req.created_at,
                req.ttl_sec,
                req.decided_at,
                req.denied_reason,
                req.note,
                req.mode,
                1 if req.emergency_flag else 0,
            ),
        )
        self._conn.commit()

    def _update_status(self, request_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE lend_requests SET status=?, decided_at=? WHERE id=?",
            (status, int(time.time()), request_id),
        )
        self._conn.commit()

    def _sync_pending_file(self) -> None:
        """atomic write pending_lends.json 给 PWA poll (no daemon overhead)."""
        try:
            self.pending_file.parent.mkdir(parents=True, exist_ok=True)
            pending = [r.to_dict() for r in self.list_pending()]
            payload = {
                "updated_at": int(time.time()),
                "count": len(pending),
                "pending": pending,
            }
            # atomic: tmp file + os.replace
            fd, tmp = tempfile.mkstemp(
                prefix=".pending_lends.", suffix=".json",
                dir=str(self.pending_file.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.pending_file)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
        except Exception:
            # fail-open: 写文件失败不影响 lend DB 状态
            pass

    @staticmethod
    def _row_to_req(r: sqlite3.Row) -> LendRequest:
        return LendRequest(
            id=r["id"],
            borrower_did=r["borrower_did"],
            lender_did=r["lender_did"],
            resource_type=r["resource_type"],
            amount=int(r["amount"]),
            model=r["model"],
            status=r["status"],
            created_at=int(r["created_at"]),
            ttl_sec=int(r["ttl_sec"]),
            decided_at=int(r["decided_at"]) if r["decided_at"] is not None else None,
            denied_reason=r["denied_reason"],
            note=r["note"],
            mode=r["mode"] if r["mode"] in (
                "strong-tie-auto", "per-request", "emergency-only"
            ) else "per-request",
            emergency_flag=bool(r["emergency_flag"]),
        )


# ── 顶层便捷函数 (cli / daemon 用) ───────────────────────────────────────────


def request_lend(
    borrower_did: str,
    lender_did: str,
    resource_type: str,
    amount: int,
    model: str,
    *,
    mode: Literal["strong-tie-auto", "per-request", "emergency-only"] = "per-request",
    ttl_sec: int = DEFAULT_REQUEST_TTL_SEC,
    emergency_flag: bool = False,
    db_path: Path | str | None = None,
    pending_file: Path | str | None = None,
) -> LendRequest:
    with LendStore(db_path=db_path, pending_file=pending_file) as store:
        return store.request_lend(
            borrower_did=borrower_did,
            lender_did=lender_did,
            resource_type=resource_type,
            amount=amount,
            model=model,
            mode=mode,
            ttl_sec=ttl_sec,
            emergency_flag=emergency_flag,
        )


def approve_lend(
    request_id: str,
    *,
    db_path: Path | str | None = None,
    pending_file: Path | str | None = None,
) -> LendRequest:
    with LendStore(db_path=db_path, pending_file=pending_file) as store:
        return store.approve_lend(request_id)


def deny_lend(
    request_id: str,
    reason: str | None = None,
    *,
    db_path: Path | str | None = None,
    pending_file: Path | str | None = None,
) -> LendRequest:
    with LendStore(db_path=db_path, pending_file=pending_file) as store:
        return store.deny_lend(request_id, reason=reason)


def list_pending_requests(
    *,
    db_path: Path | str | None = None,
    pending_file: Path | str | None = None,
) -> list[LendRequest]:
    with LendStore(db_path=db_path, pending_file=pending_file) as store:
        store.expire_stale()
        return store.list_pending()


__all__ = [
    # 常量
    "DEFAULT_LEND_DB",
    "DEFAULT_PENDING_LENDS_FILE",
    "DEFAULT_REQUEST_TTL_SEC",
    # 类型
    "LendStatus",
    # 异常
    "LendError",
    "RequestNotFoundError",
    "RequestStateError",
    # 数据
    "LendRequest",
    # 主类
    "LendStore",
    # 便捷
    "request_lend",
    "approve_lend",
    "deny_lend",
    "list_pending_requests",
]
