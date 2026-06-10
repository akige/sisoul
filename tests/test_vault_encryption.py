"""tests for sisoul.vault.encryption (Phase 1 W4)."""

from __future__ import annotations

import pytest
from nacl.exceptions import CryptoError

from sisoul.vault.encryption import (
    KEY_SIZE,
    NONCE_SIZE,
    PLACEHOLDER_MNEMONIC,
    decrypt_bytes,
    decrypt_text,
    derive_master_key,
    encrypt_bytes,
    encrypt_text,
)


def test_derive_master_key_default_size() -> None:
    key = derive_master_key()
    assert len(key) == KEY_SIZE == 32


def test_derive_master_key_deterministic() -> None:
    """同 mnemonic 两次派生应一致."""
    k1 = derive_master_key("foo bar baz")
    k2 = derive_master_key("foo bar baz")
    assert k1 == k2


def test_derive_master_key_different_mnemonic_different_key() -> None:
    k1 = derive_master_key("alice")
    k2 = derive_master_key("bob")
    assert k1 != k2


def test_derive_master_key_placeholder_default(monkeypatch) -> None:
    """传 None 且无真 seed 时应 = 传 placeholder.

    密闭: None 路径会优先读真 vault seed (开发机 ~/.sisoul/seed.txt 存在时
    返回真 key ≠ placeholder, 是正确生产行为). 用 SISOUL_SEED_FILE 指到
    不存在的路径, 强制走 placeholder fallback 分支.
    """
    monkeypatch.setenv("SISOUL_SEED_FILE", "/nonexistent/sisoul-test-seed.txt")
    assert derive_master_key(None) == derive_master_key(PLACEHOLDER_MNEMONIC)


def test_encrypt_decrypt_roundtrip_bytes() -> None:
    key = derive_master_key()
    plain = b"secret payload \x00\xff binary"
    blob = encrypt_bytes(plain, key)
    assert blob != plain  # 真加密了
    assert decrypt_bytes(blob, key) == plain


def test_encrypt_decrypt_text_utf8() -> None:
    key = derive_master_key()
    plain = "我用 Tailwind CSS, 不喜欢 modal."
    blob = encrypt_text(plain, key)
    assert decrypt_text(blob, key) == plain


def test_encrypt_each_call_nonce_random() -> None:
    """同 plain 两次加密应不同 (nonce 随机)."""
    key = derive_master_key()
    b1 = encrypt_bytes(b"same", key)
    b2 = encrypt_bytes(b"same", key)
    assert b1 != b2
    assert decrypt_bytes(b1, key) == decrypt_bytes(b2, key) == b"same"


def test_decrypt_wrong_key_raises() -> None:
    k1 = derive_master_key("alice")
    k2 = derive_master_key("bob")
    blob = encrypt_bytes(b"secret", k1)
    with pytest.raises(CryptoError):
        decrypt_bytes(blob, k2)


def test_decrypt_tampered_blob_raises() -> None:
    key = derive_master_key()
    blob = bytearray(encrypt_bytes(b"hello", key))
    # 翻转最后 1 byte (在 MAC 范围内)
    blob[-1] ^= 0xFF
    with pytest.raises(CryptoError):
        decrypt_bytes(bytes(blob), key)


def test_encrypt_invalid_key_size_raises() -> None:
    with pytest.raises(ValueError):
        encrypt_bytes(b"x", b"too-short")


def test_decrypt_too_short_blob_raises() -> None:
    key = derive_master_key()
    with pytest.raises(CryptoError):
        decrypt_bytes(b"x", key)


def test_nonce_size_constant() -> None:
    assert NONCE_SIZE == 24
