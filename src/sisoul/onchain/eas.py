"""sisoul EAS attestation queue (Phase 3 W37-W40, 波 4 dev-B).

§28 §1 模块 11: 链上 attestation queue. destructive 操作 audit 累积 → batched 上链
Optimism Sepolia testnet, 提供合规/法律级别的不可篡改证据.

设计要点:
- Attestation schema: sisoul-audit-v1 (actor_did / action_type / target / prompt_hash /
  timestamp / tool_name)
- 队列: 本地 SQLite (~/.sisoul/attest_queue.db), 累积 destructive 操作 audit
- Batch: 达到 10 条 OR 距上次 flush > 1h → 一笔 multi-attest tx (gas 共享)
- 默认 network: optimism-sepolia testnet (公共 RPC https://sepolia.optimism.io)
- mainnet 硬禁用 (本 wave 不上 mainnet, 防误花真钱)
- attester: dev-B 波 3 ship 的 DID (did:sisoul:<handle>) 作为 attester

EAS spec: https://docs.attest.org/

模块边界 (波 4 dev-B 严格约束):
- 不动 sisoul.onchain.arweave (dev-C 波 4 同 dir 不同文件)
- 不动 src/sisoul/{vault,llm,sync,identity,p2p,daemon.py} 主
- 共享 onchain/__init__.py: dev-C 后 append `from sisoul.onchain.arweave import *`

测试见 tests/test_onchain_eas.py + tests/test_eas_live_testnet.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

# ── 公开常量 ─────────────────────────────────────────────────────────────────

# Optimism Sepolia (Optimism L2 testnet) — 默认 network.
# EAS 主部署 (公开常量, 非凭据).
# 真 EAS Optimism Sepolia 合约: 见 https://docs.attest.org/docs/quick--start/contracts
OPTIMISM_SEPOLIA_CHAIN_ID = 11155420
OPTIMISM_SEPOLIA_DEFAULT_RPC = "https://sepolia.optimism.io"
EAS_CONTRACT_OPTIMISM_SEPOLIA = "0x4200000000000000000000000000000000000021"
EAS_SCHEMA_REGISTRY_OPTIMISM_SEPOLIA = "0x4200000000000000000000000000000000000020"

# Mainnet 硬禁用 (波 4 + P3-5 约束). 各链 mainnet 地址都列在 MAINNET_BLOCKED_CHAINS.
EAS_CONTRACT_OPTIMISM_MAINNET = "0x4200000000000000000000000000000000000021"
EAS_CONTRACT_ARBITRUM_ONE = "0xbD75f629A22Dc1ceD33dDA0b68c546A1c035c458"
EAS_CONTRACT_BASE_MAINNET = "0x4200000000000000000000000000000000000021"
EAS_CONTRACT_ZKSYNC_ERA = "0x21d8d8eE9F6cD3eEdc0CeB823b73Cbe3A07d0bAa"

# Phase 3 P3-5: 跨链 attest. 加 Arbitrum / Base / zkSync Sepolia testnet 真地址.
# 来源: https://docs.attest.org/docs/quick--start/contracts (公开常量, 非凭据).
ARBITRUM_SEPOLIA_CHAIN_ID = 421614
ARBITRUM_SEPOLIA_DEFAULT_RPC = "https://sepolia-rollup.arbitrum.io/rpc"
EAS_CONTRACT_ARBITRUM_SEPOLIA = "0xaEF4103A04090071165F78D45D83A0C0782c2B2a"

BASE_SEPOLIA_CHAIN_ID = 84532
BASE_SEPOLIA_DEFAULT_RPC = "https://sepolia.base.org"
EAS_CONTRACT_BASE_SEPOLIA = "0x4200000000000000000000000000000000000021"

ZKSYNC_SEPOLIA_CHAIN_ID = 300
ZKSYNC_SEPOLIA_DEFAULT_RPC = "https://sepolia.era.zksync.dev"
EAS_CONTRACT_ZKSYNC_SEPOLIA = "0x21d8d8eE9F6cD3eEdc0CeB823b73Cbe3A07d0bAa"

# Network 类型. P3-5 扩 Arbitrum / Base / zkSync.
Network = Literal[
    "optimism-sepolia",
    "optimism-mainnet",
    "arbitrum-sepolia",
    "arbitrum-mainnet",
    "base-sepolia",
    "base-mainnet",
    "zksync-sepolia",
    "zksync-mainnet",
    "mock",
]

# 所有 mainnet — 一律拒上链.
MAINNET_BLOCKED_CHAINS: set[str] = {
    "optimism-mainnet",
    "arbitrum-mainnet",
    "base-mainnet",
    "zksync-mainnet",
}

# Network → chain_id 反查 (用于 RPC chain_id 校验).
CHAIN_ID_BY_NETWORK: dict[str, int] = {
    "optimism-sepolia": OPTIMISM_SEPOLIA_CHAIN_ID,
    "arbitrum-sepolia": ARBITRUM_SEPOLIA_CHAIN_ID,
    "base-sepolia": BASE_SEPOLIA_CHAIN_ID,
    "zksync-sepolia": ZKSYNC_SEPOLIA_CHAIN_ID,
}

# Short alias (CLI --chain optimism|arbitrum|base|zksync) → testnet network.
SHORT_TO_NETWORK: dict[str, str] = {
    "optimism": "optimism-sepolia",
    "arbitrum": "arbitrum-sepolia",
    "base": "base-sepolia",
    "zksync": "zksync-sepolia",
}

# Batch 阈值默认.
DEFAULT_BATCH_SIZE = 10
DEFAULT_BATCH_TIMEOUT_SEC = 3600  # 1h

# 默认 attest queue DB 路径.
DEFAULT_ATTEST_QUEUE_DB = Path.home() / ".sisoul" / "attest_queue.db"

# 默认 config 文件 (RPC / network / private key path 用户配).
DEFAULT_ATTEST_CONFIG = Path.home() / ".sisoul" / "attest_config.json"

# EAS schema 定义 (sisoul audit attestation v1).
# Schema 字符串符合 EAS schema spec (Solidity-like 类型).
SISOUL_AUDIT_SCHEMA = (
    "string actor_did,"
    "string action_type,"
    "string target,"
    "bytes32 prompt_hash,"
    "uint64 timestamp,"
    "string tool_name"
)

# 确定性 mock schema UID (mock 模式不连网, 但需要稳定 UID 用于本地 verify).
# 真上链时, schema UID 由 SchemaRegistry.register() 链上返.
MOCK_SCHEMA_UID = "0x" + hashlib.sha256(
    f"sisoul-audit-v1::{SISOUL_AUDIT_SCHEMA}".encode("utf-8")
).hexdigest()


# ── 异常 ─────────────────────────────────────────────────────────────────────


class EASError(Exception):
    """EAS attestation 通用异常."""


class NetworkNotSupportedError(EASError):
    """禁用 mainnet (波 4 约束)."""


class AttestationNotFoundError(EASError):
    """verify 找不到 attestation."""


class QueueEmptyError(EASError):
    """flush 时队列空."""


class ConfigError(EASError):
    """config 缺失 / 不合法."""


# ── P3-5 跨链 chain 配置 ────────────────────────────────────────────────────


@dataclass
class ChainConfig:
    """单条 chain 配置 (P3-5 跨链路由)."""

    name: str
    chain_id: int
    rpc_url: str
    eas_contract: str
    is_mainnet: bool = False
    # 默认 schema UID (上线后由 SchemaRegistry.register() 链上返; mock 用 MOCK_SCHEMA_UID)
    schema_uid: str = ""


# 公开 chain 注册表 (CLI / daemon / config 共用). key 是 short 名.
# is_mainnet=True 一律 hard gate (这里只列 testnet, mainnet 走 MAINNET_BLOCKED_CHAINS 拒).
CHAIN_REGISTRY: dict[str, ChainConfig] = {
    "optimism": ChainConfig(
        name="optimism-sepolia",
        chain_id=OPTIMISM_SEPOLIA_CHAIN_ID,
        rpc_url=OPTIMISM_SEPOLIA_DEFAULT_RPC,
        eas_contract=EAS_CONTRACT_OPTIMISM_SEPOLIA,
        is_mainnet=False,
    ),
    "arbitrum": ChainConfig(
        name="arbitrum-sepolia",
        chain_id=ARBITRUM_SEPOLIA_CHAIN_ID,
        rpc_url=ARBITRUM_SEPOLIA_DEFAULT_RPC,
        eas_contract=EAS_CONTRACT_ARBITRUM_SEPOLIA,
        is_mainnet=False,
    ),
    "base": ChainConfig(
        name="base-sepolia",
        chain_id=BASE_SEPOLIA_CHAIN_ID,
        rpc_url=BASE_SEPOLIA_DEFAULT_RPC,
        eas_contract=EAS_CONTRACT_BASE_SEPOLIA,
        is_mainnet=False,
    ),
    "zksync": ChainConfig(
        name="zksync-sepolia",
        chain_id=ZKSYNC_SEPOLIA_CHAIN_ID,
        rpc_url=ZKSYNC_SEPOLIA_DEFAULT_RPC,
        eas_contract=EAS_CONTRACT_ZKSYNC_SEPOLIA,
        is_mainnet=False,
    ),
}


def resolve_chain(short_or_network: str) -> ChainConfig:
    """传 short ('optimism') 或全名 ('optimism-sepolia') 拿 ChainConfig.

    mainnet 网络名 → 抛 NetworkNotSupportedError (hard gate).
    未知 → NetworkNotSupportedError.
    """
    key = (short_or_network or "").lower()
    if key in MAINNET_BLOCKED_CHAINS:
        raise NetworkNotSupportedError(
            f"{key} 上链被禁用 (P3-5 跨链 hard gate: 不花真钱). Phase 5 GA 切开."
        )
    if key in CHAIN_REGISTRY:
        return CHAIN_REGISTRY[key]
    for _short, _cfg in CHAIN_REGISTRY.items():
        if _cfg.name == key:
            return _cfg
    raise NetworkNotSupportedError(
        f"未知 chain '{short_or_network}'. 支持: "
        f"{sorted(CHAIN_REGISTRY.keys())} 或全名 "
        f"{[c.name for c in CHAIN_REGISTRY.values()]}"
    )


# ── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class AuditAttestation:
    """单条 audit attestation (写入 queue 的最小单元).

    字段对应 SISOUL_AUDIT_SCHEMA.
    """

    actor_did: str
    action_type: str  # rm / git-push / chmod / curl-post / ssh-destructive / ...
    target: str  # file path / URL / host
    prompt_hash: str  # bytes32 hex, sha256(user prompt)
    timestamp: int  # unix epoch (uint64)
    tool_name: str  # claude-code / codex / aider / ...
    # queue 本地字段
    queue_id: str = ""  # uuid4
    queued_at: str = ""  # ISO ts 入队
    status: Literal["pending", "batched", "confirmed", "failed"] = "pending"
    batch_uid: str | None = None  # 上链后归属的 batch tx id (本地)
    attestation_uid: str | None = None  # EAS attestation UID (链上返)
    tx_hash: str | None = None  # batch tx hash

    def __post_init__(self) -> None:
        if not self.queue_id:
            self.queue_id = str(uuid.uuid4())
        if not self.queued_at:
            self.queued_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # prompt_hash 归一: 加 0x 前缀 + 补 64 hex.
        if not self.prompt_hash.startswith("0x"):
            self.prompt_hash = "0x" + self.prompt_hash
        # 不强制 64 字符, 但 short 时左 pad.
        body = self.prompt_hash[2:]
        if len(body) < 64:
            body = body.zfill(64)
        elif len(body) > 64:
            body = body[:64]
        self.prompt_hash = "0x" + body

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AuditAttestation:
        return cls(**d)

    @classmethod
    def from_audit_payload(
        cls,
        actor_did: str,
        action_type: str,
        target: str,
        prompt: str,
        tool_name: str,
        timestamp: int | None = None,
    ) -> AuditAttestation:
        """工厂: 从 audit hook POST payload 构造 (自动算 prompt_hash)."""
        h = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return cls(
            actor_did=actor_did,
            action_type=action_type,
            target=target,
            prompt_hash=h,
            timestamp=timestamp or int(time.time()),
            tool_name=tool_name,
        )


@dataclass
class BatchResult:
    """batch 上链结果."""

    batch_uid: str
    tx_hash: str
    network: str
    schema_uid: str
    attestation_uids: list[str]
    gas_used_estimate: int  # gas units
    gas_cost_wei_estimate: int
    confirmed_at: str
    count: int
    method: Literal["mock", "live-readonly", "live-tx"] = "mock"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Config ───────────────────────────────────────────────────────────────────


@dataclass
class AttestConfig:
    """用户可配 attest 参数 (~/.sisoul/attest_config.json)."""

    network: Network = "optimism-sepolia"
    rpc_url: str = OPTIMISM_SEPOLIA_DEFAULT_RPC
    batch_size: int = DEFAULT_BATCH_SIZE
    batch_timeout_sec: int = DEFAULT_BATCH_TIMEOUT_SEC
    private_key_path: str | None = None  # PEM / hex file path; None=mock 不签
    schema_uid: str = MOCK_SCHEMA_UID  # 默认 mock; 真注册后改
    attester_did: str | None = None  # 默认 None=registry 第一条
    # mainnet 双 gate (P1-6 #3): config.confirm_mainnet=True + env EAS_ALLOW_MAINNET=1 才放行
    confirm_mainnet: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AttestConfig:
        # 容错 unknown 字段.
        valid_keys = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid_keys})


def load_config(config_path: Path | str | None = None) -> AttestConfig:
    """从 ~/.sisoul/attest_config.json 读 config; 文件不存在返默认."""
    path = Path(config_path) if config_path else DEFAULT_ATTEST_CONFIG
    if not path.exists():
        return AttestConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise ConfigError(f"读 attest_config 失败 ({path}): {e}") from e
    return AttestConfig.from_dict(data)


def save_config(cfg: AttestConfig, config_path: Path | str | None = None) -> Path:
    """写 config 到 ~/.sisoul/attest_config.json."""
    path = Path(config_path) if config_path else DEFAULT_ATTEST_CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ── Queue (SQLite) ───────────────────────────────────────────────────────────


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS attest_queue (
    queue_id TEXT PRIMARY KEY,
    actor_did TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    status TEXT NOT NULL,
    batch_uid TEXT,
    attestation_uid TEXT,
    tx_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_attest_queue_status ON attest_queue(status);
CREATE INDEX IF NOT EXISTS idx_attest_queue_batch ON attest_queue(batch_uid);

CREATE TABLE IF NOT EXISTS attest_batches (
    batch_uid TEXT PRIMARY KEY,
    tx_hash TEXT NOT NULL,
    network TEXT NOT NULL,
    schema_uid TEXT NOT NULL,
    attestation_uids TEXT NOT NULL,
    gas_used_estimate INTEGER NOT NULL,
    gas_cost_wei_estimate INTEGER NOT NULL,
    confirmed_at TEXT NOT NULL,
    count INTEGER NOT NULL,
    method TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attest_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class AttestQueue:
    """本地 SQLite attest queue.

    使用模式:
        q = AttestQueue()
        q.enqueue(audit)
        if q.should_flush(cfg.batch_size, cfg.batch_timeout_sec):
            uploader.flush(q, cfg)
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_ATTEST_QUEUE_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> AttestQueue:
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()

    # ── 写 ──
    def enqueue(self, att: AuditAttestation) -> str:
        self._conn.execute(
            "INSERT OR REPLACE INTO attest_queue "
            "(queue_id, actor_did, action_type, target, prompt_hash, timestamp, tool_name, "
            " queued_at, status, batch_uid, attestation_uid, tx_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                att.queue_id,
                att.actor_did,
                att.action_type,
                att.target,
                att.prompt_hash,
                att.timestamp,
                att.tool_name,
                att.queued_at,
                att.status,
                att.batch_uid,
                att.attestation_uid,
                att.tx_hash,
            ),
        )
        self._conn.commit()
        return att.queue_id

    def mark_batched(
        self,
        queue_ids: list[str],
        batch_uid: str,
        tx_hash: str,
        attestation_uids: list[str],
    ) -> None:
        """flush 后把 queue items 标记 batched + 写回 attestation_uid (按顺序对齐)."""
        if len(queue_ids) != len(attestation_uids):
            raise EASError(
                f"queue_ids ({len(queue_ids)}) 长度不等于 attestation_uids ({len(attestation_uids)})"
            )
        for qid, auid in zip(queue_ids, attestation_uids):
            self._conn.execute(
                "UPDATE attest_queue SET status='confirmed', batch_uid=?, "
                "tx_hash=?, attestation_uid=? WHERE queue_id=?",
                (batch_uid, tx_hash, auid, qid),
            )
        self._set_meta("last_flush_ts", str(int(time.time())))
        self._conn.commit()

    def record_batch(self, batch: BatchResult) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO attest_batches "
            "(batch_uid, tx_hash, network, schema_uid, attestation_uids, "
            " gas_used_estimate, gas_cost_wei_estimate, confirmed_at, count, method) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                batch.batch_uid,
                batch.tx_hash,
                batch.network,
                batch.schema_uid,
                json.dumps(batch.attestation_uids),
                batch.gas_used_estimate,
                batch.gas_cost_wei_estimate,
                batch.confirmed_at,
                batch.count,
                batch.method,
            ),
        )
        self._conn.commit()

    # ── 读 ──
    def pending(self, limit: int | None = None) -> list[AuditAttestation]:
        q = "SELECT * FROM attest_queue WHERE status='pending' ORDER BY queued_at ASC"
        if limit:
            q += f" LIMIT {int(limit)}"
        rows = self._conn.execute(q).fetchall()
        return [self._row_to_attestation(r) for r in rows]

    def all_items(self, status: str | None = None, limit: int = 100) -> list[AuditAttestation]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM attest_queue WHERE status=? "
                "ORDER BY queued_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM attest_queue ORDER BY queued_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_attestation(r) for r in rows]

    def get_batch(self, batch_uid: str) -> BatchResult | None:
        r = self._conn.execute(
            "SELECT * FROM attest_batches WHERE batch_uid=?", (batch_uid,)
        ).fetchone()
        if not r:
            return None
        return BatchResult(
            batch_uid=r["batch_uid"],
            tx_hash=r["tx_hash"],
            network=r["network"],
            schema_uid=r["schema_uid"],
            attestation_uids=json.loads(r["attestation_uids"]),
            gas_used_estimate=r["gas_used_estimate"],
            gas_cost_wei_estimate=r["gas_cost_wei_estimate"],
            confirmed_at=r["confirmed_at"],
            count=r["count"],
            method=r["method"],
        )

    def list_batches(self, limit: int = 50) -> list[BatchResult]:
        rows = self._conn.execute(
            "SELECT * FROM attest_batches ORDER BY confirmed_at DESC LIMIT ?", (limit,)
        ).fetchall()
        out: list[BatchResult] = []
        for r in rows:
            out.append(
                BatchResult(
                    batch_uid=r["batch_uid"],
                    tx_hash=r["tx_hash"],
                    network=r["network"],
                    schema_uid=r["schema_uid"],
                    attestation_uids=json.loads(r["attestation_uids"]),
                    gas_used_estimate=r["gas_used_estimate"],
                    gas_cost_wei_estimate=r["gas_cost_wei_estimate"],
                    confirmed_at=r["confirmed_at"],
                    count=r["count"],
                    method=r["method"],
                )
            )
        return out

    def find_by_attestation_uid(self, uid: str) -> AuditAttestation | None:
        r = self._conn.execute(
            "SELECT * FROM attest_queue WHERE attestation_uid=?", (uid,)
        ).fetchone()
        if not r:
            return None
        return self._row_to_attestation(r)

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for status in ("pending", "batched", "confirmed", "failed"):
            n = self._conn.execute(
                "SELECT COUNT(*) FROM attest_queue WHERE status=?", (status,)
            ).fetchone()[0]
            out[status] = int(n)
        out["batches"] = int(
            self._conn.execute("SELECT COUNT(*) FROM attest_batches").fetchone()[0]
        )
        return out

    # ── 决策 ──
    def should_flush(self, batch_size: int, timeout_sec: int) -> bool:
        """达到 batch_size 或距上次 flush > timeout_sec → 应触发 batch."""
        n = self._conn.execute(
            "SELECT COUNT(*) FROM attest_queue WHERE status='pending'"
        ).fetchone()[0]
        if n == 0:
            return False
        if n >= batch_size:
            return True
        last = self._get_meta("last_flush_ts")
        if last is None:
            # 没 flush 过 → 等队列满, 不超时
            return False
        try:
            elapsed = int(time.time()) - int(last)
        except ValueError:
            return False
        return elapsed > timeout_sec

    # ── 内部 ──
    def _get_meta(self, key: str) -> str | None:
        r = self._conn.execute(
            "SELECT value FROM attest_meta WHERE key=?", (key,)
        ).fetchone()
        return r["value"] if r else None

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO attest_meta (key, value) VALUES (?, ?)", (key, value)
        )

    @staticmethod
    def _row_to_attestation(r: sqlite3.Row) -> AuditAttestation:
        return AuditAttestation(
            actor_did=r["actor_did"],
            action_type=r["action_type"],
            target=r["target"],
            prompt_hash=r["prompt_hash"],
            timestamp=int(r["timestamp"]),
            tool_name=r["tool_name"],
            queue_id=r["queue_id"],
            queued_at=r["queued_at"],
            status=r["status"],
            batch_uid=r["batch_uid"],
            attestation_uid=r["attestation_uid"],
            tx_hash=r["tx_hash"],
        )


# ── EAS attestation UID / encoding (mock + 真) ───────────────────────────────


def encode_attestation_data(att: AuditAttestation) -> bytes:
    """EAS schema-encoded attestation data.

    真上链时用 eth_abi.encode 按 schema 类型编码:
        encode(["string","string","string","bytes32","uint64","string"],
               [actor_did, action_type, target,
                bytes.fromhex(prompt_hash[2:]), timestamp, tool_name])

    本 mock: 用 JSON canonical 序列化 + sha256 占位 (本地校验/lookup 自洽).
    真接 EAS SDK 时切 eth_abi.

    Phase 5 mainnet 切换 + EAS 官方 Python SDK 出来后切真 encoder.
    """
    payload = json.dumps(
        {
            "actor_did": att.actor_did,
            "action_type": att.action_type,
            "target": att.target,
            "prompt_hash": att.prompt_hash,
            "timestamp": att.timestamp,
            "tool_name": att.tool_name,
        },
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return payload


def compute_attestation_uid(
    att: AuditAttestation, schema_uid: str, batch_uid: str
) -> str:
    """确定性 attestation UID (mock).

    真链上 UID 由 EAS contract 算: keccak256(schema + recipient + attester + time + ...).
    mock: sha256(canonical encoding + batch_uid + schema_uid) 占位, 本地 verify 自洽.
    """
    h = hashlib.sha256()
    h.update(encode_attestation_data(att))
    h.update(b"::")
    h.update(schema_uid.encode("utf-8"))
    h.update(b"::")
    h.update(batch_uid.encode("utf-8"))
    h.update(b"::")
    h.update(att.queue_id.encode("utf-8"))
    return "0x" + h.hexdigest()


def _mock_tx_hash(seed: str) -> str:
    return "0x" + hashlib.sha256(
        f"mock-tx:{seed}:{time.time_ns()}".encode()
    ).hexdigest()


# ── Batch uploader (mock + 真 testnet readonly) ───────────────────────────────


def _gas_estimate(count: int) -> tuple[int, int]:
    """估算 multi-attest gas.

    单 attest 大致 ~75k gas. Batch n 条 ~ (50k base + 25k per item).
    Optimism L2 gas price ~ 0.001 gwei (testnet); cost = gas * price.
    返 (gas_units, cost_wei).
    """
    gas_units = 50_000 + 25_000 * max(count, 1)
    # Optimism Sepolia gas price 极低, ~ 1_000 wei (估算)
    gas_price_wei = 1_000
    return gas_units, gas_units * gas_price_wei


def upload_batch(
    queue: AttestQueue,
    config: AttestConfig,
    *,
    force: bool = False,
    max_items: int | None = None,
) -> BatchResult:
    """flush queue → batch 上链 (mock 或 live).

    - network=optimism-mainnet → 拒 (波 4 约束).
    - network=mock → 全本地, 返 mock tx + attestation UIDs.
    - network=optimism-sepolia + private_key_path=None → readonly mock + 标"live-readonly".
    - network=optimism-sepolia + private_key_path 指向真 key 文件 → 真发 tx (本 wave 不实测,
      代码路径就绪, 用户准备 Sepolia ETH gas 即可启用).

    返 BatchResult (含 batch_uid / tx_hash / attestation_uids 等).
    """
    if config.network in MAINNET_BLOCKED_CHAINS:
        # mainnet 双重 gate (P1-6 #3, 全 4 链统一): env EAS_ALLOW_MAINNET=1 + config.confirm_mainnet=True.
        allow = os.environ.get("EAS_ALLOW_MAINNET") == "1"
        confirm = getattr(config, "confirm_mainnet", False)
        if not (allow and confirm):
            raise NetworkNotSupportedError(
                f"{config.network} 上链被双 gate 阻止: 需 env EAS_ALLOW_MAINNET=1 "
                "+ config.confirm_mainnet=True. 内测期保持 hard-block."
            )

    # 拿 pending: max_items 显式优先; force=True 拿全部; 否则拿 batch_size 条.
    if max_items is not None:
        fetch_limit: int | None = max_items
    elif force:
        fetch_limit = None
    else:
        fetch_limit = config.batch_size
    items = queue.pending(limit=fetch_limit)
    if not items:
        raise QueueEmptyError("queue 无 pending 项, 无法 flush")

    # 二次裁剪 (兜底, 防 pending 返多)
    if max_items is not None:
        items = items[:max_items]
    elif not force:
        items = items[: config.batch_size]

    batch_uid = str(uuid.uuid4())
    confirmed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    schema_uid = config.schema_uid

    # 算每条 attestation UID.
    attestation_uids = [
        compute_attestation_uid(it, schema_uid, batch_uid) for it in items
    ]
    gas_units, cost_wei = _gas_estimate(len(items))

    if config.network == "mock":
        tx_hash = _mock_tx_hash(batch_uid)
        method: Literal["mock", "live-readonly", "live-tx"] = "mock"
    else:
        # P3-5: 任一 testnet (optimism/arbitrum/base/zksync Sepolia).
        if not config.private_key_path:
            # readonly: 仍生成 mock tx, 但 method=live-readonly 表示已校验 RPC 通
            tx_hash = _mock_tx_hash(f"{config.network}-readonly:{batch_uid}")
            method = "live-readonly"
            # 真 smoke: 校验 RPC chain_id 跟 config.network 匹配.
            try:
                _verify_testnet_rpc(config.rpc_url, config.network)
            except Exception:
                # fail-open: RPC 不通 → 退到 mock, 但 method 标 mock 防误以为已上链.
                tx_hash = _mock_tx_hash(f"{config.network}-rpc-failed:{batch_uid}")
                method = "mock"
        else:
            # 真发 tx (代码路径就绪, 本 wave 不真跑).
            tx_hash, method = _live_send_batch_tx(
                items=items,
                schema_uid=schema_uid,
                config=config,
            )

    result = BatchResult(
        batch_uid=batch_uid,
        tx_hash=tx_hash,
        network=config.network,
        schema_uid=schema_uid,
        attestation_uids=attestation_uids,
        gas_used_estimate=gas_units,
        gas_cost_wei_estimate=cost_wei,
        confirmed_at=confirmed_at,
        count=len(items),
        method=method,
    )

    queue.record_batch(result)
    queue.mark_batched(
        queue_ids=[it.queue_id for it in items],
        batch_uid=batch_uid,
        tx_hash=tx_hash,
        attestation_uids=attestation_uids,
    )
    return result


# ── 波 7 dev-A bug-6: exponential backoff retry wrapper ────────────────────


def upload_batch_with_retry(
    queue: AttestQueue,
    config: AttestConfig,
    *,
    force: bool = False,
    max_items: int | None = None,
    max_retries: int = 3,
    base_delay_sec: float = 1.0,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> BatchResult:
    """upload_batch + 3 次 exponential backoff retry.

    波 7 dev-A bug-6 修复 (qa-D P2-5 列): EAS / Arweave failed retry 不实现 →
    真 tx 失败 audit 静默丢失. 这里加 wrapper, RPC 暂挂 / 网络抖 / 链 fork 自动重试.

    重试条件: EASError / RuntimeError / OSError (网络异常类).
    跳过重试: NetworkNotSupportedError / QueueEmptyError / ConfigError (是 user / config bug).

    退避: 1s, 2s, 4s (base * 2^attempt) + jitter ±20% (P1-6 #5 edge case 修).
    """
    import random as _random
    import time as _time

    last_exc: BaseException | None = None
    skip_exc = (NetworkNotSupportedError, QueueEmptyError, ConfigError)
    for attempt in range(max_retries):
        try:
            return upload_batch(queue, config, force=force, max_items=max_items)
        except skip_exc:
            # 永久失败: 直接抛, 不重试
            raise
        except (EASError, RuntimeError, OSError) as e:
            last_exc = e
            if attempt == max_retries - 1:
                break
            if on_retry is not None:
                try:
                    on_retry(attempt + 1, e)
                except Exception:  # noqa: BLE001
                    pass
            # jitter ±20% 防 thundering herd (多机同时重试 RPC 撞)
            base = base_delay_sec * (2 ** attempt)
            delay = base * (1.0 + _random.uniform(-0.2, 0.2))
            _time.sleep(max(0.05, delay))
    # 三次都失败 — 抛最后一次, queue 未 mark_batched (保持 pending, 下轮重试)
    assert last_exc is not None
    raise EASError(
        f"upload_batch_with_retry 失败 ({max_retries} 次重试均挂): {type(last_exc).__name__}: {last_exc}"
    ) from last_exc


# v1.0-decentralized #4 (Helios light client) · Wave A agent-1 · 2026-05-19:
# EAS sepolia testnet RPC 调用优先走 sisoul.rpc.HeliosClient (trustless, Merkle proof).
# helios 0.11.1 不原生支持 op-sepolia/arbitrum/zksync, 但支持 base-sepolia. 不支持的链
# 自动退到公共 RPC + 警告 banner (向后兼容). 用户可设
# SISOUL_HELIOS_DISABLE=1 强制 legacy 公共 RPC 路径 (回归测试 / 调试用).

_EAS_NETWORK_TO_HELIOS_CHAIN: dict[str, str] = {
    # helios 0.11.1 原生支持: base-sepolia 可走 trustless
    "base-sepolia": "base-sepolia",
    # 不支持的 (op-sepolia / arbitrum-sepolia / zksync-sepolia): map "" → 直走公共 RPC
    "optimism-sepolia": "",
    "arbitrum-sepolia": "",
    "zksync-sepolia": "",
}


def _verify_testnet_rpc(rpc_url: str, network: str) -> None:
    """通用: 真连指定 testnet RPC 校验 chain_id (P3-5, v1.0-decentralized #4).

    network: 'optimism-sepolia' / 'arbitrum-sepolia' / 'base-sepolia' / 'zksync-sepolia'.

    路径优先级 (v1.0-decentralized):
    1. SISOUL_HELIOS_DISABLE=1 → 跳过 helios, 直走公共 RPC (legacy).
    2. helios 全局 client 已 start 且 network 在原生支持 (base-sepolia) →
       走 helios trustless. ChainStatus.mode='helios'.
    3. fallback → 直 httpx 公共 RPC + warn banner ("trusted, not trustless").

    不发 tx, 不签名, 只 eth_chainId. chain_id 不匹配 → EASError.
    """
    if network in MAINNET_BLOCKED_CHAINS:
        raise NetworkNotSupportedError(f"{network} 不允许 readonly verify (mainnet hard gate)")

    expected = CHAIN_ID_BY_NETWORK.get(network)
    if expected is None:
        raise EASError(f"未知 testnet '{network}' (P3-5 仅支持 {list(CHAIN_ID_BY_NETWORK.keys())})")

    chain_id: int | None = None
    verified_mode: str = "trusted"

    # Path 1: helios trustless (if enabled + chain natively supported)
    helios_disabled = os.environ.get("SISOUL_HELIOS_DISABLE") == "1"
    helios_chain = _EAS_NETWORK_TO_HELIOS_CHAIN.get(network, "")
    if not helios_disabled and helios_chain:
        try:
            from sisoul.rpc.helios_client import get_default_client
            client = get_default_client()
            if client is not None:
                s = client.status(helios_chain)
                if isinstance(s, dict):  # 防御 (full status() 返 dict)
                    s = s.get(helios_chain)
                if s and s.in_sync and s.mode == "helios":
                    try:
                        hex_id = client.call_sync(helios_chain, "eth_chainId", [])
                        chain_id = int(hex_id, 16)
                        verified_mode = "trustless"
                    except Exception as e:  # noqa: BLE001
                        import logging as _log
                        _log.getLogger(__name__).warning(
                            "helios eth_chainId(%s) 失败 (%s), fallback 公共 RPC",
                            helios_chain, e
                        )
        except ImportError:
            pass  # sisoul.rpc 还没装好

    # Path 2: fallback 公共 RPC (跟原 behaviour 一致)
    if chain_id is None:
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError as e:
            raise EASError(
                "httpx 未装. pip install 'sisoul[daemon]' 或 'sisoul[onchain]'."
            ) from e

        payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}
        try:
            r = httpx.post(rpc_url, json=payload, timeout=10.0)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            raise EASError(f"RPC 调用失败 ({rpc_url}): {e}") from e

        chain_id_hex = data.get("result", "0x0")
        chain_id = int(chain_id_hex, 16)
        verified_mode = "trusted"

    if chain_id != expected:
        raise EASError(
            f"RPC chain_id 不匹配: 期望 {expected} ({network}), "
            f"实际 {chain_id} (mode={verified_mode}, rpc={rpc_url})"
        )


def _verify_optimism_sepolia_rpc(rpc_url: str) -> None:
    """Backward-compat shim (P3-5 之前老调用方). 走 _verify_testnet_rpc."""
    _verify_testnet_rpc(rpc_url, "optimism-sepolia")


def _live_send_batch_tx(
    items: list[AuditAttestation],
    schema_uid: str,
    config: AttestConfig,
) -> tuple[str, Literal["live-tx"]]:
    """真发 multi-attest tx (代码路径; 本 wave 不实测).

    步骤:
    1. 读 private_key (config.private_key_path).
    2. web3.py 连 rpc_url.
    3. encode_attestation_data 每条 → eth_abi.encode.
    4. EAS contract multi-attest call.
    5. eth_account.sign_transaction → send_raw_transaction → wait_for_receipt.

    用户需准备 Optimism Sepolia ETH gas: https://docs.optimism.io/builders/tools/build/faucets
    """
    try:
        from web3 import Web3  # type: ignore[import-not-found]
        from eth_account import Account  # type: ignore[import-not-found]
    except ImportError as e:
        raise EASError(
            "web3 / eth-account 未装. pip install 'sisoul[onchain]'."
        ) from e

    pk_path = Path(config.private_key_path)  # type: ignore[arg-type]
    if not pk_path.exists():
        raise ConfigError(f"private_key_path 不存在: {pk_path}")

    pk = pk_path.read_text(encoding="utf-8").strip()
    if not pk.startswith("0x"):
        pk = "0x" + pk

    acct = Account.from_key(pk)
    w3 = Web3(Web3.HTTPProvider(config.rpc_url))
    if not w3.is_connected():
        raise EASError(f"web3 connect 失败: {config.rpc_url}")

    # 真 tx 构造 + send (本 wave 不实测, 保留 stub 触发安全 abort).
    raise EASError(
        "live-tx 路径已就绪但未启用 (波 4 约束 readonly only). "
        f"attester={acct.address}, items={len(items)}, schema={schema_uid}. "
        "Phase 5 GA 时开启."
    )


# ── verify (链上 / 本地) ─────────────────────────────────────────────────────


def verify_attestation_local(queue: AttestQueue, uid: str) -> dict[str, Any]:
    """本地 verify: 在 queue/batches 找 uid → 重算 hash → 比对.

    返:
        {"valid": bool, "method": "local-recompute", "attestation": {...}, "batch": {...}}
    """
    att = queue.find_by_attestation_uid(uid)
    if not att:
        raise AttestationNotFoundError(f"本地 queue 找不到 attestation uid={uid}")
    if not att.batch_uid:
        return {
            "valid": False,
            "method": "local-recompute",
            "reason": "attestation 已找到但未归属任何 batch",
            "attestation": att.to_dict(),
        }
    batch = queue.get_batch(att.batch_uid)
    if not batch:
        return {
            "valid": False,
            "method": "local-recompute",
            "reason": f"batch {att.batch_uid} 不在本地",
            "attestation": att.to_dict(),
        }
    expected = compute_attestation_uid(att, batch.schema_uid, batch.batch_uid)
    valid = expected == uid
    return {
        "valid": valid,
        "method": "local-recompute",
        "attestation": att.to_dict(),
        "batch": batch.to_dict(),
        "expected_uid": expected,
        "given_uid": uid,
    }


def _easscan_graphql_url(network: str) -> str:
    """P3-5: chain → easscan GraphQL endpoint. Mainnet 在调用前已被拒.

    源: https://docs.attest.org/docs/developer-tools/api
    """
    mapping = {
        "optimism-sepolia": "https://optimism-sepolia.easscan.org/graphql",
        "arbitrum-sepolia": "https://arbitrum-sepolia.easscan.org/graphql",
        "base-sepolia": "https://base-sepolia.easscan.org/graphql",
        "zksync-sepolia": "https://zksync-sepolia.easscan.org/graphql",
    }
    if network not in mapping:
        raise EASError(
            f"未知 testnet network '{network}' (P3-5 仅支持 {list(mapping.keys())})"
        )
    return mapping[network]


def verify_attestation_onchain(
    uid: str, network: Network = "optimism-sepolia", rpc_url: str | None = None
) -> dict[str, Any]:
    """链上 verify: 调 EAS GraphQL 查 attestation (readonly).

    本 wave: mock 模式直接返 not-found-onchain (因为没真上链).
    P3-5: 支持 4 testnet (optimism / arbitrum / base / zksync Sepolia).
    GraphQL endpoint: easscan 各 chain 子域 (见 _easscan_graphql_url).

    返 {"valid": bool, "method": "onchain-graphql", "data": {...}}
    """
    if network == "mock":
        return {
            "valid": False,
            "method": "onchain-graphql",
            "reason": "network=mock, 无链上数据",
        }
    if network in MAINNET_BLOCKED_CHAINS:
        raise NetworkNotSupportedError(
            f"{network} 查询本 wave 禁用 (避免误以为已部署). Phase 5 切开 (P3-5 跨链)."
        )

    try:
        import httpx  # type: ignore[import-not-found]
    except ImportError as e:
        raise EASError("httpx 未装") from e

    graphql_url = _easscan_graphql_url(network)
    query = """
    query GetAttestation($uid: String!) {
        attestation(where: {id: $uid}) {
            id
            attester
            recipient
            schemaId
            time
            data
        }
    }
    """
    try:
        r = httpx.post(
            graphql_url,
            json={"query": query, "variables": {"uid": uid}},
            timeout=15.0,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {
            "valid": False,
            "method": "onchain-graphql",
            "reason": f"GraphQL 调用失败: {e}",
        }

    att = (data or {}).get("data", {}).get("attestation")
    if not att:
        return {
            "valid": False,
            "method": "onchain-graphql",
            "reason": f"attestation 不在 {network} EAS 上 (未上链或 UID 错)",
            "raw": data,
        }
    return {
        "valid": True,
        "method": "onchain-graphql",
        "data": att,
    }


# ── 历史 (链上 + 本地) ───────────────────────────────────────────────────────


def list_history_local(queue: AttestQueue, limit: int = 50) -> list[BatchResult]:
    """本地 batches 列表."""
    return queue.list_batches(limit=limit)


def list_history_onchain(
    attester: str | None = None,
    network: Network = "optimism-sepolia",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """链上 attestation 历史 (EAS GraphQL).

    attester=None → 不过滤 (全 schema 历史).
    attester="0xabc..." → 按 attester 地址过滤.

    本 wave: 直接查 Optimism Sepolia EAS GraphQL.
    """
    if network == "mock":
        return []
    if network in MAINNET_BLOCKED_CHAINS:
        raise NetworkNotSupportedError(f"{network} 查询本 wave 禁用 (P3-5 mainnet hard gate)")

    try:
        import httpx  # type: ignore[import-not-found]
    except ImportError as e:
        raise EASError("httpx 未装") from e

    graphql_url = _easscan_graphql_url(network)
    where: dict[str, Any] = {}
    if attester:
        where["attester"] = {"equals": attester}
    query = """
    query ListAttestations($where: AttestationWhereInput, $take: Int!) {
        attestations(where: $where, take: $take, orderBy: {time: desc}) {
            id
            attester
            recipient
            schemaId
            time
        }
    }
    """
    try:
        r = httpx.post(
            graphql_url,
            json={
                "query": query,
                "variables": {"where": where, "take": limit},
            },
            timeout=15.0,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise EASError(f"GraphQL 历史查询失败: {e}") from e

    return (data or {}).get("data", {}).get("attestations") or []


# ── DID 集成: attester DID resolver ──────────────────────────────────────────


def resolve_attester_did(config: AttestConfig, vault_dir: Path | None = None) -> str:
    """解析 attester DID. 优先 config.attester_did, 否则取本地 DID registry 第一条.

    用 dev-B 波 3 ship 的 sisoul.identity.did.list_local_dids().
    """
    if config.attester_did:
        return config.attester_did

    try:
        from sisoul.identity.did import list_local_dids  # noqa
    except Exception as e:
        raise EASError(
            f"无法 import sisoul.identity.did (波 3 dev-B DID 模块): {e}"
        ) from e

    registry_path = None
    if vault_dir:
        registry_path = vault_dir / "identity" / "dids.json"
    dids = list_local_dids(registry_path=registry_path)
    if not dids:
        raise EASError(
            "本地无 DID. 先 `sisoul did register <handle>` 或在 config 中显式设 attester_did."
        )
    return dids[0].did_string


__all__ = [
    # 常量 / 类型
    "OPTIMISM_SEPOLIA_CHAIN_ID",
    "OPTIMISM_SEPOLIA_DEFAULT_RPC",
    "EAS_CONTRACT_OPTIMISM_SEPOLIA",
    "EAS_SCHEMA_REGISTRY_OPTIMISM_SEPOLIA",
    # P3-5 跨链
    "ARBITRUM_SEPOLIA_CHAIN_ID",
    "ARBITRUM_SEPOLIA_DEFAULT_RPC",
    "EAS_CONTRACT_ARBITRUM_SEPOLIA",
    "BASE_SEPOLIA_CHAIN_ID",
    "BASE_SEPOLIA_DEFAULT_RPC",
    "EAS_CONTRACT_BASE_SEPOLIA",
    "ZKSYNC_SEPOLIA_CHAIN_ID",
    "ZKSYNC_SEPOLIA_DEFAULT_RPC",
    "EAS_CONTRACT_ZKSYNC_SEPOLIA",
    "MAINNET_BLOCKED_CHAINS",
    "CHAIN_ID_BY_NETWORK",
    "SHORT_TO_NETWORK",
    "CHAIN_REGISTRY",
    "ChainConfig",
    "resolve_chain",
    "SISOUL_AUDIT_SCHEMA",
    "MOCK_SCHEMA_UID",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_BATCH_TIMEOUT_SEC",
    "DEFAULT_ATTEST_QUEUE_DB",
    "DEFAULT_ATTEST_CONFIG",
    "Network",
    # 异常
    "EASError",
    "NetworkNotSupportedError",
    "AttestationNotFoundError",
    "QueueEmptyError",
    "ConfigError",
    # 数据
    "AuditAttestation",
    "BatchResult",
    "AttestConfig",
    # config
    "load_config",
    "save_config",
    # queue
    "AttestQueue",
    # batch
    "upload_batch",
    "encode_attestation_data",
    "compute_attestation_uid",
    # verify / history
    "verify_attestation_local",
    "verify_attestation_onchain",
    "list_history_local",
    "list_history_onchain",
    # DID
    "resolve_attester_did",
]
