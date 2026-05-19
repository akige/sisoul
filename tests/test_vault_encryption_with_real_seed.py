"""tests for vault.encryption 接入 BIP-39 真 seed (Phase 2 W17-W20, 波 3 dev-A).

验证:
- derive_master_key(<合法 BIP-39 mnemonic>) 走 BIP-39 派生 (≠ legacy sha256)
- 同 mnemonic → 同 master_key (跨设备一致)
- 不同 mnemonic → 不同 master_key
- ~/.sisoul/seed.txt 存在时, derive_master_key(None) 走 seed 派生
- 加密 → 用 seed 派生 key 解密 OK
- 篡改 seed → 解密失败
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from nacl.exceptions import CryptoError

from sisoul.identity import (
    derive_subkey,
    generate_mnemonic,
    mnemonic_to_master_key,
    save_mnemonic_to_file,
)
from sisoul.vault.encryption import (
    KEY_SIZE,
    PLACEHOLDER_MNEMONIC,
    decrypt_bytes,
    derive_master_key,
    encrypt_bytes,
)


VAULT_PURPOSE = "vault"


# ── BIP-39 path ──────────────────────────────────────────────────────────────


def test_derive_master_key_uses_bip39_for_valid_mnemonic() -> None:
    """合法 12 词 BIP-39 → 应走 BIP-39 派生, 跟 identity.derive_subkey 一致."""
    m = generate_mnemonic()
    vk = derive_master_key(m)

    # 跟 identity 直接派生比对
    expected = derive_subkey(mnemonic_to_master_key(m), VAULT_PURPOSE, 0)
    assert vk == expected
    assert len(vk) == KEY_SIZE == 32


def test_derive_master_key_deterministic_for_real_seed() -> None:
    m = generate_mnemonic()
    k1 = derive_master_key(m)
    k2 = derive_master_key(m)
    assert k1 == k2


def test_derive_master_key_different_seeds_different_keys() -> None:
    m1 = generate_mnemonic()
    m2 = generate_mnemonic()
    assert derive_master_key(m1) != derive_master_key(m2)


def test_invalid_mnemonic_falls_back_to_legacy_sha256() -> None:
    """波 2 测试兼容: 'alice' / 'bob' 等非 BIP-39 短串 → 走 legacy sha256, 不抛错."""
    k_alice = derive_master_key("alice")
    k_bob = derive_master_key("bob")
    assert len(k_alice) == 32
    assert k_alice != k_bob


# ── seed file (SISOUL_SEED_FILE env) ─────────────────────────────────────────


def test_derive_master_key_loads_from_seed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """seed 文件存在 + derive_master_key(None) → 走 seed 派生."""
    m = generate_mnemonic()
    seed_file = tmp_path / "seed.txt"
    save_mnemonic_to_file(m, seed_file)

    monkeypatch.setenv("SISOUL_SEED_FILE", str(seed_file))

    key_from_file = derive_master_key(None)
    # 应等于直接传 mnemonic 派生的结果
    key_from_mnemonic = derive_master_key(m)
    assert key_from_file == key_from_mnemonic


def test_derive_master_key_no_seed_file_falls_back_to_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """seed 文件不存在 → fallback (placeholder + sha256-via-bip39)."""
    monkeypatch.setenv("SISOUL_SEED_FILE", str(tmp_path / "nonexistent.txt"))
    k = derive_master_key(None)
    # PLACEHOLDER 也是合法 BIP-39 ("abandon ... about"), 走 BIP-39 派生
    expected = derive_master_key(PLACEHOLDER_MNEMONIC)
    assert k == expected


def test_seed_file_with_loose_permissions_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """seed 文件权限松 → load 失败 → fallback (不崩)."""
    m = generate_mnemonic()
    seed_file = tmp_path / "seed.txt"
    save_mnemonic_to_file(m, seed_file)
    os.chmod(seed_file, 0o644)

    monkeypatch.setenv("SISOUL_SEED_FILE", str(seed_file))
    # 不应抛 PermissionError, 应 fallback 到 placeholder
    k = derive_master_key(None)
    assert len(k) == 32
    # 应等于 placeholder 派生 (而非真 seed)
    expected_placeholder = derive_master_key(PLACEHOLDER_MNEMONIC)
    assert k == expected_placeholder


# ── encrypt/decrypt roundtrip with BIP-39 derived key ────────────────────────


def test_encrypt_decrypt_with_bip39_derived_key() -> None:
    m = generate_mnemonic()
    key = derive_master_key(m)
    plain = b"sensitive vault payload \x00\xff"
    blob = encrypt_bytes(plain, key)
    assert decrypt_bytes(blob, key) == plain


def test_decrypt_fails_with_different_seed() -> None:
    m1 = generate_mnemonic()
    m2 = generate_mnemonic()
    k1 = derive_master_key(m1)
    k2 = derive_master_key(m2)
    blob = encrypt_bytes(b"secret", k1)
    with pytest.raises(CryptoError):
        decrypt_bytes(blob, k2)


def test_cross_device_simulation_same_seed_roundtrip(tmp_path: Path) -> None:
    """模拟设备 A 加密 → 设备 B 用同 seed 解密."""
    m = generate_mnemonic()

    # 设备 A: 写 seed file + 加密
    seed_a = tmp_path / "device-a" / "seed.txt"
    save_mnemonic_to_file(m, seed_a)
    key_a = derive_subkey(mnemonic_to_master_key(m), VAULT_PURPOSE, 0)
    blob = encrypt_bytes(b"cross-device payload", key_a)

    # 设备 B: 同 mnemonic 派生 → 解密
    key_b = derive_subkey(mnemonic_to_master_key(m), VAULT_PURPOSE, 0)
    assert key_a == key_b
    assert decrypt_bytes(blob, key_b) == b"cross-device payload"


def test_legacy_placeholder_still_usable_for_dev() -> None:
    """波 2 dev / test 走 fallback 应仍能加解密 (不破)."""
    key = derive_master_key(None)
    blob = encrypt_bytes(b"dev mode", key)
    assert decrypt_bytes(blob, key) == b"dev mode"
