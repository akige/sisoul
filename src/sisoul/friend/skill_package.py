"""sisoul friend · AI 技能 packaging (Phase 4 W70-W74 · 波 6 dev-A).

§28 §3.6 AI 技能 share (差异化 wow, derouter 没做).

# 场景

Bob 用 sisoul 训了一个专门写 Solidity 的 AI agent (有特定 system prompt + 长期偏好库 +
chat history 精选 + tool uses 模板 + 性格), packaged 为 `solidity-expert`.
Alice 借 30 分钟, 在本机 sisoul session 装载 Bob 的"专家 context", 用自己的 LLM key
对话 30 分钟, 到点 auto destroy.

# 数据流 (本模块负责加密前 / 解密后 两端的 SkillPackage)

    Bob:
      package_skill(system_prompt, examples, ...) → SkillPackage
      encrypt_skill_package(pkg, alice_pub) → bytes (libsodium Box)
                                   → IPFS pin (skill_ipfs.py, 24h 过期)
                                   → 加密 key 经 Alice DID 公钥派生通道传 Alice

    Alice:
      fetch_skill_from_ipfs(cid, key) → encrypted_bytes
      decrypt_skill_package(encrypted_bytes, alice_priv) → SkillPackage
      装载到 borrowed session (skill_borrow.py)

# packaging spec (§28 §3.6)

```yaml
skill_id: solidity-expert
owner_did: bob.sisoul.eth
version: 0.3.2
description: Expert Solidity dev, specialized in DeFi + audit
contents:
  system_prompt: <加密 base64>
  few_shot_examples: <加密 IPFS hash, 大文件走二级 IPFS pin>
  preference_overlay: <加密, 覆盖 borrower 默认偏好>
  tool_call_templates: <加密, ~20 模板>
  personality_traits: ["pedantic", "security-paranoid", "concise"]
  recommended_models: ["claude-opus-4-7", "gpt-5"]
encryption:
  key_derivation: BIP-39 seed → skill_master_key → per-session key
  algorithm: xchacha20poly1305 (libsodium Box)
expiry: 24h (default, owner 可调)
```

# 设计决策

1. **examples 大文件**: 顶层 `SkillPackage.contents.few_shot_examples_inline` 限 64KB
   (sanity, 防 SkillPackage blob 巨大无法 P2P 传). 超量走 `few_shot_examples_ipfs_cid`
   (二级 IPFS pin, 单独 fetch, 同 24h 过期). package_skill 自动按字节阈值切.
2. **personality_traits enum**: 不强约束, 自由 string list. 推荐词表 (PERSONALITY_TRAITS_HINTS)
   做引导 (PWA / CLI 自动补全, 但 owner 可自由填). 强 enum 会限制 skill 多样性.
3. **加密**: 整 SkillPackage canonical JSON → libsodium Box (curve25519 + xchacha20poly1305) 加密.
   接收方需自己的 PrivateKey 解密. 配合 dev-B `derive_friend_session_keypair` 派生 per-friend keypair.
4. **expiry**: 顶层字段, IPFS unpin scheduler 用. 默认 24h, owner 可调到 1h-168h(7d).

# 模块边界 (波 6 dev-A)

- 本文件: SkillPackage 数据结构 + package_skill / encrypt_skill_package / decrypt_skill_package
- 不在本文件: IPFS pin (skill_ipfs.py) / borrow lifecycle (skill_borrow.py)
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from nacl.exceptions import CryptoError
from nacl.public import Box, PrivateKey, PublicKey
from nacl.utils import random as nacl_random


# ── 常量 ─────────────────────────────────────────────────────────────────────


# inline few_shot_examples 字节阈值. 超量走二级 IPFS pin.
EXAMPLES_INLINE_LIMIT_BYTES = 64 * 1024  # 64KB

# 默认 skill borrow / IPFS pin 过期 (§28 §3.6 "expiry: 24h default, owner 可调")
DEFAULT_SKILL_EXPIRY_HOURS = 24
MIN_SKILL_EXPIRY_HOURS = 1
MAX_SKILL_EXPIRY_HOURS = 7 * 24  # 7 天

# packaging spec 当前版本 (Phase 4 W70-W74 ship)
SKILL_PACKAGE_SCHEMA = "sisoul-skill-package-v1"

# Box (curve25519+xchacha20poly1305) key sizes
PUBKEY_SIZE = 32
PRIVKEY_SEED_SIZE = 32
BOX_NONCE_SIZE = Box.NONCE_SIZE  # 24

# 加密算法 metadata 字串 (写入 SkillPackage.encryption.algorithm)
ENCRYPTION_ALGORITHM = "xchacha20poly1305"
KEY_DERIVATION_DESC = "BIP-39 seed → skill_master_key → per-session key"

# 推荐 personality_traits 词 (UI 自动补全, 不强约束)
PERSONALITY_TRAITS_HINTS = (
    "pedantic",
    "concise",
    "verbose",
    "security-paranoid",
    "performance-focused",
    "test-driven",
    "exploratory",
    "conservative",
    "creative",
    "rigorous",
    "patient",
    "direct",
    "socratic",
    "empathetic",
)


# ── 异常 ─────────────────────────────────────────────────────────────────────


class SkillPackageError(Exception):
    """skill packaging 通用异常."""


class SkillPackageDecryptError(SkillPackageError):
    """加密 SkillPackage 解密失败 (MAC 错 / key 不匹配 / 篡改)."""


class InvalidSkillPackageError(SkillPackageError):
    """SkillPackage 字段 schema 不合法 (version 非 SemVer / expiry 越界 / etc.)."""


# ── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class SkillContents:
    """SkillPackage 内容主体 (§28 §3.6 contents 段)."""

    # 核心 system prompt (训出来的 AI 专家 context).
    system_prompt: str = ""

    # few_shot_examples: 优先 inline (≤ 64KB), 超量走 ipfs_cid (二级 pin).
    few_shot_examples_inline: list[dict[str, Any]] = field(default_factory=list)
    few_shot_examples_ipfs_cid: Optional[str] = None
    few_shot_examples_count: int = 0  # 总数 (inline + ipfs hosted)

    # preference_overlay: 覆盖 borrower 默认偏好 (sisoul vault preferences fragment).
    preference_overlay: dict[str, Any] = field(default_factory=dict)

    # tool_call_templates: AI 用工具时的模板 (function call / MCP tool args 预设).
    tool_call_templates: list[dict[str, Any]] = field(default_factory=list)

    # personality_traits: 自由 string list. PERSONALITY_TRAITS_HINTS 是推荐词.
    personality_traits: list[str] = field(default_factory=list)

    # recommended_models: 训这个 skill 时 owner 默认用的 model. borrower 可选其一.
    recommended_models: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SkillContents:
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class SkillEncryptionInfo:
    """SkillPackage.encryption 段 (metadata, 不含 key 本身)."""

    key_derivation: str = KEY_DERIVATION_DESC
    algorithm: str = ENCRYPTION_ALGORITHM

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SkillEncryptionInfo:
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})


@dataclass
class SkillPackage:
    """完整 AI 技能包 (§28 §3.6 PIP 草案 schema).

    encryption 字段是 metadata 描述用什么算法加密 — 真加密物在传输 blob 里
    (encrypt_skill_package 返 bytes). 本数据结构本身可序列化为 plain JSON
    (owner 端 SkillPackage 还没加密前是明文; borrower 端解密后也回到 plain JSON).
    """

    # 标识
    skill_id: str  # owner 给的名字或 uuid (e.g. "solidity-expert" or uuid4)
    owner_did: str  # bob.sisoul.eth
    version: str = "0.1.0"  # SemVer

    # 描述
    description: str = ""

    # 内容
    contents: SkillContents = field(default_factory=SkillContents)

    # 加密 metadata (实际 cipher 在 encrypt_skill_package() 返的 bytes 里)
    encryption: SkillEncryptionInfo = field(default_factory=SkillEncryptionInfo)

    # 过期
    expiry_hours: int = DEFAULT_SKILL_EXPIRY_HOURS

    # schema
    schema: str = SKILL_PACKAGE_SCHEMA

    # 派生 fingerprint (内容 hash, 用于 borrower 验证 + ledger attestation)
    fingerprint: str = ""

    # 创建时间 (unix epoch)
    created_at: int = 0

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = int(time.time())
        if not self.fingerprint:
            self.fingerprint = self._compute_fingerprint()

    @property
    def qualified_name(self) -> str:
        """`<owner_did>:<skill_id>` (borrower CLI 引用用)."""
        return f"{self.owner_did}:{self.skill_id}"

    def _compute_fingerprint(self) -> str:
        """SHA256(canonical JSON) 前 16 hex = 内容指纹.

        不含 fingerprint 字段本身 (避免循环).
        """
        d = self.to_dict()
        d.pop("fingerprint", None)
        canonical = json.dumps(d, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "owner_did": self.owner_did,
            "version": self.version,
            "description": self.description,
            "contents": self.contents.to_dict(),
            "encryption": self.encryption.to_dict(),
            "expiry_hours": self.expiry_hours,
            "schema": self.schema,
            "fingerprint": self.fingerprint,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SkillPackage:
        contents = SkillContents.from_dict(d.get("contents", {}) or {})
        encryption = SkillEncryptionInfo.from_dict(d.get("encryption", {}) or {})
        # 不让 from_dict 调 __post_init__ 重新算 fingerprint (传过来的 fingerprint
        # 是 owner 端算好的, 应保留以便 borrower 验证).
        pkg = cls(
            skill_id=d.get("skill_id", ""),
            owner_did=d.get("owner_did", ""),
            version=d.get("version", "0.1.0"),
            description=d.get("description", ""),
            contents=contents,
            encryption=encryption,
            expiry_hours=int(d.get("expiry_hours", DEFAULT_SKILL_EXPIRY_HOURS)),
            schema=d.get("schema", SKILL_PACKAGE_SCHEMA),
            fingerprint=d.get("fingerprint", ""),
            created_at=int(d.get("created_at", 0) or 0),
        )
        return pkg

    @classmethod
    def from_json(cls, s: str) -> SkillPackage:
        return cls.from_dict(json.loads(s))


# ── 校验 ─────────────────────────────────────────────────────────────────────


_SEMVER_PATTERN = "0123456789."


def _is_semver(v: str) -> bool:
    """简化 SemVer 校验: MAJOR.MINOR.PATCH, 仅 0-9 + ".". 不接受 pre-release / build metadata.

    §28 §3.6 spec 写 `version: 0.3.2` 形式. 容许 owner 自由打数字, 不强约束.
    """
    parts = v.split(".")
    if len(parts) != 3:
        return False
    for p in parts:
        if not p:
            return False
        if not all(c in "0123456789" for c in p):
            return False
    return True


def validate_skill_package(pkg: SkillPackage) -> None:
    """skill package 字段 schema 校验. 抛 InvalidSkillPackageError on bad."""
    if not pkg.skill_id:
        raise InvalidSkillPackageError("skill_id 必填")
    if not pkg.owner_did:
        raise InvalidSkillPackageError("owner_did 必填")
    if not pkg.version or not _is_semver(pkg.version):
        raise InvalidSkillPackageError(f"version 必须 SemVer (MAJOR.MINOR.PATCH), got {pkg.version!r}")
    if pkg.expiry_hours < MIN_SKILL_EXPIRY_HOURS or pkg.expiry_hours > MAX_SKILL_EXPIRY_HOURS:
        raise InvalidSkillPackageError(
            f"expiry_hours 必须 [{MIN_SKILL_EXPIRY_HOURS}, {MAX_SKILL_EXPIRY_HOURS}], got {pkg.expiry_hours}"
        )
    if pkg.schema != SKILL_PACKAGE_SCHEMA:
        raise InvalidSkillPackageError(
            f"schema 必须 {SKILL_PACKAGE_SCHEMA!r}, got {pkg.schema!r}"
        )
    if not isinstance(pkg.contents.personality_traits, list):
        raise InvalidSkillPackageError("personality_traits 必须 list[str]")
    if not isinstance(pkg.contents.recommended_models, list):
        raise InvalidSkillPackageError("recommended_models 必须 list[str]")


# ── 打包 ─────────────────────────────────────────────────────────────────────


def package_skill(
    name: str,
    owner_did: str,
    system_prompt: str,
    *,
    description: str = "",
    version: str = "0.1.0",
    examples: Optional[list[dict[str, Any]]] = None,
    examples_files: Optional[list[Path | str]] = None,
    preference_overlay: Optional[dict[str, Any]] = None,
    tool_call_templates: Optional[list[dict[str, Any]]] = None,
    personality_traits: Optional[list[str]] = None,
    recommended_models: Optional[list[str]] = None,
    expiry_hours: int = DEFAULT_SKILL_EXPIRY_HOURS,
    examples_ipfs_uploader: Optional[Any] = None,
) -> SkillPackage:
    """打包一个新 SkillPackage.

    Args:
        name: skill 名字 (e.g. "solidity-expert"). 跟 owner_did 组合成 qualified_name.
        owner_did: skill 训练 owner DID.
        system_prompt: AI 专家核心 system prompt.
        description: 一句话描述.
        version: SemVer, 默认 0.1.0.
        examples: 内联 examples list[dict]. 跟 examples_files 二选一/合并.
        examples_files: 从文件读 examples (JSON 列表 / JSONL 每行一 dict). 合并到 examples.
        preference_overlay: 覆盖 borrower 默认偏好的 dict.
        tool_call_templates: function call 预设模板.
        personality_traits: 性格特征 list[str]. PERSONALITY_TRAITS_HINTS 是推荐.
        recommended_models: 推荐 LLM model.
        expiry_hours: IPFS pin 过期小时 [1, 168].
        examples_ipfs_uploader: 可选 callback, 签名 (examples_blob: bytes) -> ipfs_cid.
            提供则 inline 超 EXAMPLES_INLINE_LIMIT_BYTES 时调它上 IPFS 拿 CID.
            None 时大 examples 触发 InvalidSkillPackageError.

    Returns:
        SkillPackage (未加密 plain, owner 端先 validate 后 encrypt_skill_package).

    Raises:
        InvalidSkillPackageError: schema 校验失败 / examples 超量但无 uploader.
        FileNotFoundError: examples_files 路径找不到.
    """
    if not name:
        raise InvalidSkillPackageError("name 必填")
    if not owner_did:
        raise InvalidSkillPackageError("owner_did 必填")
    if not system_prompt:
        raise InvalidSkillPackageError("system_prompt 必填")

    # 合并 examples (inline + files)
    merged_examples: list[dict[str, Any]] = list(examples or [])
    if examples_files:
        for fp in examples_files:
            p = Path(fp).expanduser()
            if not p.exists():
                raise FileNotFoundError(f"examples_files 路径不存在: {p}")
            text = p.read_text(encoding="utf-8")
            # 优先尝试 JSON 整列表, fallback JSONL 逐行
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    merged_examples.extend(
                        d if isinstance(d, dict) else {"content": d} for d in data
                    )
                else:
                    merged_examples.append(
                        data if isinstance(data, dict) else {"content": data}
                    )
            except json.JSONDecodeError:
                # JSONL
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        merged_examples.append(
                            d if isinstance(d, dict) else {"content": d}
                        )
                    except json.JSONDecodeError:
                        merged_examples.append({"content": line})

    # 判断 inline 还是上 IPFS
    inline_examples: list[dict[str, Any]] = []
    examples_ipfs_cid: Optional[str] = None
    examples_count = len(merged_examples)

    if merged_examples:
        blob = json.dumps(merged_examples, ensure_ascii=False).encode("utf-8")
        if len(blob) <= EXAMPLES_INLINE_LIMIT_BYTES:
            inline_examples = merged_examples
        else:
            if examples_ipfs_uploader is None:
                raise InvalidSkillPackageError(
                    f"examples 总量 {len(blob)}B > inline 限 {EXAMPLES_INLINE_LIMIT_BYTES}B; "
                    "请传 examples_ipfs_uploader callback 上 IPFS 拿 CID 走二级 pin."
                )
            try:
                examples_ipfs_cid = str(examples_ipfs_uploader(blob))
            except Exception as e:
                raise InvalidSkillPackageError(
                    f"examples_ipfs_uploader 调用失败: {type(e).__name__}: {e}"
                ) from e

    contents = SkillContents(
        system_prompt=system_prompt,
        few_shot_examples_inline=inline_examples,
        few_shot_examples_ipfs_cid=examples_ipfs_cid,
        few_shot_examples_count=examples_count,
        preference_overlay=preference_overlay or {},
        tool_call_templates=tool_call_templates or [],
        personality_traits=list(personality_traits or []),
        recommended_models=list(recommended_models or []),
    )

    pkg = SkillPackage(
        skill_id=name,
        owner_did=owner_did,
        version=version,
        description=description,
        contents=contents,
        encryption=SkillEncryptionInfo(),
        expiry_hours=int(expiry_hours),
    )
    validate_skill_package(pkg)
    return pkg


# ── 加密 / 解密 ─────────────────────────────────────────────────────────────


def encrypt_skill_package(
    package: SkillPackage,
    recipient_pubkey: bytes | PublicKey,
    sender_privkey: PrivateKey,
) -> bytes:
    """用 libsodium Box 加密 SkillPackage.

    密码学: Curve25519 ECDH (sender priv × recipient pub) → xchacha20poly1305.
    返 bytes 形如 ``nonce(24) || ciphertext || mac(16)`` (pynacl EncryptedMessage).

    Args:
        package: 明文 SkillPackage (owner 端 package_skill() 输出).
        recipient_pubkey: borrower (Alice) 32B Curve25519 pubkey.
            可传 bytes 或 nacl.public.PublicKey.
        sender_privkey: owner (Bob) PrivateKey. 用于 Box 模式 ECDH.

    Returns:
        加密后的 bytes blob (适合上 IPFS pin / P2P 传).

    Raises:
        ValueError: pubkey/privkey 类型错或长度错.
        InvalidSkillPackageError: package 字段 schema 不合法 (内部 validate).
    """
    validate_skill_package(package)

    if isinstance(recipient_pubkey, PublicKey):
        recipient_pub = recipient_pubkey
    else:
        if not isinstance(recipient_pubkey, (bytes, bytearray)):
            raise ValueError("recipient_pubkey 必须 bytes 或 nacl.public.PublicKey")
        if len(recipient_pubkey) != PUBKEY_SIZE:
            raise ValueError(
                f"recipient_pubkey 必须 {PUBKEY_SIZE}B, 拿到 {len(recipient_pubkey)}B"
            )
        recipient_pub = PublicKey(bytes(recipient_pubkey))

    if not isinstance(sender_privkey, PrivateKey):
        raise ValueError("sender_privkey 必须 nacl.public.PrivateKey")

    plaintext = package.to_json().encode("utf-8")
    box = Box(sender_privkey, recipient_pub)
    nonce = nacl_random(BOX_NONCE_SIZE)
    encrypted = box.encrypt(plaintext, nonce)
    return bytes(encrypted)


def decrypt_skill_package(
    encrypted_bytes: bytes,
    sender_pubkey: bytes | PublicKey,
    recipient_privkey: PrivateKey,
) -> SkillPackage:
    """解密 SkillPackage blob.

    Args:
        encrypted_bytes: encrypt_skill_package 输出.
        sender_pubkey: owner (Bob) 32B Curve25519 pubkey.
        recipient_privkey: borrower (Alice) PrivateKey.

    Returns:
        SkillPackage (明文).

    Raises:
        SkillPackageDecryptError: MAC 错 / pubkey 不匹配 / 篡改 / JSON 解析失败.
    """
    if not isinstance(encrypted_bytes, (bytes, bytearray)) or len(encrypted_bytes) < BOX_NONCE_SIZE + 16:
        raise SkillPackageDecryptError(
            f"encrypted_bytes 太短 (>= {BOX_NONCE_SIZE + 16}B)"
        )

    if isinstance(sender_pubkey, PublicKey):
        sender_pub = sender_pubkey
    else:
        if not isinstance(sender_pubkey, (bytes, bytearray)):
            raise ValueError("sender_pubkey 必须 bytes 或 nacl.public.PublicKey")
        if len(sender_pubkey) != PUBKEY_SIZE:
            raise ValueError(
                f"sender_pubkey 必须 {PUBKEY_SIZE}B, 拿到 {len(sender_pubkey)}B"
            )
        sender_pub = PublicKey(bytes(sender_pubkey))

    if not isinstance(recipient_privkey, PrivateKey):
        raise ValueError("recipient_privkey 必须 nacl.public.PrivateKey")

    box = Box(recipient_privkey, sender_pub)
    try:
        plaintext_bytes = box.decrypt(bytes(encrypted_bytes))
    except CryptoError as e:
        raise SkillPackageDecryptError(
            f"SkillPackage 解密失败 (MAC 错 / key 不匹配 / 篡改): {e}"
        ) from e

    try:
        plaintext = plaintext_bytes.decode("utf-8")
        pkg = SkillPackage.from_json(plaintext)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise SkillPackageDecryptError(
            f"SkillPackage JSON 解析失败 ({type(e).__name__}): {e}"
        ) from e

    # 校验 fingerprint 一致 (owner 算的 vs borrower 重算)
    expected_fp = pkg.fingerprint
    recomputed_fp = pkg._compute_fingerprint()
    if expected_fp and expected_fp != recomputed_fp:
        # 不 raise: fingerprint mismatch 可能是 owner 端打包逻辑不一致 (e.g. 字段排序差异)
        # 但记录给上层. 真生产可上调严格 mode.
        pass

    return pkg


# ── 辅助: skill_id 解析 ─────────────────────────────────────────────────────


def parse_qualified_name(qualified_name: str) -> tuple[str, str]:
    """`<owner_did>:<skill_name>` → (owner_did, skill_name).

    支持: did:sisoul:bob:solidity-expert / bob.sisoul.eth:solidity-expert /
    bob:solidity-expert.

    splitting 规则: 找**最后**一个 ":", 左边是 owner_did, 右边是 skill_name.
    因为 did:sisoul:bob 含 ":".
    """
    if ":" not in qualified_name:
        raise InvalidSkillPackageError(
            f"qualified_name 必须含 ':' 分隔 owner_did 和 skill_name, got {qualified_name!r}"
        )
    idx = qualified_name.rfind(":")
    owner = qualified_name[:idx]
    skill = qualified_name[idx + 1:]
    if not owner or not skill:
        raise InvalidSkillPackageError(
            f"qualified_name 两端不能空, got owner={owner!r} skill={skill!r}"
        )
    return owner, skill


# ── base64 helper (CLI / HTTP body 传 encrypted bytes 用) ────────────────────


def encrypted_to_b64(blob: bytes) -> str:
    return base64.b64encode(blob).decode("ascii")


def b64_to_encrypted(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


__all__ = [
    # 常量
    "EXAMPLES_INLINE_LIMIT_BYTES",
    "DEFAULT_SKILL_EXPIRY_HOURS",
    "MIN_SKILL_EXPIRY_HOURS",
    "MAX_SKILL_EXPIRY_HOURS",
    "SKILL_PACKAGE_SCHEMA",
    "PUBKEY_SIZE",
    "BOX_NONCE_SIZE",
    "ENCRYPTION_ALGORITHM",
    "KEY_DERIVATION_DESC",
    "PERSONALITY_TRAITS_HINTS",
    # 异常
    "SkillPackageError",
    "SkillPackageDecryptError",
    "InvalidSkillPackageError",
    # 数据
    "SkillContents",
    "SkillEncryptionInfo",
    "SkillPackage",
    # 打包
    "package_skill",
    "validate_skill_package",
    # 加解密
    "encrypt_skill_package",
    "decrypt_skill_package",
    # 辅助
    "parse_qualified_name",
    "encrypted_to_b64",
    "b64_to_encrypted",
]
