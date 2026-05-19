"""tests for sisoul.identity.seed (Phase 2 W17-W20, 波 3 dev-A)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from sisoul.identity.seed import (
    DEFAULT_SEED_FILE,
    SUBKEY_SIZE,
    InvalidMnemonicError,
    derive_subkey,
    generate_mnemonic,
    load_mnemonic_from_file,
    mnemonic_to_master_key,
    save_mnemonic_to_file,
    verify_mnemonic,
)


# ── generate_mnemonic ────────────────────────────────────────────────────────


def test_generate_mnemonic_default_12_words() -> None:
    m = generate_mnemonic()
    words = m.split()
    assert len(words) == 12
    assert verify_mnemonic(m)


def test_generate_mnemonic_24_words() -> None:
    m = generate_mnemonic(strength=256)
    assert len(m.split()) == 24
    assert verify_mnemonic(m)


def test_generate_mnemonic_invalid_strength_raises() -> None:
    with pytest.raises(ValueError, match="strength"):
        generate_mnemonic(strength=100)


def test_generate_mnemonic_random_each_call() -> None:
    """两次生成应不同 (随机熵)."""
    m1 = generate_mnemonic()
    m2 = generate_mnemonic()
    assert m1 != m2


def test_generate_mnemonic_all_strengths_valid() -> None:
    for strength in (128, 160, 192, 224, 256):
        m = generate_mnemonic(strength=strength)
        assert verify_mnemonic(m), f"strength {strength} produced invalid mnemonic"


# ── verify_mnemonic ──────────────────────────────────────────────────────────


def test_verify_mnemonic_known_valid() -> None:
    # BIP-39 标准测试向量 (entropy 全 0 → "abandon ... about")
    valid = (
        "abandon abandon abandon abandon abandon abandon "
        "abandon abandon abandon abandon abandon about"
    )
    assert verify_mnemonic(valid) is True


def test_verify_mnemonic_wrong_checksum() -> None:
    # 改最后一个词破 checksum
    bad = (
        "abandon abandon abandon abandon abandon abandon "
        "abandon abandon abandon abandon abandon abandon"
    )
    assert verify_mnemonic(bad) is False


def test_verify_mnemonic_invalid_word() -> None:
    bad = "notinwordlist " * 11 + "about"
    assert verify_mnemonic(bad) is False


def test_verify_mnemonic_wrong_word_count() -> None:
    # 11 词 (BIP-39 不支持)
    assert verify_mnemonic("abandon " * 10 + "about") is False


def test_verify_mnemonic_empty_or_none_safe() -> None:
    assert verify_mnemonic("") is False
    assert verify_mnemonic("   ") is False
    # 非 str 类型
    assert verify_mnemonic(None) is False  # type: ignore[arg-type]
    assert verify_mnemonic(12345) is False  # type: ignore[arg-type]


# ── mnemonic_to_master_key ───────────────────────────────────────────────────


def test_mnemonic_to_master_key_size_64() -> None:
    m = generate_mnemonic()
    seed = mnemonic_to_master_key(m)
    assert len(seed) == 64


def test_mnemonic_to_master_key_deterministic() -> None:
    m = generate_mnemonic()
    s1 = mnemonic_to_master_key(m)
    s2 = mnemonic_to_master_key(m)
    assert s1 == s2


def test_mnemonic_to_master_key_passphrase_changes_seed() -> None:
    m = generate_mnemonic()
    s_empty = mnemonic_to_master_key(m, passphrase="")
    s_pass = mnemonic_to_master_key(m, passphrase="my-extra")
    assert s_empty != s_pass


def test_mnemonic_to_master_key_invalid_raises() -> None:
    with pytest.raises(InvalidMnemonicError):
        mnemonic_to_master_key("not a valid mnemonic at all")


def test_mnemonic_to_master_key_bip39_test_vector() -> None:
    """BIP-39 标准测试向量: entropy 全 0 + passphrase "TREZOR" → 已知 seed."""
    m = (
        "abandon abandon abandon abandon abandon abandon "
        "abandon abandon abandon abandon abandon about"
    )
    expected_hex = (
        "c55257c360c07c72029aebc1b53c05ed0362ada38ead3e3e9efa3708e53495531"
        "f09a6987599d18264c1e1c92f2cf141630c7a3c4ab7c81b2f001698e7463b04"
    )
    seed = mnemonic_to_master_key(m, passphrase="TREZOR")
    assert seed.hex() == expected_hex


# ── derive_subkey ────────────────────────────────────────────────────────────


def test_derive_subkey_size_32() -> None:
    master = mnemonic_to_master_key(generate_mnemonic())
    sk = derive_subkey(master, "vault", index=0)
    assert len(sk) == SUBKEY_SIZE == 32


def test_derive_subkey_deterministic() -> None:
    master = mnemonic_to_master_key(generate_mnemonic())
    a = derive_subkey(master, "vault", 0)
    b = derive_subkey(master, "vault", 0)
    assert a == b


def test_derive_subkey_different_purpose_different_key() -> None:
    master = mnemonic_to_master_key(generate_mnemonic())
    vault_k = derive_subkey(master, "vault", 0)
    did_k = derive_subkey(master, "did", 0)
    assert vault_k != did_k


def test_derive_subkey_different_index_different_key() -> None:
    master = mnemonic_to_master_key(generate_mnemonic())
    k0 = derive_subkey(master, "skill", 0)
    k1 = derive_subkey(master, "skill", 1)
    assert k0 != k1


def test_derive_subkey_different_master_different_key() -> None:
    m1 = mnemonic_to_master_key(generate_mnemonic())
    m2 = mnemonic_to_master_key(generate_mnemonic())
    assert derive_subkey(m1, "vault", 0) != derive_subkey(m2, "vault", 0)


def test_derive_subkey_invalid_args_raise() -> None:
    master = mnemonic_to_master_key(generate_mnemonic())
    with pytest.raises(ValueError):
        derive_subkey("not bytes", "vault", 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        derive_subkey(b"", "vault", 0)
    with pytest.raises(ValueError):
        derive_subkey(master, "", 0)
    with pytest.raises(ValueError):
        derive_subkey(master, "vault", -1)


# ── save_mnemonic_to_file / load_mnemonic_from_file ─────────────────────────


def test_save_mnemonic_chmod_600(tmp_path: Path) -> None:
    m = generate_mnemonic()
    seed_file = tmp_path / "seed.txt"
    actual = save_mnemonic_to_file(m, seed_file)
    assert actual == seed_file.resolve()
    file_mode = stat.S_IMODE(seed_file.stat().st_mode)
    assert file_mode == 0o600, f"expected 0o600, got {oct(file_mode)}"


def test_save_mnemonic_creates_parent_dir(tmp_path: Path) -> None:
    m = generate_mnemonic()
    target = tmp_path / "deep" / "nested" / "seed.txt"
    save_mnemonic_to_file(m, target)
    assert target.exists()


def test_save_mnemonic_refuses_overwrite(tmp_path: Path) -> None:
    m = generate_mnemonic()
    seed_file = tmp_path / "seed.txt"
    save_mnemonic_to_file(m, seed_file)
    with pytest.raises(FileExistsError):
        save_mnemonic_to_file(m, seed_file)


def test_save_mnemonic_refuses_invalid(tmp_path: Path) -> None:
    with pytest.raises(InvalidMnemonicError):
        save_mnemonic_to_file("not valid mnemonic", tmp_path / "seed.txt")


def test_load_mnemonic_roundtrip(tmp_path: Path) -> None:
    m = generate_mnemonic()
    seed_file = tmp_path / "seed.txt"
    save_mnemonic_to_file(m, seed_file)
    loaded = load_mnemonic_from_file(seed_file)
    assert loaded == m


def test_load_mnemonic_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_mnemonic_from_file(tmp_path / "no-such.txt")


def test_load_mnemonic_invalid_content_raises(tmp_path: Path) -> None:
    seed_file = tmp_path / "seed.txt"
    seed_file.write_text("garbage content not bip39\n", encoding="utf-8")
    os.chmod(seed_file, 0o600)
    with pytest.raises(InvalidMnemonicError):
        load_mnemonic_from_file(seed_file)


def test_load_mnemonic_loose_permissions_raises(tmp_path: Path) -> None:
    """chmod 644 (group/other 可读) → 拒绝加载."""
    m = generate_mnemonic()
    seed_file = tmp_path / "seed.txt"
    save_mnemonic_to_file(m, seed_file)
    os.chmod(seed_file, 0o644)
    with pytest.raises(PermissionError, match="权限"):
        load_mnemonic_from_file(seed_file)


def test_default_seed_file_constant() -> None:
    """DEFAULT_SEED_FILE 应是 ~/.sisoul/seed.txt."""
    assert DEFAULT_SEED_FILE == Path.home() / ".sisoul" / "seed.txt"
