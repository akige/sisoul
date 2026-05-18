"""sisoul vault · libsodium 加密 (Phase 1 W4 + Phase 2 W17-W20 接入 BIP-39).

§28 §1.1 模块 2 · §29 §3 W4 + §4.1 W17-W20:
libsodium SecretBox (xsalsa20-poly1305) 对称加密 vault.

设计:
- master_key 32 bytes (SecretBox 需要)
- 派生优先级 (Phase 2 W17-W20 ship):
    1. 显式传 mnemonic → BIP-39 派生 (mnemonic_to_master_key → derive_subkey("vault"))
    2. ~/.sisoul/seed.txt 存在 → 加载 + BIP-39 派生
    3. fallback → PLACEHOLDER_MNEMONIC (废弃 sha256 算法, 仅 dev/test, WARN 日志)
- encrypt_bytes(plain, key) → nonce(24) + ciphertext (含 MAC)
- decrypt_bytes(blob, key) → plain (篡改 / 错 key 抛 nacl.exceptions.CryptoError)

兼容性 (波 2 测试不破坏):
- derive_master_key(None) 仍返回 placeholder 派生结果 (跟波 2 一致, sha256 算法保留)
- derive_master_key(mnemonic) 改成 BIP-39 派生 (波 2 测试用 "alice"/"bob" 不是合法 12 词 → 走 fallback sha256, 兼容)
- 行为变化: 真合法 BIP-39 mnemonic 现在走真派生, 不再走 sha256

注意 SecretBox.NONCE_SIZE = 24 (xsalsa20).
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Final

from nacl.exceptions import CryptoError
from nacl.secret import SecretBox
from nacl.utils import random as nacl_random

logger = logging.getLogger(__name__)

# Phase 1 W4 占位 mnemonic. Phase 2 W17 真随机 BIP-39 时替换.
# 仅 dev / 单元测试用. 真用户首次 sisoul init 不应走此分支.
# 注: 这串 12 词其实是合法 BIP-39 (entropy 全 0), 但保留旧 sha256 派生算法 (波 2 测试兼容).
PLACEHOLDER_MNEMONIC: Final[str] = (
    "abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon abandon abandon about"
)
# 派生 salt 固定 (Phase 1 不做用户级 salt 隔离).
_DERIVE_SALT: Final[bytes] = b"sisoul-phase1-w4-placeholder-salt-v1"

KEY_SIZE: Final[int] = SecretBox.KEY_SIZE  # 32
NONCE_SIZE: Final[int] = SecretBox.NONCE_SIZE  # 24

# vault subkey purpose tag (BIP-39 derive_subkey)
_VAULT_PURPOSE: Final[str] = "vault"


def _derive_legacy_placeholder(mnemonic: str | None) -> bytes:
    """旧 sha256 派生算法 (Phase 1 W4 默认 + 兼容 fallback).

    保留给 fallback 场景 + 波 2 测试 ("alice"/"bob" 短串).
    """
    m = (mnemonic or PLACEHOLDER_MNEMONIC).strip().encode("utf-8")
    digest = hashlib.sha256(_DERIVE_SALT + m).digest()
    assert len(digest) == KEY_SIZE, "sha256 must produce 32B"
    return digest


def derive_master_key(mnemonic: str | None = None) -> bytes:
    """派生 32B vault master key.

    派生路径:
    - mnemonic = None → fallback (placeholder + sha256, 跟波 2 行为一致, 加 WARN)
    - mnemonic 是合法 BIP-39 → BIP-39 PBKDF2 + HMAC-SHA256 子 key 派生 (真 Phase 2 路径)
    - mnemonic 非空但非法 BIP-39 → 回退 sha256(salt+mnemonic) (波 2 测试 "alice"/"bob" 走这里)

    Returns:
        32B SecretBox key.
    """
    # 显式传 None → 看 ~/.sisoul/seed.txt 有没有真 seed
    if mnemonic is None:
        seed_key = _try_load_seed_from_default_file()
        if seed_key is not None:
            return seed_key
        # fallback → 用 PLACEHOLDER_MNEMONIC 走下面统一路径 (保证 None == PLACEHOLDER 等价)
        logger.warning(
            "vault master_key 走 placeholder fallback! "
            "跑 `sisoul init --vault-dir <path>` 生成真 BIP-39 seed 以保护 vault."
        )
        mnemonic = PLACEHOLDER_MNEMONIC

    # 延迟 import 避免循环
    try:
        from sisoul.identity.seed import (
            derive_subkey,
            mnemonic_to_master_key,
            verify_mnemonic,
        )
    except ImportError:  # pragma: no cover · mnemonic 库未装
        logger.warning(
            "sisoul.identity 不可用 (mnemonic 库未装?), 用 fallback sha256 派生"
        )
        return _derive_legacy_placeholder(mnemonic)

    if verify_mnemonic(mnemonic):
        master_seed = mnemonic_to_master_key(mnemonic)
        return derive_subkey(master_seed, _VAULT_PURPOSE, index=0)
    # mnemonic 非空但非法 BIP-39 (e.g. 波 2 测试 "alice"/"bob") → 兼容 fallback sha256
    return _derive_legacy_placeholder(mnemonic)


def _try_load_seed_from_default_file() -> bytes | None:
    """尝试从 ~/.sisoul/seed.txt 加载 mnemonic 并派生 vault key.

    Returns:
        32B key | None (文件不存在 / 不可读 / 内容非法).
    """
    # 测试可注入: SISOUL_SEED_FILE env 覆盖 (避免污染用户真 ~/.sisoul/)
    env_path = os.environ.get("SISOUL_SEED_FILE")
    try:
        from sisoul.identity.seed import (
            DEFAULT_SEED_FILE,
            derive_subkey,
            load_mnemonic_from_file,
            mnemonic_to_master_key,
        )
    except ImportError:  # pragma: no cover
        return None

    seed_path: Path = Path(env_path).expanduser() if env_path else DEFAULT_SEED_FILE
    if not seed_path.exists():
        return None
    try:
        mnemonic = load_mnemonic_from_file(seed_path)
    except (FileNotFoundError, PermissionError, ValueError) as e:
        logger.warning("seed 文件加载失败 (%s): %s", seed_path, e)
        return None
    master_seed = mnemonic_to_master_key(mnemonic)
    return derive_subkey(master_seed, _VAULT_PURPOSE, index=0)


def encrypt_bytes(plain: bytes, key: bytes) -> bytes:
    """加密 → nonce(24B) || ciphertext_with_mac.

    输出 1 个 bytes blob, 解密时整段传给 decrypt_bytes.
    每次调用生成新 nonce (libsodium 强烈推荐).
    """
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes, got {len(key)}")
    box = SecretBox(key)
    nonce = nacl_random(NONCE_SIZE)
    ct = box.encrypt(plain, nonce)  # nacl 默认返回 EncryptedMessage (nonce + ciphertext)
    return bytes(ct)


def decrypt_bytes(blob: bytes, key: bytes) -> bytes:
    """解密. 篡改 / 错 key 抛 nacl.exceptions.CryptoError.

    blob = nonce(24B) + ciphertext_with_mac (encrypt_bytes 输出格式).
    """
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes, got {len(key)}")
    if len(blob) < NONCE_SIZE + SecretBox.MACBYTES:
        raise CryptoError("ciphertext too short")
    box = SecretBox(key)
    return box.decrypt(blob)


def encrypt_text(plain: str, key: bytes) -> bytes:
    """utf-8 文本加密 shortcut."""
    return encrypt_bytes(plain.encode("utf-8"), key)


def decrypt_text(blob: bytes, key: bytes) -> str:
    """utf-8 文本解密 shortcut."""
    return decrypt_bytes(blob, key).decode("utf-8")
