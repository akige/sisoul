"""sisoul friend · AI 技能 IPFS 加密分发 (Phase 4 W70-W74 · 波 6 dev-A).

§28 §3.6 AI 技能 share 数据流第 4 步: Bob daemon 把 solidity-expert package 加密 +
IPFS pin (临时 hash, 24h 过期).

# Wave A-3 v1.0-decentralized 更新

§32 §B.3 决策: 砍 Pinata SaaS, 主路径换为内嵌 kubo IPFS 节点
(``sisoul.p2p.ipfs_kubo.IPFSKuboNode``), 朋友间互相 pin.

backend 选择:

- ``backend="kubo"``: 走 IPFSKuboNode (Wave A-3 default 目标).
- ``backend="pinata"``: 走原 Pinata HTTP API (legacy / backward-compat).
- ``backend="auto"`` (默认): env ``SISOUL_IPFS_BACKEND=kubo`` 时走 kubo,
  否则 Pinata (向后兼容现有测试 / 生产部署平滑迁移).

env ``SISOUL_IPFS_BACKEND`` 取值: ``kubo`` / ``pinata`` / ``auto`` (默认).

# 24h 过期实现 (Pinata legacy 路径)

Pinata 本身没有 "TTL auto-unpin" 概念. client 端实现:

1. pin 时调 ``POST /pinning/pinFileToIPFS`` 同时带 ``pinataMetadata.keyvalues.expires_at``
   = unix ts (Pinata 接 metadata 透传).
2. sisoul daemon scheduler 定期跑 ``unpin_expired_skills()`` 扫所有过期 CID 调
   ``DELETE /pinning/unpin/{cid}`` (本模块 export, daemon cron 5min 跑).
3. 本地 SQLite ``~/.sisoul/skill_pins.db`` 记本机 pin 过的 CID + expiry.

# 24h 过期实现 (kubo 路径)

kubo 也无 TTL, 同样靠本地 DB + scheduler. unpin 调 ``ipfs pin rm`` 而非 Pinata DELETE.

# 模块边界

- 本文件: SkillIPFSClient + pin_skill_to_ipfs + fetch_skill_from_ipfs +
  unpin_expired_skills + SkillPinDB + KuboBackend (Wave A-3)
- 不在本文件: SkillPackage 数据结构 (skill_package.py) / borrow lifecycle (skill_borrow.py)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


# ── 常量 ─────────────────────────────────────────────────────────────────────


# Pinata HTTP API base (跟 sisoul.onchain.arweave 一致).
PINATA_API_BASE = "https://api.pinata.cloud"

# IPFS gateway 兜底 (fetch 时按顺序试).
IPFS_GATEWAYS = (
    "https://gateway.pinata.cloud/ipfs/{cid}",
    "https://ipfs.io/ipfs/{cid}",
)

# 本地 pin 记录 DB.
DEFAULT_SKILL_PIN_DB = Path.home() / ".sisoul" / "skill_pins.db"

# scheduler 推荐扫描间隔 (秒). daemon cron 用.
DEFAULT_UNPIN_SCAN_INTERVAL_SEC = 5 * 60  # 5min

# Pinata pin 默认超时
DEFAULT_PIN_TIMEOUT_SEC = 30.0
DEFAULT_FETCH_TIMEOUT_SEC = 60.0
DEFAULT_UNPIN_TIMEOUT_SEC = 30.0


# ── 异常 ─────────────────────────────────────────────────────────────────────


class SkillIPFSError(Exception):
    """IPFS pin / fetch / unpin 通用异常."""


class SkillPinError(SkillIPFSError):
    """pin 操作失败 (Pinata API 错 / 网络)."""


class SkillFetchError(SkillIPFSError):
    """fetch 操作失败 (所有 gateway 都失败)."""


class SkillUnpinError(SkillIPFSError):
    """unpin 操作失败 (Pinata API 错 / cid 不存在)."""


# ── 数据 ─────────────────────────────────────────────────────────────────────


@dataclass
class SkillPinRecord:
    """单条 IPFS pin 记录 (~/.sisoul/skill_pins.db).

    用 owner_did + skill_id 索引 → 同一 skill 多版本可同时 pin.

    Wave A-3 新增 `backend` 字段区分 pinata / kubo / mock. `pinata_pinned`
    保留兼容现有测试, 含义扩展为 "已上 IPFS pin" (pinata 或 kubo).
    """

    cid: str
    owner_did: str
    skill_id: str
    pinned_at: int  # unix epoch
    expires_at: int  # unix epoch
    size_bytes: int = 0
    pinata_pinned: bool = True  # False = mock / 本地 only (test 用)
    unpinned: bool = False
    unpinned_at: Optional[int] = None
    note: Optional[str] = None
    backend: str = "pinata"  # "pinata" | "kubo" | "mock"  Wave A-3

    def is_expired(self, now: int | None = None) -> bool:
        n = now or int(time.time())
        return n >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── 本地 DB ─────────────────────────────────────────────────────────────────


_SKILL_PIN_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS skill_pins (
    cid TEXT PRIMARY KEY,
    owner_did TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    pinned_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    pinata_pinned INTEGER NOT NULL DEFAULT 1,
    unpinned INTEGER NOT NULL DEFAULT 0,
    unpinned_at INTEGER,
    note TEXT,
    backend TEXT NOT NULL DEFAULT 'pinata'
);

CREATE INDEX IF NOT EXISTS idx_skill_pins_owner ON skill_pins(owner_did);
CREATE INDEX IF NOT EXISTS idx_skill_pins_expires ON skill_pins(expires_at);
CREATE INDEX IF NOT EXISTS idx_skill_pins_unpinned ON skill_pins(unpinned);
"""

# Wave A-3: 旧 DB 迁移 (新增 backend 列, IF NOT EXISTS).
_SKILL_PIN_MIGRATE_SQL = [
    # SQLite < 3.35 不支持 IF NOT EXISTS, 用 try/except 包. ALTER TABLE 加默认列幂等失败 ok.
    "ALTER TABLE skill_pins ADD COLUMN backend TEXT NOT NULL DEFAULT 'pinata'",
]


class SkillPinDB:
    """本地 SQLite skill pin cache.

    使用模式:
        with SkillPinDB() as db:
            db.upsert(record)
            db.list_expired_active()
    """

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        # lazy 重算 home (兼容 monkeypatch HOME for tests, 跟 dev-D ledger 一致).
        self.db_path = (
            Path(db_path) if db_path else (Path.home() / ".sisoul" / "skill_pins.db")
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SKILL_PIN_SCHEMA_SQL)
        # Wave A-3 迁移: 旧 DB 没 backend 列, 加上. 已有 → ALTER 抛 OperationalError 忽略.
        for stmt in _SKILL_PIN_MIGRATE_SQL:
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # 列已存在
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> SkillPinDB:
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()

    def upsert(self, rec: SkillPinRecord) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO skill_pins "
            "(cid, owner_did, skill_id, pinned_at, expires_at, size_bytes, "
            " pinata_pinned, unpinned, unpinned_at, note, backend) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rec.cid,
                rec.owner_did,
                rec.skill_id,
                rec.pinned_at,
                rec.expires_at,
                rec.size_bytes,
                1 if rec.pinata_pinned else 0,
                1 if rec.unpinned else 0,
                rec.unpinned_at,
                rec.note,
                rec.backend or "pinata",
            ),
        )
        self._conn.commit()

    def get(self, cid: str) -> Optional[SkillPinRecord]:
        r = self._conn.execute(
            "SELECT * FROM skill_pins WHERE cid=?", (cid,)
        ).fetchone()
        return self._row_to_record(r) if r else None

    def mark_unpinned(self, cid: str, ts: int | None = None) -> None:
        self._conn.execute(
            "UPDATE skill_pins SET unpinned=1, unpinned_at=? WHERE cid=?",
            (ts or int(time.time()), cid),
        )
        self._conn.commit()

    def list_active(
        self, owner_did: Optional[str] = None, limit: int = 500
    ) -> list[SkillPinRecord]:
        if owner_did:
            rows = self._conn.execute(
                "SELECT * FROM skill_pins WHERE unpinned=0 AND owner_did=? "
                "ORDER BY pinned_at DESC LIMIT ?",
                (owner_did, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM skill_pins WHERE unpinned=0 "
                "ORDER BY pinned_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_expired_active(self, now: int | None = None) -> list[SkillPinRecord]:
        """过期但还没 unpin 的记录 (scheduler 用)."""
        n = now or int(time.time())
        rows = self._conn.execute(
            "SELECT * FROM skill_pins WHERE unpinned=0 AND expires_at<=? "
            "ORDER BY expires_at ASC",
            (n,),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        out["total"] = int(
            self._conn.execute("SELECT COUNT(*) FROM skill_pins").fetchone()[0]
        )
        out["active"] = int(
            self._conn.execute(
                "SELECT COUNT(*) FROM skill_pins WHERE unpinned=0"
            ).fetchone()[0]
        )
        out["unpinned"] = int(
            self._conn.execute(
                "SELECT COUNT(*) FROM skill_pins WHERE unpinned=1"
            ).fetchone()[0]
        )
        out["expired_active"] = int(
            self._conn.execute(
                "SELECT COUNT(*) FROM skill_pins WHERE unpinned=0 AND expires_at<=?",
                (int(time.time()),),
            ).fetchone()[0]
        )
        return out

    @staticmethod
    def _row_to_record(r: sqlite3.Row) -> SkillPinRecord:
        # backend 列 Wave A-3 加, 旧行可能没有, 容错
        try:
            backend = r["backend"] or "pinata"
        except (IndexError, KeyError):
            backend = "pinata"
        return SkillPinRecord(
            cid=r["cid"],
            owner_did=r["owner_did"],
            skill_id=r["skill_id"],
            pinned_at=int(r["pinned_at"]),
            expires_at=int(r["expires_at"]),
            size_bytes=int(r["size_bytes"]),
            pinata_pinned=bool(r["pinata_pinned"]),
            unpinned=bool(r["unpinned"]),
            unpinned_at=int(r["unpinned_at"]) if r["unpinned_at"] is not None else None,
            note=r["note"],
            backend=backend,
        )


# ── 客户端 ───────────────────────────────────────────────────────────────────


class SkillIPFSClient:
    """IPFS 客户端 (skill blob pin/fetch/unpin).

    Wave A-3: 支持双 backend.

    - ``backend="pinata"``: Pinata HTTP API (legacy).
    - ``backend="kubo"``: 内嵌 kubo 子进程 (推荐, 详 ``sisoul.p2p.ipfs_kubo``).
    - ``backend="auto"`` (默认): env ``SISOUL_IPFS_BACKEND=kubo`` 时走 kubo, 否则 pinata.

    pinata_jwt 优先级: 构造参数 > env ``PINATA_JWT`` > 走 mock (开发期 fallback,
    返 mockcid-<sha> 不真上 Pinata).
    """

    def __init__(
        self,
        pinata_jwt: Optional[str] = None,
        *,
        db_path: Optional[Path | str] = None,
        api_base: str = PINATA_API_BASE,
        gateways: tuple[str, ...] = IPFS_GATEWAYS,
        backend: str = "auto",
        kubo_node: Optional[Any] = None,
    ) -> None:
        self.pinata_jwt = pinata_jwt or os.environ.get("PINATA_JWT")
        self.api_base = api_base
        self.gateways = gateways
        self._db_path = Path(db_path) if db_path else None
        # Wave A-3: backend 选择
        if backend == "auto":
            env_backend = os.environ.get("SISOUL_IPFS_BACKEND", "pinata").strip().lower()
            backend = env_backend if env_backend in ("kubo", "pinata", "mock") else "pinata"
        if backend not in ("kubo", "pinata", "mock"):
            raise ValueError(f"backend 必须 kubo/pinata/mock, got {backend!r}")
        self.backend = backend
        self._kubo_node = kubo_node  # lazy import 避免循环

    def _db(self) -> SkillPinDB:
        return SkillPinDB(db_path=self._db_path)

    def _get_kubo_node(self) -> Any:
        """lazy 拿 IPFSKuboNode (Wave A-3). 避免循环 import."""
        if self._kubo_node is not None:
            return self._kubo_node
        from sisoul.p2p.ipfs_kubo import get_default_node
        self._kubo_node = get_default_node()
        return self._kubo_node

    def _kubo_pin(
        self,
        encrypted_skill_bytes: bytes,
        *,
        owner_did: str,
        skill_id: str,
        expires_at: int,
    ) -> tuple[str, bool]:
        """走 kubo 路径 pin. 返 (cid, pinned_bool).

        kubo 找不到 binary → 自动降 mock (sha-based cid + 本地 cache).
        """
        node = self._get_kubo_node()
        # mock 模式 (无 kubo binary): 自降 mockcid + cache
        if getattr(node, "mode", None) == "mock":
            cid = "mockcid-" + hashlib.sha256(encrypted_skill_bytes).hexdigest()[:46]
            _MOCK_BLOB_CACHE[cid] = bytes(encrypted_skill_bytes)
            return cid, False

        # 真 kubo subprocess: start lazy + add
        import asyncio as _aio
        if not node.is_running:
            try:
                _aio.run(node.start())
            except RuntimeError:
                # 已在 loop 中, 试 sync 入口
                node.start_sync()
        try:
            cid = _aio.run(node.add(bytes(encrypted_skill_bytes), pin=True))
        except (RuntimeError, Exception) as e:  # noqa: BLE001
            raise SkillPinError(f"kubo add 失败: {type(e).__name__}: {e}") from e
        return cid, True

    def _kubo_fetch(self, cid: str, *, timeout_sec: float) -> bytes:
        """走 kubo cat."""
        node = self._get_kubo_node()
        if getattr(node, "mode", None) == "mock":
            blob = _MOCK_BLOB_CACHE.get(cid)
            if blob is None:
                raise SkillFetchError(
                    f"kubo mock 模式: cid {cid} 不在 _MOCK_BLOB_CACHE"
                )
            return blob
        import asyncio as _aio
        if not node.is_running:
            try:
                _aio.run(node.start())
            except RuntimeError:
                node.start_sync()
        try:
            return _aio.run(node.cat(cid, timeout=timeout_sec))
        except Exception as e:  # noqa: BLE001
            raise SkillFetchError(f"kubo cat 失败: {type(e).__name__}: {e}") from e

    def _kubo_unpin(self, cid: str) -> bool:
        """走 kubo pin rm."""
        node = self._get_kubo_node()
        if getattr(node, "mode", None) == "mock":
            _MOCK_BLOB_CACHE.pop(cid, None)
            return True
        import asyncio as _aio
        if not node.is_running:
            try:
                _aio.run(node.start())
            except RuntimeError:
                node.start_sync()
        try:
            _aio.run(node.unpin(cid))
            return True
        except Exception as e:  # noqa: BLE001
            raise SkillUnpinError(f"kubo unpin 失败: {type(e).__name__}: {e}") from e

    # ── pin ────────────────────────────────────────────────────────────────

    def pin(
        self,
        encrypted_skill_bytes: bytes,
        *,
        owner_did: str,
        skill_id: str,
        expiry_hours: int = 24,
        filename: Optional[str] = None,
        note: Optional[str] = None,
        timeout_sec: float = DEFAULT_PIN_TIMEOUT_SEC,
    ) -> SkillPinRecord:
        """把加密 SkillPackage blob pin 到 IPFS.

        Args:
            encrypted_skill_bytes: encrypt_skill_package() 输出.
            owner_did: skill 训练 owner DID (写本地 DB 索引).
            skill_id: skill 名 (e.g. "solidity-expert").
            expiry_hours: 过期小时数 (默认 24, scheduler 到时 unpin).
            filename: 上传 multipart filename (Pinata UI 显示用).
            note: 备注 (本地 DB only, 不传 Pinata).
            timeout_sec: HTTP 超时.

        Returns:
            SkillPinRecord (含 cid + expires_at, 写入本地 DB).

        Raises:
            SkillPinError: 上 Pinata 失败 且 无 jwt mock fallback 不可用 (异常情况).
        """
        if not isinstance(encrypted_skill_bytes, (bytes, bytearray)):
            raise SkillPinError("encrypted_skill_bytes 必须 bytes")
        if not owner_did:
            raise SkillPinError("owner_did 必填")
        if not skill_id:
            raise SkillPinError("skill_id 必填")

        now = int(time.time())
        expires_at = now + int(expiry_hours) * 3600
        fname = filename or f"{skill_id}-{now}.skill.enc"
        size = len(encrypted_skill_bytes)

        cid: Optional[str] = None
        pinata_pinned = False

        # Wave A-3: kubo backend 路径
        if self.backend == "kubo":
            cid, pinned = self._kubo_pin(
                encrypted_skill_bytes,
                owner_did=owner_did,
                skill_id=skill_id,
                expires_at=expires_at,
            )
            pinata_pinned = pinned  # field 兼容: True = 真上 IPFS
            rec = SkillPinRecord(
                cid=cid,
                owner_did=owner_did,
                skill_id=skill_id,
                pinned_at=now,
                expires_at=expires_at,
                size_bytes=size,
                pinata_pinned=pinata_pinned,
                unpinned=False,
                note=note,
                backend="kubo" if pinned else "mock",
            )
            with self._db() as db:
                db.upsert(rec)
            return rec

        if self.backend == "mock":
            cid = "mockcid-" + hashlib.sha256(encrypted_skill_bytes).hexdigest()[:46]
            _MOCK_BLOB_CACHE[cid] = bytes(encrypted_skill_bytes)
            rec = SkillPinRecord(
                cid=cid,
                owner_did=owner_did,
                skill_id=skill_id,
                pinned_at=now,
                expires_at=expires_at,
                size_bytes=size,
                pinata_pinned=False,
                unpinned=False,
                note=note,
                backend="mock",
            )
            with self._db() as db:
                db.upsert(rec)
            return rec

        # backend == "pinata" (legacy 路径) ↓
        if not self.pinata_jwt:
            # mock fallback
            cid = "mockcid-" + hashlib.sha256(encrypted_skill_bytes).hexdigest()[:46]
            logger.warning(
                "PINATA_JWT 未设, 返 mock CID %s (skill_id=%s owner=%s)",
                cid, skill_id, owner_did,
            )
            pinata_pinned = False
        else:
            url = f"{self.api_base}/pinning/pinFileToIPFS"
            headers = {"Authorization": f"Bearer {self.pinata_jwt}"}
            files = {
                "file": (fname, bytes(encrypted_skill_bytes), "application/octet-stream"),
            }
            # Pinata 支持 multipart 带 metadata 字段 pinataMetadata (JSON string).
            metadata = {
                "name": f"sisoul-skill:{skill_id}",
                "keyvalues": {
                    "owner_did": owner_did,
                    "skill_id": skill_id,
                    "expires_at": str(expires_at),
                    "sisoul_kind": "ai-skill-package-v1",
                },
            }
            data = {"pinataMetadata": json.dumps(metadata)}
            try:
                with httpx.Client(timeout=timeout_sec) as client:
                    resp = client.post(url, headers=headers, files=files, data=data)
                    resp.raise_for_status()
                    body = resp.json()
                    cid = body.get("IpfsHash") or body.get("cid")
                    if not cid:
                        raise SkillPinError(f"Pinata 响应缺 IpfsHash: {body}")
                    cid = str(cid)
                    pinata_pinned = True
            except (httpx.HTTPError, ValueError) as e:
                raise SkillPinError(f"Pinata pin 失败: {type(e).__name__}: {e}") from e

        rec = SkillPinRecord(
            cid=cid,
            owner_did=owner_did,
            skill_id=skill_id,
            pinned_at=now,
            expires_at=expires_at,
            size_bytes=size,
            pinata_pinned=pinata_pinned,
            unpinned=False,
            note=note,
            backend="pinata" if pinata_pinned else "mock",
        )
        with self._db() as db:
            db.upsert(rec)
        return rec

    # ── fetch ──────────────────────────────────────────────────────────────

    def fetch(
        self,
        cid: str,
        *,
        timeout_sec: float = DEFAULT_FETCH_TIMEOUT_SEC,
    ) -> bytes:
        """从 IPFS gateway 拉加密 skill blob.

        mockcid- 开头: 不真拉, 从本地 ``_mock_blob_cache`` 拿 (test 用).

        Args:
            cid: IPFS CID.
            timeout_sec: HTTP 超时.

        Returns:
            encrypted_skill_bytes (调 decrypt_skill_package 解密).

        Raises:
            SkillFetchError: 所有 gateway 都失败 / mock blob 没 cache.
        """
        if cid.startswith("mockcid-"):
            blob = _MOCK_BLOB_CACHE.get(cid)
            if blob is None:
                raise SkillFetchError(
                    f"mock cid {cid} 不在 _MOCK_BLOB_CACHE; "
                    "test 用 register_mock_blob() 先 cache."
                )
            return blob

        # Wave A-3: kubo 路径
        if self.backend == "kubo":
            return self._kubo_fetch(cid, timeout_sec=timeout_sec)

        last_err: Exception = SkillFetchError("no gateway tried")
        for tpl in self.gateways:
            url = tpl.format(cid=cid)
            try:
                with httpx.Client(timeout=timeout_sec) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    return resp.content
            except httpx.HTTPError as e:
                last_err = e
                continue
        raise SkillFetchError(f"IPFS 拉取失败 (所有 gateway 都失败): {last_err}")

    # ── unpin ──────────────────────────────────────────────────────────────

    def unpin(
        self,
        cid: str,
        *,
        timeout_sec: float = DEFAULT_UNPIN_TIMEOUT_SEC,
        ignore_404: bool = True,
    ) -> bool:
        """unpin 一条 IPFS CID.

        本地 DB 标 unpinned=1 + ts. 调 Pinata DELETE /pinning/unpin/{cid}.
        mock cid 或无 jwt → 跳过远程, 仅本地标.

        Args:
            cid: 要 unpin 的 CID.
            timeout_sec: HTTP 超时.
            ignore_404: True 时 Pinata 返 404 (cid 不在 account 下) 视为成功.

        Returns:
            True = unpin 成功 (或 mock / 已经 unpin); False = 失败.

        Raises:
            SkillUnpinError: Pinata 调用失败 且 非 404 (或 ignore_404=False).
        """
        with self._db() as db:
            rec = db.get(cid)
            if rec and rec.unpinned:
                # 已经 unpin 过, 幂等返
                return True

        # Wave A-3: kubo 路径
        if self.backend == "kubo":
            try:
                ok = self._kubo_unpin(cid)
            except SkillUnpinError:
                raise
            with self._db() as db:
                if db.get(cid):
                    db.mark_unpinned(cid)
            return ok

        if cid.startswith("mockcid-") or not self.pinata_jwt:
            # mock / 无 jwt: 仅本地标. mock cache 清掉.
            _MOCK_BLOB_CACHE.pop(cid, None)
            with self._db() as db:
                if db.get(cid):
                    db.mark_unpinned(cid)
            return True

        url = f"{self.api_base}/pinning/unpin/{cid}"
        headers = {"Authorization": f"Bearer {self.pinata_jwt}"}
        try:
            with httpx.Client(timeout=timeout_sec) as client:
                resp = client.delete(url, headers=headers)
                if resp.status_code == 404 and ignore_404:
                    with self._db() as db:
                        if db.get(cid):
                            db.mark_unpinned(cid)
                    return True
                resp.raise_for_status()
        except httpx.HTTPError as e:
            raise SkillUnpinError(f"Pinata unpin 失败: {type(e).__name__}: {e}") from e

        with self._db() as db:
            if db.get(cid):
                db.mark_unpinned(cid)
        return True


# ── module-level helpers ─────────────────────────────────────────────────────


def pin_skill_to_ipfs(
    encrypted_skill_bytes: bytes,
    *,
    owner_did: str,
    skill_id: str,
    expiry_hours: int = 24,
    pinata_jwt: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> SkillPinRecord:
    """简化 wrapper. 详 SkillIPFSClient.pin()."""
    client = SkillIPFSClient(pinata_jwt=pinata_jwt, db_path=db_path)
    return client.pin(
        encrypted_skill_bytes,
        owner_did=owner_did,
        skill_id=skill_id,
        expiry_hours=expiry_hours,
    )


def fetch_skill_from_ipfs(
    cid: str,
    *,
    pinata_jwt: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> bytes:
    """简化 wrapper. 返 encrypted blob, 调用方再 decrypt_skill_package()."""
    client = SkillIPFSClient(pinata_jwt=pinata_jwt, db_path=db_path)
    return client.fetch(cid)


def pin_for_friend(
    did: str,
    cid: str,
    *,
    size_bytes: int = 0,
    expires_at: Optional[int] = None,
    is_friend_check: Optional[Any] = None,
    db_path: Optional[Path | str] = None,
) -> bool:
    """Wave A-3: 朋友请我 pin 一个 CID. 走 kubo backend.

    现成 wrapper, 透传到 IPFSKuboNode.pin_for_friend + 写本地 DB.

    Args:
        did: 朋友 DID (whitelist 检查用).
        cid: 要 pin 的 CID.
        size_bytes: 朋友报的体积 (size_limit gate).
        expires_at: 过期 ts (None = 永久 pin, scheduler 不会 unpin).
        is_friend_check: callable(did)->bool, None = 默认拒.
        db_path: 本地 DB.

    Returns:
        True = 真 pin 上; False = 拒.
    """
    from sisoul.p2p.ipfs_kubo import get_default_node
    import asyncio as _aio

    node = get_default_node()
    try:
        accepted = _aio.run(node.pin_for_friend(
            did, cid,
            size_bytes=size_bytes,
            expires_at=expires_at,
            is_friend_check=is_friend_check,
        ))
    except Exception as e:  # noqa: BLE001
        logger.warning("pin_for_friend(%s, %s) 失败: %s", did, cid, e)
        return False

    if accepted:
        # 记本地 DB (用 friend DID 作 owner_did)
        now = int(time.time())
        # 默认 30d expiry (跟 §B.3.6 R4 一致), 除非 caller 指定永久
        exp = expires_at or (now + 30 * 24 * 3600)
        rec = SkillPinRecord(
            cid=cid,
            owner_did=did,
            skill_id=f"friend-pin:{cid[:12]}",
            pinned_at=now,
            expires_at=exp,
            size_bytes=size_bytes,
            pinata_pinned=True,
            unpinned=False,
            note=f"friend pin request from {did}",
            backend="kubo",
        )
        with SkillPinDB(db_path=db_path) as db:
            db.upsert(rec)
    return accepted


def unpin_expired_skills(
    *,
    pinata_jwt: Optional[str] = None,
    db_path: Optional[Path | str] = None,
    now: int | None = None,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    """scheduler 入口: 扫本地 DB 找所有 expired & active 的 CID, 调 unpin.

    Args:
        pinata_jwt: Pinata token, 不传走 env / mock fallback.
        db_path: 本地 DB 路径 (test 隔离用).
        now: 当前 ts (test 用; 生产 None 取 time.time).
        raise_on_error: True 时遇到 unpin 失败抛; False 收集进 errors 字段继续.

    Returns:
        dict {"scanned": N, "unpinned": M, "errors": [(cid, errmsg)...]}
    """
    client = SkillIPFSClient(pinata_jwt=pinata_jwt, db_path=db_path)
    out = {"scanned": 0, "unpinned": 0, "errors": []}
    with SkillPinDB(db_path=db_path) as db:
        expired = db.list_expired_active(now=now)
    out["scanned"] = len(expired)
    for rec in expired:
        try:
            ok = client.unpin(rec.cid)
            if ok:
                out["unpinned"] += 1
        except SkillUnpinError as e:
            out["errors"].append((rec.cid, str(e)))
            if raise_on_error:
                raise
    return out


# ── mock blob cache (test 用) ───────────────────────────────────────────────


# fetch() 遇到 mockcid- 开头, 不真拉 IPFS, 从这里查. test 用 register_mock_blob() 注入.
_MOCK_BLOB_CACHE: dict[str, bytes] = {}


def register_mock_blob(cid: str, blob: bytes) -> None:
    """test 注入 mock blob, 让 fetch(mockcid) 能拿回. 仅 test 用."""
    if not cid.startswith("mockcid-"):
        raise ValueError(f"register_mock_blob 只接 mockcid- 开头, got {cid!r}")
    _MOCK_BLOB_CACHE[cid] = bytes(blob)


def clear_mock_blob_cache() -> None:
    """test 清空 mock cache. fixture teardown 用."""
    _MOCK_BLOB_CACHE.clear()


__all__ = [
    # 常量
    "PINATA_API_BASE",
    "IPFS_GATEWAYS",
    "DEFAULT_SKILL_PIN_DB",
    "DEFAULT_UNPIN_SCAN_INTERVAL_SEC",
    # 异常
    "SkillIPFSError",
    "SkillPinError",
    "SkillFetchError",
    "SkillUnpinError",
    # 数据
    "SkillPinRecord",
    "SkillPinDB",
    # 客户端
    "SkillIPFSClient",
    # helpers
    "pin_skill_to_ipfs",
    "fetch_skill_from_ipfs",
    "unpin_expired_skills",
    "pin_for_friend",  # Wave A-3
    # test helpers
    "register_mock_blob",
    "clear_mock_blob_cache",
]
