"""测试 p2p.encryption — BIP-39 派 P2P key + SecretBox 加解密 (波 4 dev-A)."""

from __future__ import annotations

import os

import pytest

from sisoul.identity import generate_mnemonic, mnemonic_to_master_key
from sisoul.p2p.encryption import (
    CHUNK_SIZE,
    KEY_SIZE,
    NONCE_SIZE,
    DecryptionError,
    P2PEncryptionError,
    decrypt,
    decrypt_stream,
    derive_p2p_key,
    encrypt,
    encrypt_stream,
)


# ── derive_p2p_key ────────────────────────────────────────────────────────────


class TestDeriveP2PKey:
    def test_basic_returns_32_bytes(self):
        master = b"\x00" * 64
        key = derive_p2p_key(master)
        assert isinstance(key, bytes)
        assert len(key) == KEY_SIZE == 32

    def test_deterministic_same_seed(self):
        """同 master seed → 同 P2P key (跨设备一致基石)."""
        master = b"\x42" * 64
        k1 = derive_p2p_key(master)
        k2 = derive_p2p_key(master)
        assert k1 == k2

    def test_different_seeds_different_keys(self):
        master_a = b"\x01" * 64
        master_b = b"\x02" * 64
        assert derive_p2p_key(master_a) != derive_p2p_key(master_b)

    def test_index_isolation(self):
        """同 seed 不同 index → 不同 key (多通道)."""
        master = b"\xaa" * 64
        assert derive_p2p_key(master, index=0) != derive_p2p_key(master, index=1)

    def test_bip39_real_seed_roundtrip(self):
        """真 BIP-39 mnemonic 派 master → P2P key, 跨设备模拟."""
        mnemonic = generate_mnemonic(strength=128)
        master = mnemonic_to_master_key(mnemonic)
        # 设备 A 派 key
        key_a = derive_p2p_key(master)
        # 设备 B 同 mnemonic → 同 master → 同 key
        master_b = mnemonic_to_master_key(mnemonic)
        key_b = derive_p2p_key(master_b)
        assert key_a == key_b
        # 加解密 roundtrip
        ct = encrypt(key_a, b"cross-device payload")
        pt = decrypt(key_b, ct)
        assert pt == b"cross-device payload"

    def test_different_mnemonic_cannot_decrypt(self):
        """不同 mnemonic 派出的 P2P key 不能互解 (隐私保证)."""
        m1 = generate_mnemonic(strength=128)
        m2 = generate_mnemonic(strength=128)
        # 极小概率重复, retry
        while m1 == m2:
            m2 = generate_mnemonic(strength=128)
        key1 = derive_p2p_key(mnemonic_to_master_key(m1))
        key2 = derive_p2p_key(mnemonic_to_master_key(m2))
        assert key1 != key2
        ct = encrypt(key1, b"secret")
        with pytest.raises(DecryptionError):
            decrypt(key2, ct)

    def test_empty_master_rejected(self):
        with pytest.raises(ValueError):
            derive_p2p_key(b"")

    def test_non_bytes_master_rejected(self):
        with pytest.raises(ValueError):
            derive_p2p_key("not-bytes")  # type: ignore[arg-type]


# ── encrypt / decrypt ────────────────────────────────────────────────────────


class TestEncryptDecrypt:
    KEY = b"\x77" * 32

    def test_basic_roundtrip(self):
        ct = encrypt(self.KEY, b"hello world")
        assert decrypt(self.KEY, ct) == b"hello world"

    def test_nonce_random_each_call(self):
        """同 plaintext 加两次 → 不同 ciphertext (nonce 随机)."""
        ct1 = encrypt(self.KEY, b"x")
        ct2 = encrypt(self.KEY, b"x")
        assert ct1 != ct2

    def test_empty_plaintext(self):
        ct = encrypt(self.KEY, b"")
        assert decrypt(self.KEY, ct) == b""

    def test_large_plaintext_1mb(self):
        data = os.urandom(1024 * 1024)
        ct = encrypt(self.KEY, data)
        assert decrypt(self.KEY, ct) == data

    def test_wrong_key_raises(self):
        ct = encrypt(self.KEY, b"x")
        wrong_key = b"\x00" * 32
        with pytest.raises(DecryptionError):
            decrypt(wrong_key, ct)

    def test_tampered_ciphertext_raises(self):
        ct = encrypt(self.KEY, b"important")
        # 篡改第 30 byte (在 MAC 范围内)
        tampered = bytearray(ct)
        tampered[NONCE_SIZE + 1] ^= 0xFF
        with pytest.raises(DecryptionError):
            decrypt(self.KEY, bytes(tampered))

    def test_invalid_key_length(self):
        with pytest.raises(ValueError):
            encrypt(b"\x01" * 31, b"x")
        with pytest.raises(ValueError):
            decrypt(b"\x01" * 31, b"x" * 50)

    def test_blob_too_short_raises(self):
        with pytest.raises(ValueError):
            decrypt(self.KEY, b"\x00" * 10)

    def test_p2p_encryption_error_hierarchy(self):
        """DecryptionError 是 P2PEncryptionError 子类."""
        assert issubclass(DecryptionError, P2PEncryptionError)


# ── stream API ────────────────────────────────────────────────────────────────


class TestStream:
    KEY = b"\x33" * 32

    def test_basic_stream_roundtrip(self):
        data = b"abcdef" * 100  # 600 B
        chunks = encrypt_stream(self.KEY, data, chunk_size=200)
        assert len(chunks) == 3
        out = decrypt_stream(self.KEY, chunks)
        assert out == data

    def test_empty_stream_has_sentinel(self):
        chunks = encrypt_stream(self.KEY, b"")
        assert len(chunks) == 1
        assert decrypt_stream(self.KEY, chunks) == b""

    def test_default_chunk_size_64k(self):
        data = os.urandom(200 * 1024)  # 200 KiB
        chunks = encrypt_stream(self.KEY, data)
        assert len(chunks) == 4  # 200/64 ≈ 3.125 → 4 块
        assert decrypt_stream(self.KEY, chunks) == data
        assert CHUNK_SIZE == 64 * 1024

    def test_tampered_chunk_raises(self):
        data = b"abcdef" * 100
        chunks = encrypt_stream(self.KEY, data, chunk_size=200)
        # 篡改第 2 块
        bad = bytearray(chunks[1])
        bad[NONCE_SIZE + 5] ^= 0xFF
        chunks[1] = bytes(bad)
        with pytest.raises(DecryptionError) as exc:
            decrypt_stream(self.KEY, chunks)
        assert "chunk 1" in str(exc.value)

    def test_chunk_size_invalid(self):
        with pytest.raises(ValueError):
            encrypt_stream(self.KEY, b"x", chunk_size=0)
