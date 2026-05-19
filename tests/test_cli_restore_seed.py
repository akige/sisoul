"""tests for sisoul restore BIP-39 seed 模式 (Phase 2 W17-W20, 波 3 dev-A)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from sisoul.cli_commands.restore import (
    SEED_FILENAME,
    RestoreError,
    cli_restore,
    run_restore_from_seed,
)
from sisoul.identity import (
    InvalidMnemonicError,
    generate_mnemonic,
    save_mnemonic_to_file,
)


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "sisoul-restore-test"


# ── run_restore_from_seed: 直接传 mnemonic ──────────────────────────────────


def test_restore_from_seed_creates_vault(vault_root: Path) -> None:
    m = generate_mnemonic()
    paths = run_restore_from_seed(seed=m, vault_dir=vault_root)
    assert paths.dna.exists()
    seed_file = vault_root / SEED_FILENAME
    assert seed_file.exists()
    assert stat.S_IMODE(seed_file.stat().st_mode) == 0o600


def test_restore_from_seed_dna_marks_restored(vault_root: Path) -> None:
    m = generate_mnemonic()
    run_restore_from_seed(seed=m, vault_dir=vault_root)
    dna = json.loads((vault_root / "dna.json").read_text(encoding="utf-8"))
    assert dna["has_seed"] is True
    assert dna["restored_from_seed"] is True
    assert "master_key_hash" in dna
    assert len(dna["master_key_hash"]) == 16


def test_restore_from_seed_invalid_raises(vault_root: Path) -> None:
    with pytest.raises(InvalidMnemonicError):
        run_restore_from_seed(seed="totally bogus mnemonic 12 words", vault_dir=vault_root)


def test_restore_from_seed_existing_vault_no_force_raises(vault_root: Path) -> None:
    m = generate_mnemonic()
    run_restore_from_seed(seed=m, vault_dir=vault_root)
    with pytest.raises(SystemExit):
        run_restore_from_seed(seed=m, vault_dir=vault_root, force=False)


def test_restore_from_seed_force_overwrites(vault_root: Path) -> None:
    m1 = generate_mnemonic()
    run_restore_from_seed(seed=m1, vault_dir=vault_root)
    m2 = generate_mnemonic()
    paths = run_restore_from_seed(seed=m2, vault_dir=vault_root, force=True)
    assert paths.dna.exists()
    # 新 seed 写入
    new_seed = (vault_root / SEED_FILENAME).read_text(encoding="utf-8").strip()
    assert new_seed == m2
    assert new_seed != m1


# ── 跨设备一致性: 同 mnemonic → 同 master_key_hash ──────────────────────────


def test_restore_same_seed_same_master_key_hash(tmp_path: Path) -> None:
    m = generate_mnemonic()
    p1 = run_restore_from_seed(seed=m, vault_dir=tmp_path / "device1")
    p2 = run_restore_from_seed(seed=m, vault_dir=tmp_path / "device2")
    d1 = json.loads(p1.dna.read_text(encoding="utf-8"))
    d2 = json.loads(p2.dna.read_text(encoding="utf-8"))
    assert d1["master_key_hash"] == d2["master_key_hash"]


# ── --from-seed-file 模式 ────────────────────────────────────────────────────


def test_restore_from_seed_file(tmp_path: Path, vault_root: Path) -> None:
    m = generate_mnemonic()
    seed_file = tmp_path / "imported_seed.txt"
    save_mnemonic_to_file(m, seed_file)

    paths = run_restore_from_seed(
        seed=None, from_seed_file=seed_file, vault_dir=vault_root
    )
    assert paths.dna.exists()
    # 复制到 vault 后内容一致
    assert (vault_root / SEED_FILENAME).read_text(encoding="utf-8").strip() == m


def test_restore_from_seed_no_input_raises(vault_root: Path) -> None:
    with pytest.raises(RestoreError, match="必须传"):
        run_restore_from_seed(seed=None, from_seed_file=None, vault_dir=vault_root)


def test_restore_from_seed_both_inputs_raises(tmp_path: Path, vault_root: Path) -> None:
    m = generate_mnemonic()
    seed_file = tmp_path / "s.txt"
    save_mnemonic_to_file(m, seed_file)
    with pytest.raises(RestoreError, match="二选一"):
        run_restore_from_seed(
            seed=m, from_seed_file=seed_file, vault_dir=vault_root
        )


# ── CLI runner ───────────────────────────────────────────────────────────────


def _cli_app() -> typer.Typer:
    app = typer.Typer()
    app.command()(cli_restore)
    return app


def test_cli_restore_seed_positional(vault_root: Path) -> None:
    m = generate_mnemonic()
    runner = CliRunner()
    result = runner.invoke(_cli_app(), [m, "--vault-dir", str(vault_root)])
    assert result.exit_code == 0, result.output
    assert "BIP-39 seed" in result.output or "restored from" in result.output
    assert (vault_root / "dna.json").exists()


def test_cli_restore_seed_invalid_exit_2(vault_root: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        _cli_app(),
        ["bogus bogus bogus bogus bogus bogus bogus bogus bogus bogus bogus bogus",
         "--vault-dir", str(vault_root)],
    )
    assert result.exit_code == 2, result.output


def test_cli_restore_seed_file_flag(tmp_path: Path, vault_root: Path) -> None:
    m = generate_mnemonic()
    seed_file = tmp_path / "s.txt"
    save_mnemonic_to_file(m, seed_file)
    runner = CliRunner()
    result = runner.invoke(
        _cli_app(),
        ["--from-seed-file", str(seed_file), "--vault-dir", str(vault_root)],
    )
    assert result.exit_code == 0, result.output
    assert (vault_root / "dna.json").exists()


def test_cli_restore_no_input_exit_1(vault_root: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(_cli_app(), ["--vault-dir", str(vault_root)])
    assert result.exit_code == 1, result.output


# ── 兼容性: --from-zip 仍 work (波 2 dev-D 路径) ─────────────────────────────


def test_cli_restore_zip_path_still_works(tmp_path: Path, vault_root: Path) -> None:
    """ZIP restore 仍可走 (回归 dev-D 波 2 路径)."""
    import zipfile

    # 构造最小合法 ZIP
    zip_path = tmp_path / "test-export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        dna_content = json.dumps(
            {"sisoul_version": "0.1.0-dev", "vault_created_at": "2026-05-18T00:00:00+00:00"}
        )
        zf.writestr("vault/dna.json", dna_content)
        zf.writestr("vault/preferences/2026-05-18.md", "test pref")

    runner = CliRunner()
    result = runner.invoke(
        _cli_app(),
        ["--from-zip", str(zip_path), "--vault-dir", str(vault_root)],
    )
    assert result.exit_code == 0, result.output
    assert (vault_root / "dna.json").exists()
