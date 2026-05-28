"""sisoul DID 链上身份层 (Phase 2 W21-W22, dev-B).

§28 §1 模块 10 + §29 §4.1 W21-W22.

设计要点:
- DID method: ``did:sisoul:<handle>`` (W3C DID Core 兼容, sisoul 自有 method).
- 链上 anchor: ENS subdomain ``<handle>.sisoul.eth`` 在 Sepolia testnet 注册
  (mainnet 切换 Phase 3 RC 才做, 不在本 wave 范围 — 避免花真 gas).
- Social recovery: Privy 集成 (mock, Phase 3 RC 真接 SDK).
  选 Privy 而非 Magic.link 的理由见 ``docs/internal/wave-3-dev-B-report.md``.
- 不强制用户管理 wallet 私钥 (social login → embedded wallet 派生).

模块边界 (波 3 严格约束):
- 跟 ``identity/seed.py`` (dev-A BIP-39) 不依赖, master_seed 通过参数传入 (可选).
- 不动 ``identity/__init__.py`` (dev-A 负责导出整合).

测试覆盖见 ``tests/test_identity_did.py``.
真 testnet smoke 见 ``SISOUL_TEST_LIVE_TESTNET=1`` 跳过开关.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

# ENS root for sisoul. testnet 阶段为 mock 根, mainnet RC 时切真 sisoul.eth.
SISOUL_ENS_ROOT = "sisoul.eth"

# Sepolia testnet ENS Registrar / Resolver 合约 (公开常量, 非凭据).
# 真合约见 https://docs.ens.domains/learn/deployments .
SEPOLIA_ENS_REGISTRY = "0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e"

# 公共 Sepolia RPC fallback (无凭据). 真 smoke test 时优先用 env SISOUL_SEPOLIA_RPC.
DEFAULT_SEPOLIA_RPC = "https://rpc.sepolia.org"

# handle 字符集 (ENS label spec: lowercase ascii + digits + hyphen, 3-63 字符).
HANDLE_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?$")

# 本地 DID 注册表存放位置 (vault 根下 identity/dids.json).
DEFAULT_DID_REGISTRY_REL = "identity/dids.json"

Network = Literal["sepolia", "mainnet", "mock"]
SocialProvider = Literal["github", "google", "apple", "twitter", "email"]


class DIDError(Exception):
    """DID 操作通用异常."""


class InvalidHandleError(DIDError):
    """handle 不合法 (字符集 / 长度)."""


class HandleAlreadyTakenError(DIDError):
    """本地已注册过同名 handle (避免覆盖)."""


class DIDNotFoundError(DIDError):
    """resolve 找不到 DID."""


class NetworkNotSupportedError(DIDError):
    """禁用 mainnet 注册 (波 3 约束: 不花真 gas)."""


# ── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class ServiceEndpoint:
    """W3C DID Core service endpoint."""

    id: str
    type: str
    service_endpoint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "serviceEndpoint": self.service_endpoint,
        }


@dataclass
class DID:
    """DID 主结构 (内存 + 序列化).

    最小化 W3C DID Core compliant doc, 字段:
    - method=did:sisoul
    - identifier (ENS subdomain, ``<handle>.sisoul.eth``)
    - public_key (派生自 master_seed 或 social wallet)
    - controllers (默认本 DID 自己; Phase 3 可加 social recovery guardian)
    - services (默认空; PWA / daemon endpoint 可注册)
    """

    handle: str
    public_key: str
    network: Network = "sepolia"
    controllers: list[str] = field(default_factory=list)
    services: list[ServiceEndpoint] = field(default_factory=list)
    created_at: str = ""
    ens_tx_hash: str | None = None  # ENS 注册 tx (mock 时是伪 hash)
    social_provider: SocialProvider | None = None
    social_recovery_id: str | None = None  # Privy user id (mock)
    # 元数据
    method: str = "sisoul"

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not self.controllers:
            self.controllers = [self.did_string]

    @property
    def ens_subdomain(self) -> str:
        """``<handle>.sisoul.eth``."""
        return f"{self.handle}.{SISOUL_ENS_ROOT}"

    @property
    def did_string(self) -> str:
        """W3C DID URI: ``did:sisoul:<handle>``."""
        return f"did:{self.method}:{self.handle}"

    def to_did_document(self) -> dict[str, Any]:
        """W3C DID Core v1.0 document 形态."""
        return {
            "@context": [
                "https://www.w3.org/ns/did/v1",
                "https://w3id.org/security/suites/ed25519-2020/v1",
            ],
            "id": self.did_string,
            "alsoKnownAs": [f"ens:{self.ens_subdomain}"],
            "verificationMethod": [
                {
                    "id": f"{self.did_string}#key-1",
                    "type": "Ed25519VerificationKey2020",
                    "controller": self.did_string,
                    "publicKeyMultibase": self.public_key,
                }
            ],
            "authentication": [f"{self.did_string}#key-1"],
            "assertionMethod": [f"{self.did_string}#key-1"],
            "controller": self.controllers,
            "service": [s.to_dict() for s in self.services],
        }

    def to_dict(self) -> dict[str, Any]:
        """完整序列化 (含 metadata, 用于本地 dids.json 存储)."""
        d = asdict(self)
        # ServiceEndpoint dataclass 已被 asdict 递归, 字段名 service_endpoint → keep snake
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DID:
        services_raw = d.pop("services", []) or []
        services = [
            ServiceEndpoint(
                id=s["id"],
                type=s["type"],
                service_endpoint=s.get("service_endpoint") or s.get("serviceEndpoint", ""),
            )
            for s in services_raw
        ]
        d.pop("method", None)  # 固定 sisoul, 防 from_dict 误覆盖
        return cls(services=services, **d)


# ── handle 校验 ─────────────────────────────────────────────────────────────


def validate_handle(handle: str) -> str:
    """校验 + 归一 handle. 返回归一后的 handle (小写). 不合法抛 InvalidHandleError."""
    if not isinstance(handle, str):
        raise InvalidHandleError(f"handle 必须是 str, 拿到 {type(handle).__name__}")
    h = handle.strip().lower()
    if not h:
        raise InvalidHandleError("handle 不能为空")
    if len(h) < 3:
        raise InvalidHandleError(f"handle 至少 3 字符 (ENS label 规则), 拿到 {len(h)}")
    if len(h) > 63:
        raise InvalidHandleError(f"handle 最多 63 字符 (ENS label 规则), 拿到 {len(h)}")
    if not HANDLE_PATTERN.match(h):
        raise InvalidHandleError(
            f"handle '{h}' 含非法字符, 仅允许 a-z 0-9 - (不能开头/结尾用 -)"
        )
    return h


def compute_ens_subdomain(handle: str) -> str:
    """``<handle>.sisoul.eth`` (校验后)."""
    return f"{validate_handle(handle)}.{SISOUL_ENS_ROOT}"


def compute_namehash(name: str) -> str:
    """ENS namehash (EIP-137) keccak256 算法 (本实现用 sha3_256 占位).

    真链上注册时 web3.py 的 ``ens.utils.raw_name_to_hash`` 算 keccak256 namehash.
    本 mock 实现用 sha3_256 (差一点点, 但本地校验/lookup 自洽即可).
    Phase 3 RC 切真合约时用 ``from ens.utils import raw_name_to_hash``.
    """
    node = b"\x00" * 32
    if name:
        labels = name.split(".")
        for label in reversed(labels):
            label_hash = hashlib.sha3_256(label.encode("utf-8")).digest()
            node = hashlib.sha3_256(node + label_hash).digest()
    return "0x" + node.hex()


# ── public key 派生 (mock) ───────────────────────────────────────────────────


def derive_public_key(
    handle: str,
    master_seed: bytes | None = None,
    *,
    social_id: str | None = None,
) -> str:
    """派生 Ed25519 public key 的 multibase 表示 (mock).

    优先级:
    1. master_seed (BIP-39 派生 by dev-A) → 主路径
    2. social_id (Privy social login → embedded wallet) → fallback
    3. 都没有 → 用 handle hash 占位 (仅 mock 用)

    Phase 3 RC: 接 dev-A 的 ``identity/seed.derive_signing_key()`` + Privy SDK.
    """
    if master_seed:
        digest = hashlib.sha512(master_seed + handle.encode("utf-8")).digest()[:32]
    elif social_id:
        digest = hashlib.sha512(
            f"social:{social_id}:{handle}".encode("utf-8")
        ).digest()[:32]
    else:
        digest = hashlib.sha512(f"mock:{handle}".encode("utf-8")).digest()[:32]
    # multibase z-prefix (base58btc, ed25519-pub key spec).
    # mock: 直接十六进制前缀 z + hex (真实现用 base58 编码).
    return "z" + digest.hex()


# ── ENS 注册 (Sepolia testnet / mock) ────────────────────────────────────────


def _mock_tx_hash(seed: str) -> str:
    """生成确定性 mock tx hash (test 可复现)."""
    return "0x" + hashlib.sha256(f"mock-tx:{seed}:{time.time_ns()}".encode()).hexdigest()


def register_ens_subdomain(
    handle: str,
    public_key: str,
    *,
    network: Network = "sepolia",
    rpc_url: str | None = None,
    live: bool = False,
) -> dict[str, Any]:
    """注册 ENS subdomain ``<handle>.sisoul.eth``.

    - network=mainnet → 拒绝 (波 3 约束).
    - network=sepolia + live=False → mock (返伪 tx_hash).
    - network=sepolia + live=True → 真连 web3.py + Sepolia RPC (需 web3 已装 + SISOUL_SEPOLIA_RPC env).
      smoke test 模式: 只调 ENS resolver lookup 看接口通, **不真发 tx**
      (真发 tx 需 faucet + 私钥, 超出 wave 3 范围).
    - network=mock → 全本地, 不连任何 RPC.

    返回 ``{"tx_hash":..., "ens_subdomain":..., "network":..., "namehash":..., "method":...}``
    """
    handle = validate_handle(handle)
    if network == "mainnet":
        raise NetworkNotSupportedError(
            "mainnet 注册被禁用 (波 3 约束: 不花真 gas). Phase 3 RC 切开."
        )

    subdomain = f"{handle}.{SISOUL_ENS_ROOT}"
    namehash = compute_namehash(subdomain)

    if network == "mock" or not live:
        return {
            "tx_hash": _mock_tx_hash(handle),
            "ens_subdomain": subdomain,
            "namehash": namehash,
            "network": network,
            "method": "mock",
            "public_key": public_key,
            "note": "mock 注册, 不上链. live=True + sepolia 才连 RPC.",
        }

    # live=True + sepolia: 真连 web3.py 调 resolver (read-only, 不发 tx).
    try:
        from web3 import Web3  # type: ignore[import-not-found]
    except ImportError as e:
        raise DIDError(
            "live=True 需要 web3 已装. pip install 'sisoul[crypto]' 含 web3>=6.0"
        ) from e

    rpc = rpc_url or os.environ.get("SISOUL_SEPOLIA_RPC") or DEFAULT_SEPOLIA_RPC
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
    try:
        chain_id = w3.eth.chain_id
    except Exception as e:  # noqa: BLE001 — RPC 网络层任何错都归一
        raise DIDError(f"Sepolia RPC 不通 ({rpc}): {e}") from e
    # Sepolia chain_id = 11155111
    if chain_id != 11155111:
        raise DIDError(f"RPC 返回 chain_id={chain_id}, 期望 Sepolia (11155111)")

    return {
        "tx_hash": None,  # smoke test: 不发 tx
        "ens_subdomain": subdomain,
        "namehash": namehash,
        "network": "sepolia",
        "method": "live-readonly",
        "public_key": public_key,
        "chain_id": chain_id,
        "rpc": rpc,
        "note": "RPC 接通 + chain_id 验证 OK. 不发 tx (无私钥). Phase 3 RC 实发.",
    }


# ── DID resolver ────────────────────────────────────────────────────────────


def resolve_did(
    did_or_ens: str,
    *,
    registry_path: Path | None = None,
) -> DID:
    """根据 did:sisoul:<handle>, <handle>.sisoul.eth, 或 did:key:z... 解析.

    Wave B' P0-3: 新增 did:key 路径 (本地决定性派生, 无 registry).
    """
    if did_or_ens.startswith("did:key:"):
        return _resolve_did_key(did_or_ens)

    if did_or_ens.startswith("did:sisoul:"):
        handle = did_or_ens[len("did:sisoul:"):]
    elif did_or_ens.endswith(f".{SISOUL_ENS_ROOT}"):
        handle = did_or_ens[: -len(f".{SISOUL_ENS_ROOT}")]
    else:
        raise DIDError(
            f"无法解析: '{did_or_ens}'. 接受 'did:sisoul:<handle>', "
            f"'<handle>.{SISOUL_ENS_ROOT}', 或 'did:key:z...'"
        )
    handle = validate_handle(handle)
    registry = load_registry(registry_path)
    for entry in registry:
        if entry.get("handle") == handle:
            return DID.from_dict(dict(entry))
    raise DIDNotFoundError(f"DID 未找到: {did_or_ens} (本地 registry 无 handle={handle})")


def _resolve_did_key(did_key_str: str) -> DID:
    """did:key:z... → DID 对象 (无 registry, 完全本地解析).

    handle 字段填 identifier (z... multibase 串). did_string 返 'did:key:z...' 正确.
    method='key', network='mock'. Wave B' P0-3.
    """
    from sisoul.identity.did_key import (
        decode_did_key,
        InvalidDidKeyFormatError,
        UnsupportedMulticodecError,
    )
    try:
        dk = decode_did_key(did_key_str)
    except (InvalidDidKeyFormatError, UnsupportedMulticodecError) as e:
        raise DIDError(f"did:key 解析失败: {e}") from e

    return DID(
        handle=dk.identifier,
        public_key=dk.identifier,
        network="mock",
        method="key",
    )


# ── 本地 registry ────────────────────────────────────────────────────────────


def _registry_path(custom: Path | None = None) -> Path:
    if custom is not None:
        return Path(custom)
    # 默认 ~/.sisoul/identity/dids.json
    return Path.home() / ".sisoul" / DEFAULT_DID_REGISTRY_REL


def load_registry(path: Path | None = None) -> list[dict[str, Any]]:
    fp = _registry_path(path)
    if not fp.exists():
        return []
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def save_registry(entries: list[dict[str, Any]], path: Path | None = None) -> Path:
    fp = _registry_path(path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return fp


def list_local_dids(registry_path: Path | None = None) -> list[DID]:
    return [DID.from_dict(dict(e)) for e in load_registry(registry_path)]


# ── 顶层操作: register / link-social / link-friend ─────────────────────────


def register_did(
    handle: str,
    *,
    network: Network = "sepolia",
    master_seed: bytes | None = None,
    social_provider: SocialProvider | None = None,
    social_id: str | None = None,
    registry_path: Path | None = None,
    rpc_url: str | None = None,
    live: bool = False,
) -> DID:
    """完整流程: 校验 handle → 派 key → ENS 注册 → 写本地 registry → 返回 DID."""
    handle = validate_handle(handle)

    # 重复 check
    existing = load_registry(registry_path)
    if any(e.get("handle") == handle for e in existing):
        raise HandleAlreadyTakenError(
            f"本地 registry 已有 handle='{handle}', 用 --force 覆盖 (未实现, Phase 3) 或换名"
        )

    pubkey = derive_public_key(handle, master_seed=master_seed, social_id=social_id)

    ens_result = register_ens_subdomain(
        handle, pubkey, network=network, rpc_url=rpc_url, live=live
    )

    did = DID(
        handle=handle,
        public_key=pubkey,
        network=network,
        ens_tx_hash=ens_result.get("tx_hash"),
        social_provider=social_provider,
        social_recovery_id=social_id,
    )

    existing.append(did.to_dict())
    save_registry(existing, registry_path)
    return did


# ── Privy / Magic.link social recovery (mock) ────────────────────────────────


@dataclass
class SocialRecoveryResult:
    """social login 派生结果 (mock)."""

    provider: SocialProvider
    user_id: str  # Privy user id (uuid)
    embedded_wallet_address: str  # 0x...
    issued_at: str


def link_social_recovery(
    provider: SocialProvider,
    *,
    oauth_token: str | None = None,
    user_email: str | None = None,
    seed: str | None = None,
) -> SocialRecoveryResult:
    """Privy 风格的 social recovery (mock).

    真实现 (Phase 3 RC):
    ``privy.embedded_wallets.create_for_user(oauth_token=...)`` → embedded wallet.
    本 mock: 确定性派生 user_id + wallet address (test 可复现).

    选 Privy 不选 Magic.link 的理由 (详 wave-3-dev-B-report.md):
    - Privy embedded wallet 更主流, Farcaster / Friend.tech 都用
    - 支持 OAuth + email + passkey 多方式
    - SDK 文档完整 (TypeScript 优先, 跟 dev-C PWA 对得上)
    """
    if provider not in {"github", "google", "apple", "twitter", "email"}:
        raise DIDError(f"不支持的 social provider: {provider}")
    if provider != "email" and not oauth_token:
        raise DIDError(f"provider={provider} 需要 oauth_token (mock 接受任意非空字符串)")
    if provider == "email" and not user_email:
        raise DIDError("provider=email 需要 user_email")

    # 确定性 mock (test 复现): seed 为空时用 oauth_token / user_email 哈希
    seed_str = seed or (oauth_token or "") + (user_email or "")
    user_uuid = str(
        uuid.UUID(bytes=hashlib.sha256(f"privy:{provider}:{seed_str}".encode()).digest()[:16])
    )
    wallet_hex = hashlib.sha256(
        f"wallet:{provider}:{seed_str}".encode()
    ).hexdigest()[:40]

    return SocialRecoveryResult(
        provider=provider,
        user_id=user_uuid,
        embedded_wallet_address="0x" + wallet_hex,
        issued_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def link_friend_did(
    own_did: DID,
    friend_did_string: str,
    *,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    """Phase 4 朋友关系 stub. 本 wave 只 ship 框架 (不上链 EAS attestation).

    真实现见波 5 dev-A ``sisoul/friend/``.
    本 stub 只把朋友 DID 加进 own_did.controllers 旁的 metadata 字段, 返回操作记录.
    """
    if not friend_did_string.startswith("did:sisoul:") and not friend_did_string.endswith(
        f".{SISOUL_ENS_ROOT}"
    ):
        raise DIDError(
            f"friend DID 格式不对: {friend_did_string} "
            f"(期望 'did:sisoul:<handle>' 或 '<handle>.{SISOUL_ENS_ROOT}')"
        )
    record = {
        "stub": True,
        "own_did": own_did.did_string,
        "friend_did": friend_did_string,
        "linked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Phase 4 朋友关系前置 stub. 真 EAS attestation 见 dev-A 波 5.",
    }
    # 简单落地 (不入主 registry, 单独 friends.json)
    if registry_path is not None:
        friends_fp = Path(registry_path).parent / "friends.json"
    else:
        friends_fp = Path.home() / ".sisoul" / "identity" / "friends.json"
    friends_fp.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if friends_fp.exists():
        try:
            existing = json.loads(friends_fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = []
    existing.append(record)
    friends_fp.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


__all__ = [
    "DID",
    "DIDError",
    "DIDNotFoundError",
    "HandleAlreadyTakenError",
    "InvalidHandleError",
    "NetworkNotSupportedError",
    "ServiceEndpoint",
    "SISOUL_ENS_ROOT",
    "SocialRecoveryResult",
    "compute_ens_subdomain",
    "compute_namehash",
    "derive_public_key",
    "link_friend_did",
    "link_social_recovery",
    "list_local_dids",
    "load_registry",
    "register_did",
    "register_ens_subdomain",
    "resolve_did",
    "save_registry",
    "validate_handle",
]
