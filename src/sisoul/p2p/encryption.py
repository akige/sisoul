"""sisoul p2p · 加密层 (Phase 3 W31-W36 · 波 4 dev-A).

§28 §1.1 模块 9 (跨设备 P2P sync) · §29 §5.1 W31-W36.

设计:
- 用 libsodium SecretStream (NaCl xchacha20poly1305) 加密 vault 传输.
- key 由 BIP-39 master seed 经 ``sisoul.identity.seed.derive_subkey(master, "p2p")`` 派生
  → 双方拥有同一 BIP-39 seed (跨设备灵魂迁移) 才能解密, 中间人无 seed 无法解.
- 同一 seed → 同一 32B p2p key → 同一 SecretBox 通道.
  跨设备 (Mac/Linux/iPad) 都派同一 key, 真去中心化 (无需在线服务器交换 key).

为啥不用 noise protocol / handshake:
- noise 适合两端独立 keypair 协商. 本场景双端**同源** (同 BIP-39 seed), 直接派对称 key 更简.
- Phase 4 朋友共享才需要 keypair 加密 (Alice ↔ Bob 不同 seed), 那时再上 libsodium box / noise.

API:
- ``derive_p2p_key(master_seed) -> 32B key`` 复用 identity.seed.derive_subkey 派生.
- ``encrypt(key, plaintext) -> bytes`` 输出 = nonce(24B) || ciphertext
- ``decrypt(key, blob) -> bytes`` 校验 MAC 失败抛 ``DecryptionError``.
- ``encrypt_stream / decrypt_stream`` (chunked, vault 文件可能 > MB).
"""

from __future__ import annotations

from typing import Final

from nacl.exceptions import CryptoError
from nacl.secret import SecretBox
from nacl.utils import random as nacl_random

from sisoul.identity import derive_subkey

# P2P 子 key purpose (跟 vault "vault" / did "did" 隔离)
_P2P_PURPOSE: Final[str] = "p2p"

# SecretBox key/nonce 长度 (libsodium 标准)
KEY_SIZE: Final[int] = SecretBox.KEY_SIZE  # 32
NONCE_SIZE: Final[int] = SecretBox.NONCE_SIZE  # 24


class P2PEncryptionError(Exception):
    """P2P 加解密 root error."""


class DecryptionError(P2PEncryptionError):
    """密文校验失败 (MAC 错 / nonce 错 / key 错). 不可恢复."""


def derive_p2p_key(master_seed: bytes, index: int = 0) -> bytes:
    """从 BIP-39 master seed 派生 32B P2P 通道 key.

    Args:
        master_seed: 64B BIP-39 master seed (identity.seed.mnemonic_to_master_key 返回值).
        index: 同 seed 下多个 P2P 通道 (默认 0; 朋友共享时可分通道).

    Returns:
        32B SecretBox key. 同 seed + index ⇒ 同 key, 跨设备一致.
    """
    if not isinstance(master_seed, (bytes, bytearray)) or len(master_seed) == 0:
        raise ValueError("master_seed 必须非空 bytes")
    return derive_subkey(master_seed, _P2P_PURPOSE, index=index)


def encrypt(key: bytes, plaintext: bytes) -> bytes:
    """对称加密. 输出 = nonce(24B) || ciphertext_with_mac.

    Args:
        key: 32B SecretBox key (derive_p2p_key 返回值).
        plaintext: 明文 bytes.

    Returns:
        bytes (nonce 前 24B + 密文; 后端可用 ``decrypt`` 解).

    Raises:
        ValueError: key 长度不对.
    """
    if not isinstance(key, (bytes, bytearray)) or len(key) != KEY_SIZE:
        raise ValueError(f"key 必须 {KEY_SIZE}B, 实际 {len(key) if isinstance(key, (bytes, bytearray)) else type(key)}")
    if not isinstance(plaintext, (bytes, bytearray)):
        raise ValueError("plaintext 必须 bytes")

    box = SecretBox(bytes(key))
    nonce = nacl_random(NONCE_SIZE)
    ciphertext = box.encrypt(bytes(plaintext), nonce)
    # nacl SecretBox.encrypt 返回 EncryptedMessage 已含 nonce 前缀, 但我们显式包装确保格式
    # 直接返回 ciphertext (含 nonce 前缀 + MAC)
    assert ciphertext[:NONCE_SIZE] == nonce
    return bytes(ciphertext)


def decrypt(key: bytes, blob: bytes) -> bytes:
    """解密对应 ``encrypt`` 输出.

    Args:
        key: 32B key.
        blob: encrypt 返回值 (nonce + ciphertext + MAC).

    Returns:
        明文 bytes.

    Raises:
        ValueError: key 长度不对 / blob 太短.
        DecryptionError: MAC 校验失败 (篡改 / key 错 / 损坏).
    """
    if not isinstance(key, (bytes, bytearray)) or len(key) != KEY_SIZE:
        raise ValueError(f"key 必须 {KEY_SIZE}B")
    if not isinstance(blob, (bytes, bytearray)) or len(blob) < NONCE_SIZE + 16:
        # 至少 nonce(24) + MAC(16) + 0 明文
        raise ValueError(f"blob 太短 ({len(blob) if hasattr(blob, '__len__') else '?'}B), 至少 40B")

    box = SecretBox(bytes(key))
    try:
        return box.decrypt(bytes(blob))
    except CryptoError as e:
        raise DecryptionError(f"P2P 解密失败 (MAC 错 / key 不匹配 / 损坏): {e}") from e


# ── stream API (chunked, vault 文件可能大) ─────────────────────────────────────


CHUNK_SIZE: Final[int] = 64 * 1024  # 64 KiB chunk


def encrypt_stream(key: bytes, plaintext: bytes, chunk_size: int = CHUNK_SIZE) -> list[bytes]:
    """分块加密 (每块独立 nonce). 返回 list[encrypted_chunk].

    设计:
    - 每块独立加密, 接收方按序解密拼接.
    - 简化版 SecretStream (libsodium SecretStream API 更安全但 pynacl 没暴露稳定接口).
    - Phase 3 W31-W36 够用; Phase 4 朋友共享 (大文件 / 边传边解) 升级 SecretStream.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须 > 0")
    if not isinstance(plaintext, (bytes, bytearray)):
        raise ValueError("plaintext 必须 bytes")

    out: list[bytes] = []
    data = bytes(plaintext)
    for offset in range(0, len(data), chunk_size):
        chunk = data[offset : offset + chunk_size]
        out.append(encrypt(key, chunk))
    # 空 plaintext 也要 emit 一个 sentinel 块 (空块), 否则 decrypt_stream 不知道是不是空文件
    if not out:
        out.append(encrypt(key, b""))
    return out


def decrypt_stream(key: bytes, chunks: list[bytes]) -> bytes:
    """对应 ``encrypt_stream`` 反向拼接.

    Raises:
        DecryptionError: 任一块校验失败.
    """
    parts: list[bytes] = []
    for i, c in enumerate(chunks):
        try:
            parts.append(decrypt(key, c))
        except DecryptionError as e:
            raise DecryptionError(f"chunk {i} 解密失败: {e}") from e
    return b"".join(parts)


__all__ = [
    "CHUNK_SIZE",
    "DecryptionError",
    "KEY_SIZE",
    "NONCE_SIZE",
    "P2PEncryptionError",
    "decrypt",
    "decrypt_stream",
    "derive_p2p_key",
    "encrypt",
    "encrypt_stream",
]
