"""BIP-39 seed · master key 派生 · 子 key 派生 (Phase 2 W17-W20 · 波 3 dev-A).

§28 §1.1 模块 8 (灵魂迁移核心) · §29 §4.1 W17-W20.

实现:
1. generate_mnemonic(strength=128) → 12 词 BIP-39 mnemonic (英文 wordlist)
   strength 128 bit → 12 词. 160→15, 192→18, 224→21, 256→24.
2. verify_mnemonic(mnemonic) → BIP-39 checksum 校验.
3. mnemonic_to_master_key(mnemonic, passphrase="") →
   64B master seed (BIP-39 标准 PBKDF2-HMAC-SHA512, 2048 iter, salt = "mnemonic"+passphrase).
4. derive_subkey(master, purpose, index=0) →
   32B 子 key (HMAC-SHA256(master, purpose_bytes || index_4bytes)).
   purpose ∈ {"vault","did","skill",...} · index 同 purpose 下多个子 key 用 (skill 资产编号等).
5. save/load 文件 + chmod 600 强制.

兼容性: mnemonic_to_master_key 用 mnemonic 库的 to_seed (标准 BIP-39),
跨钱包 (Metamask / Trezor / Ledger) 同 mnemonic 派生同 master seed.
"""

from __future__ import annotations

import hmac
import os
import stat
import struct
from hashlib import sha256
from pathlib import Path
from typing import Final

from mnemonic import Mnemonic

# 默认 seed 文件位置 (Phase 2 W17 单机 fallback, 真用户应保存离线 seed)
DEFAULT_SEED_FILE: Final[Path] = Path.home() / ".sisoul" / "seed.txt"

# 子 key 长度 (32B = libsodium SecretBox.KEY_SIZE; ed25519 priv 也是 32B → DID 复用)
SUBKEY_SIZE: Final[int] = 32

# 支持的语言 (Phase 2 只支持英文; Phase 3+ 可加中文 / 日文)
_LANGUAGE: Final[str] = "english"

# strength → 词数 映射 (BIP-39 标准)
_STRENGTH_TO_WORDS: Final[dict[int, int]] = {
    128: 12,
    160: 15,
    192: 18,
    224: 21,
    256: 24,
}


class InvalidMnemonicError(ValueError):
    """mnemonic checksum / 词表校验失败."""


def _mnemo() -> Mnemonic:
    """复用 Mnemonic 实例 (内部加载 wordlist, 多次调用复用)."""
    return Mnemonic(_LANGUAGE)


def generate_mnemonic(strength: int = 128) -> str:
    """生成 BIP-39 mnemonic.

    Args:
        strength: 熵 bit 数. 128 (12 词, 默认) / 160 / 192 / 224 / 256 (24 词).

    Returns:
        空格分隔的英文 mnemonic 字符串.

    Raises:
        ValueError: strength 不在支持范围.
    """
    if strength not in _STRENGTH_TO_WORDS:
        raise ValueError(
            f"strength 必须 ∈ {sorted(_STRENGTH_TO_WORDS.keys())}, 实际 {strength}"
        )
    m = _mnemo()
    mnemonic = m.generate(strength=strength)
    # 自检: 生成的 mnemonic 应自洽 (checksum 正确)
    assert m.check(mnemonic), "BIP-39 库生成的 mnemonic 应自洽 (库 bug?)"
    expected_words = _STRENGTH_TO_WORDS[strength]
    actual_words = len(mnemonic.split())
    assert actual_words == expected_words, (
        f"strength {strength} 应 {expected_words} 词, 实际 {actual_words}"
    )
    return mnemonic


def verify_mnemonic(mnemonic: str) -> bool:
    """BIP-39 checksum + 词表校验.

    Returns:
        True = 合法 BIP-39 mnemonic; False = checksum 错 / 词表外 / 词数不符.
    """
    if not isinstance(mnemonic, str) or not mnemonic.strip():
        return False
    return _mnemo().check(mnemonic.strip())


def mnemonic_to_master_key(mnemonic: str, passphrase: str = "") -> bytes:
    """BIP-39 PBKDF2-HMAC-SHA512 派生 64B master seed.

    Args:
        mnemonic: 12-24 词 BIP-39 mnemonic.
        passphrase: 可选 BIP-39 passphrase (第 25 词, 默认空). 改 passphrase = 不同 seed.

    Returns:
        64B master seed (跨钱包标准, 同 mnemonic+passphrase 全宇宙一致).

    Raises:
        InvalidMnemonicError: mnemonic 不合法.
    """
    mnemonic = mnemonic.strip() if isinstance(mnemonic, str) else ""
    if not verify_mnemonic(mnemonic):
        raise InvalidMnemonicError(f"mnemonic 不合法 (checksum / 词表错): {mnemonic[:32]}...")
    seed = _mnemo().to_seed(mnemonic, passphrase=passphrase)
    assert len(seed) == 64, f"BIP-39 seed 应 64B, 实际 {len(seed)}"
    return seed


def derive_subkey(master_key: bytes, purpose: str, index: int = 0) -> bytes:
    """从 master seed 派生 32B 子 key.

    算法 (BIP-32 inspired 简化, 不做 chain code):
        subkey = HMAC-SHA256(key=master_key, msg=purpose.encode("utf-8") || index_u32_be)

    Args:
        master_key: 64B BIP-39 master seed (mnemonic_to_master_key 返回值).
        purpose: 业务隔离 tag, 例 "vault" / "did" / "skill" / "p2p".
        index: 同 purpose 下多个子 key (默认 0).

    Returns:
        32B 子 key. 同 master+purpose+index ⇒ 同子 key (决定性, 跨设备一致).

    Raises:
        ValueError: master_key 不是 bytes / purpose 空 / index 负.
    """
    if not isinstance(master_key, (bytes, bytearray)):
        raise ValueError(f"master_key 必须 bytes, 实际 {type(master_key).__name__}")
    if len(master_key) == 0:
        raise ValueError("master_key 不能为空")
    if not isinstance(purpose, str) or not purpose:
        raise ValueError("purpose 必须非空 str")
    if not isinstance(index, int) or index < 0:
        raise ValueError(f"index 必须 >= 0 int, 实际 {index}")

    msg = purpose.encode("utf-8") + struct.pack(">I", index)
    digest = hmac.new(bytes(master_key), msg, sha256).digest()
    assert len(digest) == SUBKEY_SIZE, f"HMAC-SHA256 应 32B, 实际 {len(digest)}"
    return digest


def save_mnemonic_to_file(mnemonic: str, path: Path | None = None) -> Path:
    """保存 mnemonic 到本地文件 + chmod 600.

    Args:
        mnemonic: 合法 BIP-39 mnemonic.
        path: 文件路径, None = DEFAULT_SEED_FILE.

    Returns:
        实际写入路径 (绝对).

    Raises:
        InvalidMnemonicError: mnemonic 不合法 (不允许保存非法 seed).
        FileExistsError: 文件已存在 (不覆盖, 避免误删用户 seed). 调用方应先 unlink.
    """
    mnemonic = mnemonic.strip() if isinstance(mnemonic, str) else ""
    if not verify_mnemonic(mnemonic):
        raise InvalidMnemonicError("拒绝保存非法 mnemonic")

    target = Path(path) if path is not None else DEFAULT_SEED_FILE
    target = target.expanduser()
    if target.exists():
        raise FileExistsError(
            f"seed 文件已存在: {target} (拒绝覆盖, 避免误删原 seed; "
            f"如确认替换请先 unlink)"
        )
    target.parent.mkdir(parents=True, exist_ok=True)

    # 用 os.open + O_CREAT|O_EXCL|O_WRONLY mode=0o600 一步原子建文件
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, (mnemonic + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    # 再次保险 chmod (某些 umask 可能掩到 644)
    os.chmod(target, 0o600)
    return target.resolve()


def load_mnemonic_from_file(path: Path | None = None) -> str:
    """加载 mnemonic 从文件.

    Args:
        path: 文件路径, None = DEFAULT_SEED_FILE.

    Returns:
        strip 后的 mnemonic 字符串.

    Raises:
        FileNotFoundError: 文件不存在.
        InvalidMnemonicError: 文件内容不是合法 BIP-39 mnemonic.
        PermissionError: 文件权限松 (> 600), 拒绝加载 (防 seed 被偷).
    """
    target = Path(path) if path is not None else DEFAULT_SEED_FILE
    target = target.expanduser()
    if not target.exists():
        raise FileNotFoundError(f"seed 文件不存在: {target}")

    # 权限校验: 文件 mode 应 ≤ 0o600 (只 owner 读写)
    file_mode = stat.S_IMODE(target.stat().st_mode)
    # 允许 600 / 400; 拒绝 group/other 任何位
    if file_mode & 0o077:
        raise PermissionError(
            f"seed 文件权限过松 ({oct(file_mode)}), 应 ≤ 0600. "
            f"运行 chmod 600 {target} 修复"
        )

    text = target.read_text(encoding="utf-8").strip()
    if not verify_mnemonic(text):
        raise InvalidMnemonicError(f"seed 文件内容不是合法 BIP-39 mnemonic: {target}")
    return text
