"""sisoul friend · AI 技能 IPFS 加密分发 (Phase 4 W70-W74 · 波 6 dev-A).

§28 §3.6 AI 技能 share 数据流第 4 步: Bob daemon 把 solidity-expert package 加密 +
IPFS pin (临时 hash, 24h 过期).

# 设计

复用波 4 dev-C ``sisoul.onchain.arweave.ArweaveSnapshot.pin_to_ipfs`` 的 Pinata HTTP API
模式 (`POST https://api.pinata.cloud/pinning/pinFileToIPFS` + Bearer JWT). 但本模块独立类
``SkillIPFSClient``, 避免拉 ArweaveSnapshot 整套 (它含 vault 加密 + Arweave 上链 + history).

# 24h 过期实现

Pinata 本身没有 "TTL auto-unpin" 概念. 我们 client 端实现:

1. pin 时调 ``POST /pinning/pinFileToIPFS`` 同时带 ``pinataMetadata.keyvalues.expires_at``
   = unix ts (Pinata 接 metadata 透传).
2. sisoul daemon scheduler 定期跑 ``unpin_expired_skills()`` 扫所有过期 CID 调
   ``DELETE /pinning/unpin/{cid}`` (本模块 export, daemon cron 5min 跑).
3. 本地 SQLite ``~/.sisoul/skill_pins.db`` 记本机 pin 过的 CID + expiry (Pinata metadata
   是辅助, 本地 DB 是真相源 — 跨 Pinata account / 跨设备时本地 DB 仍能 unpin).

# 模块边界

- 本文件: SkillIPFSClient + pin_skill_to_ipfs + fetch_skill_from_ipfs +
  unpin_expired_skills + SkillPinDB
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
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_skill_pins_owner ON skill_pins(owner_did);
CREATE INDEX IF NOT EXISTS idx_skill_pins_expires ON skill_pins(expires_at);
CREATE INDEX IF NOT EXISTS idx_skill_pins_unpinned ON skill_pins(unpinned);
"""


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
            " pinata_pinned, unpinned, unpinned_at, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        )


# ── 客户端 ───────────────────────────────────────────────────────────────────


class SkillIPFSClient:
    """Pinata HTTP API 客户端 (skill blob pin/fetch/unpin).

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
    ) -> None:
        self.pinata_jwt = pinata_jwt or os.environ.get("PINATA_JWT")
        self.api_base = api_base
        self.gateways = gateways
        self._db_path = Path(db_path) if db_path else None

    def _db(self) -> SkillPinDB:
        return SkillPinDB(db_path=self._db_path)

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
    # test helpers
    "register_mock_blob",
    "clear_mock_blob_cache",
]
