"""sisoul friend · skill borrow lifecycle (Phase 4 W70-W74 · 波 6 dev-A).

§28 §3.6 AI 技能 share 完整流程:

    $ sisoul skill borrow bob.sisoul.eth:solidity-expert --duration 30min
    > 检查 Bob 授权: per-request 模式, 弹窗给 Bob
    > Bob 点 "是"
    > Bob daemon 把 skill package 加密 + IPFS pin (24h)
    > 加密 key 经 Alice 公钥加密传 Alice daemon
    > Alice daemon decrypt + load skill 到 sisoul session (tmp dir)
    > ✅ borrowed session 启动 (30min)
    > ... Alice 用 skill 聊 30 分钟 ...
    > 30 分钟到 session 自动 destroy:
    >   wipe tmp dir + 内存清零 + IPFS unpin + 写互惠 ledger

# 跟 LLM quota share (波 5 dev-D borrow.py) 的区别

| | LLM quota share | AI skill share (本模块) |
|---|---|---|
| owner 让出什么 | LLM API quota | skill package (system prompt+examples+...) |
| LLM key 谁的 | Bob 的 (经 dev-B encrypted_proxy) | Alice 自己的 |
| owner 是否看到 prompt | 走 dev-B 端到端加密, 不看到 | 不参与 borrower 的 chat, 完全不看到 |
| ledger 记法 | 每条 chat (tokens) | 借入一次 1 条 (resource_type='ai_skill') |
| lifecycle | 单条 chat 完成即结束 | 30min session, 自动 destroy |

# wipe 隐私铁律

end_skill_borrow_session() 必须 (重要程度从高到低):

1. 删 ``local_decrypted_path`` 整个 tmp dir (skill 解密后 contents 落地的目录).
2. 从内存 ``_ACTIVE_SESSIONS`` 移除 session 对象, 让 GC 回收 SkillPackage 引用.
3. IPFS unpin (best-effort, 失败记 errors 不阻塞).
4. ledger record_usage(direction='borrow', resource_type='ai_skill', amount=1).

# 30min auto destroy 实现

- ``SkillBorrowSession`` 落 ``~/.sisoul/skill_borrow.db`` (跨进程持久).
- 内存 ``_ACTIVE_SESSIONS`` cache 真活的 SkillPackage 引用 (单进程).
- daemon scheduler 定期跑 ``auto_destroy_expired_sessions()`` 扫所有 expires_at <= now
  的 active session, 调 end_skill_borrow_session(reason='auto-expired').
- CLI / HTTP 主动调 end_skill_borrow_session(reason='manual') 也走同路径.

# 模块边界 (波 6 dev-A)

- 本文件: SkillBorrowSession + request_borrow_skill + end_skill_borrow_session +
  auto_destroy_expired_sessions + skill chat helper
- 不动其他 dev-* 文件
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from sisoul.friend.skill_ipfs import (
    SkillIPFSClient,
    SkillIPFSError,
    SkillPinRecord,
    fetch_skill_from_ipfs,
    pin_skill_to_ipfs,
)
from sisoul.friend.skill_package import (
    InvalidSkillPackageError,
    SkillPackage,
    SkillPackageDecryptError,
    decrypt_skill_package,
    encrypt_skill_package,
    parse_qualified_name,
)

logger = logging.getLogger(__name__)


# ── 常量 ─────────────────────────────────────────────────────────────────────


# 默认 borrow session lifecycle (§28 §3.6 "30min 默认").
DEFAULT_BORROW_DURATION_MINUTES = 30
MIN_BORROW_DURATION_MINUTES = 1
MAX_BORROW_DURATION_MINUTES = 24 * 60  # 24h

# 本地 session DB.
DEFAULT_SKILL_BORROW_DB = Path.home() / ".sisoul" / "skill_borrow.db"

# 解密后 skill contents tmp dir 根 (每 session 一个子 dir).
DEFAULT_SKILL_TMP_ROOT = Path(tempfile.gettempdir()) / "sisoul-skill-borrow"

# scheduler 默认扫描间隔 (秒).
DEFAULT_AUTO_DESTROY_SCAN_INTERVAL_SEC = 30

SessionStatus = Literal["active", "expired", "destroyed", "failed"]


# ── 异常 ─────────────────────────────────────────────────────────────────────


class SkillBorrowError(Exception):
    """skill borrow 通用异常."""


class SkillBorrowPermissionError(SkillBorrowError):
    """权限 (friend / permissions / mode) 拒绝."""


class SkillBorrowSessionNotFoundError(SkillBorrowError):
    """session_id 不在 DB / 内存."""


class SkillBorrowExpiredError(SkillBorrowError):
    """session 已过期."""


# ── 数据 ─────────────────────────────────────────────────────────────────────


@dataclass
class SkillBorrowSession:
    """一次 skill borrow 完整状态."""

    session_id: str
    skill_id: str  # 不含 owner_did 的纯 name (e.g. "solidity-expert")
    borrower_did: str
    owner_did: str
    started_at: int
    expires_at: int
    status: SessionStatus = "active"
    ipfs_cid: Optional[str] = None
    local_decrypted_path: Optional[str] = None  # tmp dir str
    qualified_name: str = ""
    duration_minutes: int = DEFAULT_BORROW_DURATION_MINUTES
    destroyed_at: Optional[int] = None
    destroy_reason: Optional[str] = None
    ledger_entry_id: Optional[str] = None
    note: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.session_id:
            self.session_id = "sk_" + uuid.uuid4().hex[:12]
        if not self.qualified_name:
            self.qualified_name = f"{self.owner_did}:{self.skill_id}"

    def is_expired(self, now: int | None = None) -> bool:
        n = now or int(time.time())
        return n >= self.expires_at

    def remaining_seconds(self, now: int | None = None) -> int:
        n = now or int(time.time())
        return max(0, self.expires_at - n)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── 本地 DB ─────────────────────────────────────────────────────────────────


_SKILL_BORROW_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS skill_borrow_sessions (
    session_id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    borrower_did TEXT NOT NULL,
    owner_did TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    duration_minutes INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    ipfs_cid TEXT,
    local_decrypted_path TEXT,
    destroyed_at INTEGER,
    destroy_reason TEXT,
    ledger_entry_id TEXT,
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_skill_borrow_status ON skill_borrow_sessions(status);
CREATE INDEX IF NOT EXISTS idx_skill_borrow_expires ON skill_borrow_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_skill_borrow_borrower ON skill_borrow_sessions(borrower_did);
CREATE INDEX IF NOT EXISTS idx_skill_borrow_owner ON skill_borrow_sessions(owner_did);
"""


class SkillBorrowDB:
    """本地 SQLite skill borrow sessions store."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self.db_path = (
            Path(db_path) if db_path else (Path.home() / ".sisoul" / "skill_borrow.db")
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SKILL_BORROW_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> SkillBorrowDB:
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()

    def upsert(self, s: SkillBorrowSession) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO skill_borrow_sessions "
            "(session_id, skill_id, borrower_did, owner_did, qualified_name, "
            " started_at, expires_at, duration_minutes, status, ipfs_cid, "
            " local_decrypted_path, destroyed_at, destroy_reason, ledger_entry_id, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                s.session_id,
                s.skill_id,
                s.borrower_did,
                s.owner_did,
                s.qualified_name,
                s.started_at,
                s.expires_at,
                s.duration_minutes,
                s.status,
                s.ipfs_cid,
                s.local_decrypted_path,
                s.destroyed_at,
                s.destroy_reason,
                s.ledger_entry_id,
                s.note,
            ),
        )
        self._conn.commit()

    def get(self, session_id: str) -> Optional[SkillBorrowSession]:
        r = self._conn.execute(
            "SELECT * FROM skill_borrow_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        return self._row_to_session(r) if r else None

    def list_active(
        self,
        *,
        borrower_did: Optional[str] = None,
        owner_did: Optional[str] = None,
        limit: int = 200,
    ) -> list[SkillBorrowSession]:
        clauses = ["status='active'"]
        params: list[Any] = []
        if borrower_did:
            clauses.append("borrower_did=?")
            params.append(borrower_did)
        if owner_did:
            clauses.append("owner_did=?")
            params.append(owner_did)
        where = " WHERE " + " AND ".join(clauses)
        q = (
            f"SELECT * FROM skill_borrow_sessions{where} "
            f"ORDER BY started_at DESC LIMIT ?"
        )
        params.append(limit)
        rows = self._conn.execute(q, params).fetchall()
        return [self._row_to_session(r) for r in rows]

    def list_expired_active(self, now: int | None = None) -> list[SkillBorrowSession]:
        n = now or int(time.time())
        rows = self._conn.execute(
            "SELECT * FROM skill_borrow_sessions WHERE status='active' AND expires_at<=? "
            "ORDER BY expires_at ASC",
            (n,),
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in ("active", "expired", "destroyed", "failed"):
            n = self._conn.execute(
                "SELECT COUNT(*) FROM skill_borrow_sessions WHERE status=?", (s,)
            ).fetchone()[0]
            out[s] = int(n)
        out["total"] = int(
            self._conn.execute(
                "SELECT COUNT(*) FROM skill_borrow_sessions"
            ).fetchone()[0]
        )
        return out

    @staticmethod
    def _row_to_session(r: sqlite3.Row) -> SkillBorrowSession:
        return SkillBorrowSession(
            session_id=r["session_id"],
            skill_id=r["skill_id"],
            borrower_did=r["borrower_did"],
            owner_did=r["owner_did"],
            qualified_name=r["qualified_name"],
            started_at=int(r["started_at"]),
            expires_at=int(r["expires_at"]),
            duration_minutes=int(r["duration_minutes"]),
            status=r["status"],
            ipfs_cid=r["ipfs_cid"],
            local_decrypted_path=r["local_decrypted_path"],
            destroyed_at=int(r["destroyed_at"]) if r["destroyed_at"] is not None else None,
            destroy_reason=r["destroy_reason"],
            ledger_entry_id=r["ledger_entry_id"],
            note=r["note"],
        )


# ── 内存活动 sessions cache (持 SkillPackage 引用, 不持久) ───────────────────


# session_id → (SkillBorrowSession, SkillPackage 解密对象). 单进程 cache.
_ACTIVE_SESSIONS: dict[str, tuple[SkillBorrowSession, SkillPackage]] = {}


def _stash_active(session: SkillBorrowSession, pkg: SkillPackage) -> None:
    _ACTIVE_SESSIONS[session.session_id] = (session, pkg)


def _pop_active(session_id: str) -> Optional[tuple[SkillBorrowSession, SkillPackage]]:
    return _ACTIVE_SESSIONS.pop(session_id, None)


def get_active_skill_package(session_id: str) -> Optional[SkillPackage]:
    """daemon proxy-chat / CLI 调: 拿当前 session 的解密 SkillPackage."""
    item = _ACTIVE_SESSIONS.get(session_id)
    return item[1] if item else None


# ── 权限检查 (try dev-A relationship + dev-C permissions) ────────────────────


def _check_friend_and_permissions(
    borrower_did: str,
    owner_did: str,
    skill_id: str,
    duration_minutes: int,
    *,
    per_request_approved: bool = False,
    emergency_flag: bool = False,
    skip_permission_check: bool = False,
) -> tuple[bool, str]:
    """try dev-A friend mutual + dev-C permissions.check_permission(resource='ai_skill').

    fail-open 策略: dev-A/dev-C 不可用时记 warning 放行 (开发期 fallback).
    skip_permission_check=True 时直接放行 (test 用).
    """
    if skip_permission_check:
        return True, "skipped"

    # dev-A friend mutual check (best-effort)
    try:
        from sisoul.friend.relationship import FriendRelationship  # type: ignore[import-untyped]

        # FriendRelationship 以 own_did 视角看. 这里 borrower 视角: 看 owner 是不是朋友.
        rel = FriendRelationship(own_did=borrower_did)
        try:
            friend = rel.get_friend(owner_did)
            if friend.status == "revoked":
                return False, f"friend revoked: {owner_did}"
            # active / pending 都放行 (pending = 链上 attestation 未齐, 但本机视角 add 过)
        except Exception:
            # 没记 friend → fail-open 放行 (开发期; 真生产 should deny)
            logger.warning(
                "friend %s 不在本机 FriendDB; fail-open 放行 (开发期 fallback)",
                owner_did,
            )
    except Exception as e:
        logger.warning(
            "dev-A relationship 不可达 (%s: %s); fail-open 放行",
            type(e).__name__, e,
        )

    # dev-C permission check
    try:
        from sisoul.friend.permissions import check_permission  # type: ignore[import-untyped]
    except Exception as e:
        logger.warning(
            "dev-C permissions 不可达 (%s: %s); fail-open 放行",
            type(e).__name__, e,
        )
        return True, "permissions-unavailable-fail-open"

    try:
        allowed, reason = check_permission(
            borrower_did,
            "ai_skill",
            duration_minutes,
            model=skill_id,
            per_request_approved=per_request_approved,
            emergency_flag=emergency_flag,
        )
    except Exception as e:
        logger.warning(
            "check_permission raised (%s: %s); fail-open 放行 per-request-pending",
            type(e).__name__, e,
        )
        return False, f"check_permission_error:{type(e).__name__}"

    return bool(allowed), str(reason)


# ── ledger write (try dev-D ledger) ─────────────────────────────────────────


def _write_ledger_borrow_attestation(
    session: SkillBorrowSession,
    *,
    ledger_db: Optional[Path | str] = None,
    enqueue_onchain: bool = True,
) -> Optional[str]:
    """borrow 完成时写 1 条 ledger entry (resource_type='ai_skill', amount=1).

    返 entry_id 或 None (dev-D ledger 不可用时).
    """
    try:
        from sisoul.friend.ledger import ReciprocityLedger  # type: ignore[import-untyped]
    except Exception as e:
        logger.warning(
            "dev-D ledger 不可达 (%s: %s); 跳过 ledger 写入",
            type(e).__name__, e,
        )
        return None

    try:
        led = ReciprocityLedger(db_path=ledger_db, self_did=session.borrower_did)
        try:
            entry = led.record_usage(
                borrower_did=session.borrower_did,
                lender_did=session.owner_did,
                resource_type="ai_skill",
                amount=1,  # 1 次 skill borrow
                model_or_skill_id=session.skill_id,
                direction="borrow",
                note=f"skill_borrow:{session.session_id}:duration={session.duration_minutes}min",
                enqueue_onchain=enqueue_onchain,
            )
            return entry.entry_id
        finally:
            led.close()
    except Exception as e:
        logger.warning(
            "ledger record_usage 失败 (%s: %s); 不阻塞 borrow",
            type(e).__name__, e,
        )
        return None


# ── tmp dir 写入解密后 contents ──────────────────────────────────────────────


def _materialize_skill_to_tmp(
    pkg: SkillPackage, session_id: str, *, tmp_root: Optional[Path] = None
) -> Path:
    """把解密后 SkillPackage contents 写到 tmp dir, 让 borrower session 装载.

    布局:
        <tmp_root>/<session_id>/
            ├── package.json         # 完整 SkillPackage 元数据
            ├── system_prompt.md     # 主 system prompt
            ├── examples.json        # few_shot_examples_inline (大文件外引)
            ├── preferences.yaml     # preference_overlay
            └── tool_templates.json
    """
    root = Path(tmp_root) if tmp_root else DEFAULT_SKILL_TMP_ROOT
    session_dir = root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    # 全 package metadata
    (session_dir / "package.json").write_text(pkg.to_json(), encoding="utf-8")
    # system prompt 单独存
    (session_dir / "system_prompt.md").write_text(
        pkg.contents.system_prompt, encoding="utf-8"
    )
    if pkg.contents.few_shot_examples_inline:
        (session_dir / "examples.json").write_text(
            json.dumps(pkg.contents.few_shot_examples_inline, ensure_ascii=False),
            encoding="utf-8",
        )
    if pkg.contents.preference_overlay:
        (session_dir / "preferences.json").write_text(
            json.dumps(pkg.contents.preference_overlay, ensure_ascii=False),
            encoding="utf-8",
        )
    if pkg.contents.tool_call_templates:
        (session_dir / "tool_templates.json").write_text(
            json.dumps(pkg.contents.tool_call_templates, ensure_ascii=False),
            encoding="utf-8",
        )
    return session_dir


def _wipe_tmp_dir(path: Optional[str]) -> bool:
    """覆盖文件后 unlink. best-effort 防 forensic 简单回收.

    Python 没法保证擦真物理 disk block (FS journal / SSD wear leveling 复杂),
    但 truncate + 0 覆盖 + rmtree 是普通用户场景的合理做法.
    """
    if not path:
        return True
    p = Path(path)
    if not p.exists():
        return True
    try:
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    try:
                        size = f.stat().st_size
                        with open(f, "r+b") as fh:
                            fh.write(b"\x00" * size)
                            fh.flush()
                            os.fsync(fh.fileno())
                    except (OSError, PermissionError):
                        pass
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                size = p.stat().st_size
                with open(p, "r+b") as fh:
                    fh.write(b"\x00" * size)
                    fh.flush()
                    os.fsync(fh.fileno())
            except (OSError, PermissionError):
                pass
            p.unlink(missing_ok=True)
        return True
    except Exception as e:
        logger.warning("_wipe_tmp_dir 失败 %s: %s", p, e)
        return False


# ── 核心: request_borrow_skill ───────────────────────────────────────────────


@dataclass
class BorrowRequestResult:
    """borrow 完整请求结果 (CLI / HTTP 返)."""

    session: SkillBorrowSession
    skill_package_fingerprint: str
    permission_reason: str
    used_fallback: bool = False  # True = 走 try/except fallback 而非真 friend path
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["session"] = self.session.to_dict()
        return d


def request_borrow_skill(
    owner_did: str,
    skill_id: str,
    *,
    borrower_did: str,
    duration_minutes: int = DEFAULT_BORROW_DURATION_MINUTES,
    encrypted_skill_provider: Optional[Callable[[str, str], tuple[bytes, str]]] = None,
    decrypt_callback: Optional[Callable[[bytes], SkillPackage]] = None,
    per_request_approved: bool = False,
    emergency_flag: bool = False,
    skip_permission_check: bool = False,
    duration_seconds_override: Optional[int] = None,
    db_path: Optional[Path | str] = None,
    tmp_root: Optional[Path] = None,
    ledger_db: Optional[Path | str] = None,
    enqueue_onchain: bool = True,
    pinata_jwt: Optional[str] = None,
    pin_db_path: Optional[Path | str] = None,
) -> BorrowRequestResult:
    """完整 borrow lifecycle 起点.

    流程:
      1. 检查 friend 关系 + permissions (try/except fail-open)
      2. owner 端 provides encrypted package + IPFS CID
         (encrypted_skill_provider callback, 真生产经 P2P 调 owner daemon /sisoul/skill/lend)
      3. borrower 用自己 priv key + owner pub key decrypt (decrypt_callback)
      4. 解密的 SkillPackage 写 tmp dir + 缓存到 _ACTIVE_SESSIONS
      5. 落 session 到 DB
      6. 30min 后 auto_destroy_expired_sessions() 清

    encrypted_skill_provider: 测试用. 签名 (owner_did, skill_id) -> (encrypted_bytes, ipfs_cid).
      None 时 raise (真生产由 daemon route 注入, 走 owner P2P + IPFS).

    decrypt_callback: 测试用. 签名 (encrypted_bytes) -> SkillPackage.
      None 时跳过解密 (raise; 真生产由 daemon 注入 borrower priv + owner pub).

    duration_seconds_override: test 用 (传 5 表示 5 秒 expire, 不用真等 30 分钟).
    """
    if not borrower_did:
        raise SkillBorrowError("borrower_did 必填")
    if not owner_did:
        raise SkillBorrowError("owner_did 必填")
    if not skill_id:
        raise SkillBorrowError("skill_id 必填")
    if duration_minutes < MIN_BORROW_DURATION_MINUTES or duration_minutes > MAX_BORROW_DURATION_MINUTES:
        raise SkillBorrowError(
            f"duration_minutes 必须 [{MIN_BORROW_DURATION_MINUTES}, "
            f"{MAX_BORROW_DURATION_MINUTES}], got {duration_minutes}"
        )

    # 1. permission check
    allowed, reason = _check_friend_and_permissions(
        borrower_did=borrower_did,
        owner_did=owner_did,
        skill_id=skill_id,
        duration_minutes=duration_minutes,
        per_request_approved=per_request_approved,
        emergency_flag=emergency_flag,
        skip_permission_check=skip_permission_check,
    )
    if not allowed:
        raise SkillBorrowPermissionError(
            f"borrow denied: {reason} (borrower={borrower_did} skill={owner_did}:{skill_id})"
        )

    # 2. provider 提供 encrypted blob + CID (真生产经 P2P 跟 owner daemon 协商)
    if encrypted_skill_provider is None:
        raise SkillBorrowError(
            "encrypted_skill_provider 必填 (真生产由 daemon route 注入 P2P provider; "
            "test 注入 mock)."
        )
    try:
        encrypted_bytes, ipfs_cid = encrypted_skill_provider(owner_did, skill_id)
    except Exception as e:
        raise SkillBorrowError(
            f"encrypted_skill_provider 失败 ({type(e).__name__}): {e}"
        ) from e

    # 3. 解密
    if decrypt_callback is None:
        raise SkillBorrowError(
            "decrypt_callback 必填 (真生产由 daemon 注入 borrower priv + owner pub; "
            "test 注入 mock)."
        )
    try:
        pkg = decrypt_callback(encrypted_bytes)
    except (SkillPackageDecryptError, InvalidSkillPackageError) as e:
        raise SkillBorrowError(f"skill 解密失败: {type(e).__name__}: {e}") from e

    # 4. 写 tmp dir
    now = int(time.time())
    expire_sec = (
        duration_seconds_override
        if duration_seconds_override is not None
        else int(duration_minutes) * 60
    )
    session = SkillBorrowSession(
        session_id="",  # __post_init__ 生
        skill_id=skill_id,
        borrower_did=borrower_did,
        owner_did=owner_did,
        started_at=now,
        expires_at=now + int(expire_sec),
        duration_minutes=int(duration_minutes),
        status="active",
        ipfs_cid=ipfs_cid,
    )
    tmp_dir = _materialize_skill_to_tmp(pkg, session.session_id, tmp_root=tmp_root)
    session.local_decrypted_path = str(tmp_dir)

    # 5. cache + DB
    _stash_active(session, pkg)
    with SkillBorrowDB(db_path=db_path) as db:
        db.upsert(session)

    return BorrowRequestResult(
        session=session,
        skill_package_fingerprint=pkg.fingerprint,
        permission_reason=reason,
        used_fallback=("fail-open" in reason or "unavailable" in reason or "skipped" in reason),
    )


# ── 核心: end_skill_borrow_session ───────────────────────────────────────────


def end_skill_borrow_session(
    session_id: str,
    *,
    reason: str = "manual",
    db_path: Optional[Path | str] = None,
    pinata_jwt: Optional[str] = None,
    pin_db_path: Optional[Path | str] = None,
    ledger_db: Optional[Path | str] = None,
    enqueue_onchain: bool = True,
    unpin_ipfs: bool = True,
    write_ledger: bool = True,
) -> SkillBorrowSession:
    """主动结束 borrow session.

    顺序:
      1. wipe 本地解密 tmp dir + 内存清零
      2. 从 _ACTIVE_SESSIONS 移除 (release SkillPackage 引用让 GC)
      3. IPFS unpin (best-effort)
      4. 写 ledger attestation
      5. 标 session.status='destroyed' 落 DB

    返更新后的 SkillBorrowSession.
    """
    with SkillBorrowDB(db_path=db_path) as db:
        session = db.get(session_id)
        if not session:
            # 从内存 cache 看
            cached = _ACTIVE_SESSIONS.get(session_id)
            if cached:
                session = cached[0]
            else:
                raise SkillBorrowSessionNotFoundError(
                    f"session {session_id} 不在 DB / 内存"
                )

        if session.status == "destroyed":
            return session  # 幂等

        now = int(time.time())

        # 1. wipe tmp dir
        wipe_ok = _wipe_tmp_dir(session.local_decrypted_path)
        if not wipe_ok:
            logger.warning(
                "session %s tmp dir wipe 失败 (path=%s); 仍继续 destroy",
                session_id, session.local_decrypted_path,
            )

        # 2. 内存清零
        _pop_active(session_id)

        # 3. IPFS unpin (best-effort)
        if unpin_ipfs and session.ipfs_cid:
            try:
                client = SkillIPFSClient(pinata_jwt=pinata_jwt, db_path=pin_db_path)
                client.unpin(session.ipfs_cid)
            except SkillIPFSError as e:
                logger.warning(
                    "session %s IPFS unpin 失败 (cid=%s): %s",
                    session_id, session.ipfs_cid, e,
                )

        # 4. ledger
        if write_ledger and not session.ledger_entry_id:
            entry_id = _write_ledger_borrow_attestation(
                session,
                ledger_db=ledger_db,
                enqueue_onchain=enqueue_onchain,
            )
            session.ledger_entry_id = entry_id

        # 5. 标 destroyed
        session.status = "destroyed"
        session.destroyed_at = now
        session.destroy_reason = reason
        db.upsert(session)

    return session


# ── auto-destroy scheduler ──────────────────────────────────────────────────


def auto_destroy_expired_sessions(
    *,
    db_path: Optional[Path | str] = None,
    pinata_jwt: Optional[str] = None,
    pin_db_path: Optional[Path | str] = None,
    ledger_db: Optional[Path | str] = None,
    enqueue_onchain: bool = True,
    now: int | None = None,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    """daemon scheduler 入口: 扫所有 expires_at <= now 的 active session, 调 end_skill_borrow_session.

    Returns:
        {"scanned": N, "destroyed": M, "errors": [(session_id, err)...]}
    """
    out: dict[str, Any] = {"scanned": 0, "destroyed": 0, "errors": []}
    with SkillBorrowDB(db_path=db_path) as db:
        expired = db.list_expired_active(now=now)
    out["scanned"] = len(expired)
    for s in expired:
        try:
            end_skill_borrow_session(
                s.session_id,
                reason="auto-expired",
                db_path=db_path,
                pinata_jwt=pinata_jwt,
                pin_db_path=pin_db_path,
                ledger_db=ledger_db,
                enqueue_onchain=enqueue_onchain,
            )
            out["destroyed"] += 1
        except SkillBorrowError as e:
            out["errors"].append((s.session_id, str(e)))
            if raise_on_error:
                raise
    return out


# ── 列表 / 查询 ─────────────────────────────────────────────────────────────


def list_borrow_sessions(
    *,
    borrower_did: Optional[str] = None,
    owner_did: Optional[str] = None,
    db_path: Optional[Path | str] = None,
    only_active: bool = True,
    limit: int = 200,
) -> list[SkillBorrowSession]:
    with SkillBorrowDB(db_path=db_path) as db:
        if only_active:
            return db.list_active(borrower_did=borrower_did, owner_did=owner_did, limit=limit)
        # 全表
        params: list[Any] = []
        clauses: list[str] = []
        if borrower_did:
            clauses.append("borrower_did=?")
            params.append(borrower_did)
        if owner_did:
            clauses.append("owner_did=?")
            params.append(owner_did)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = db._conn.execute(  # noqa: SLF001
            f"SELECT * FROM skill_borrow_sessions{where} "
            f"ORDER BY started_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [SkillBorrowDB._row_to_session(r) for r in rows]


def get_borrow_session(
    session_id: str,
    *,
    db_path: Optional[Path | str] = None,
) -> Optional[SkillBorrowSession]:
    with SkillBorrowDB(db_path=db_path) as db:
        return db.get(session_id)


# ── skill chat via dev-B encrypted_proxy ────────────────────────────────────


def proxy_skill_chat(
    session_id: str,
    prompt: str,
    *,
    model: Optional[str] = None,
    db_path: Optional[Path | str] = None,
    forwarder: Optional[Callable[..., tuple[str, int, int]]] = None,
    llm_api_key: Optional[str] = None,
    provider: str = "anthropic",
    **llm_kwargs: Any,
) -> dict[str, Any]:
    """borrower 用 skill 跟 LLM 聊一轮.

    重要: 跟 LLM quota share 不同, borrower 用**自己**的 LLM key (不消耗 owner quota).
    走 sisoul.llm 适配器 (或注入 mock forwarder).

    流程:
      1. 拿 session + 解密的 SkillPackage (内存 _ACTIVE_SESSIONS)
      2. 组装 effective prompt = pkg.contents.system_prompt + few_shot_examples + 用户 prompt
      3. 调 LLM (forwarder 或默认 sisoul.llm.get_adapter)
      4. 返回 response (不写 owner 的 ledger; owner 不消耗 quota)

    Returns:
        {"text": "...", "tokens_used": N, "model_used": "...", "session_id": ..., "session_remaining_sec": M}

    Raises:
        SkillBorrowSessionNotFoundError: session 不在内存 (可能已 destroyed)
        SkillBorrowExpiredError: session.expires_at <= now
    """
    item = _ACTIVE_SESSIONS.get(session_id)
    if item is None:
        # 看 DB 给更具体的错
        session_db = get_borrow_session(session_id, db_path=db_path)
        if session_db is None:
            raise SkillBorrowSessionNotFoundError(
                f"session {session_id} 不在内存/DB"
            )
        if session_db.status == "destroyed":
            raise SkillBorrowSessionNotFoundError(
                f"session {session_id} 已 destroyed (reason={session_db.destroy_reason})"
            )
        raise SkillBorrowSessionNotFoundError(
            f"session {session_id} 不在内存 (status={session_db.status}); "
            "可能 daemon 重启? 重新 borrow."
        )

    session, pkg = item
    if session.is_expired():
        raise SkillBorrowExpiredError(
            f"session {session_id} 已过期 (expires_at={session.expires_at})"
        )

    # 选 model: 显式 > recommended_models 第一个 > "claude-opus-4-7"
    chosen_model = (
        model
        or (pkg.contents.recommended_models[0] if pkg.contents.recommended_models else None)
        or "claude-opus-4-7"
    )

    # 组装 effective prompt (主要把 skill system_prompt 注入)
    system_prompt = pkg.contents.system_prompt
    examples_text = ""
    if pkg.contents.few_shot_examples_inline:
        # 简单 inline 渲染前 3 条 (避免炸 context)
        examples_text = "\n\n[few-shot examples]\n" + "\n".join(
            json.dumps(e, ensure_ascii=False)
            for e in pkg.contents.few_shot_examples_inline[:3]
        )
    effective_prompt = (
        f"[system]\n{system_prompt}{examples_text}\n\n[user]\n{prompt}"
    )

    # 调 LLM
    if forwarder is None:
        # 默认走 sisoul.llm
        try:
            from sisoul.llm import get_adapter  # type: ignore[import-untyped]
            adapter = get_adapter(provider, api_key=llm_api_key, model=chosen_model)
            messages = [{"role": "user", "content": effective_prompt}]
            response_text = adapter.chat(messages, **llm_kwargs)
            prompt_tok = max(1, len(effective_prompt) // 4)
            response_tok = max(1, len(response_text) // 4)
        except Exception as e:
            raise SkillBorrowError(
                f"LLM adapter 调用失败 ({type(e).__name__}): {e}"
            ) from e
    else:
        try:
            response_text, prompt_tok, response_tok = forwarder(
                prompt=effective_prompt,
                model=chosen_model,
                provider=provider,
                api_key=llm_api_key,
                **llm_kwargs,
            )
        except Exception as e:
            raise SkillBorrowError(
                f"forwarder 调用失败 ({type(e).__name__}): {e}"
            ) from e

    return {
        "text": response_text,
        "tokens_used": int(prompt_tok) + int(response_tok),
        "prompt_tokens": int(prompt_tok),
        "response_tokens": int(response_tok),
        "model_used": chosen_model,
        "session_id": session_id,
        "session_remaining_sec": session.remaining_seconds(),
        "skill_id": session.skill_id,
        "owner_did": session.owner_did,
    }


__all__ = [
    # 常量
    "DEFAULT_BORROW_DURATION_MINUTES",
    "MIN_BORROW_DURATION_MINUTES",
    "MAX_BORROW_DURATION_MINUTES",
    "DEFAULT_SKILL_BORROW_DB",
    "DEFAULT_SKILL_TMP_ROOT",
    "DEFAULT_AUTO_DESTROY_SCAN_INTERVAL_SEC",
    "SessionStatus",
    # 异常
    "SkillBorrowError",
    "SkillBorrowPermissionError",
    "SkillBorrowSessionNotFoundError",
    "SkillBorrowExpiredError",
    # 数据
    "SkillBorrowSession",
    "SkillBorrowDB",
    "BorrowRequestResult",
    # 内存 cache
    "get_active_skill_package",
    # 核心
    "request_borrow_skill",
    "end_skill_borrow_session",
    "auto_destroy_expired_sessions",
    "list_borrow_sessions",
    "get_borrow_session",
    "proxy_skill_chat",
]
