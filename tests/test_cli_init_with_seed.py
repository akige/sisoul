"""tests for sisoul init BIP-39 seed 集成 (Phase 2 W17-W20, 波 3 dev-A)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from sisoul.cli_commands.init import (
    SEED_FILENAME,
    cli_init,
    run_init,
)
from sisoul.identity import (
    InvalidMnemonicError,
    generate_mnemonic,
    load_mnemonic_from_file,
    verify_mnemonic,
)


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "sisoul-test"


# ── run_init 默认生成 seed ───────────────────────────────────────────────────


def test_init_generates_seed_by_default(vault_root: Path) -> None:
    paths = run_init(goals="g1", vault_dir=vault_root, interactive=False)
    seed_file = paths.root / SEED_FILENAME
    assert seed_file.exists()
    # chmod 600
    mode = stat.S_IMODE(seed_file.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"
    # 内容是合法 BIP-39 12 词
    content = seed_file.read_text(encoding="utf-8").strip()
    assert verify_mnemonic(content)
    assert len(content.split()) == 12


def test_init_dna_marks_has_seed_true(vault_root: Path) -> None:
    paths = run_init(goals="g1", vault_dir=vault_root, interactive=False)
    dna = json.loads(paths.dna.read_text(encoding="utf-8"))
    assert dna["has_seed"] is True
    assert dna["schema_version"] == 2
    assert len(dna["master_key_hash"]) == 16


# ── --skip-seed ──────────────────────────────────────────────────────────────


def test_init_skip_seed_no_file(vault_root: Path) -> None:
    paths = run_init(
        goals="g1", vault_dir=vault_root, interactive=False, skip_seed=True
    )
    assert not (paths.root / SEED_FILENAME).exists()
    dna = json.loads(paths.dna.read_text(encoding="utf-8"))
    assert dna["has_seed"] is False


# ── --import-seed ────────────────────────────────────────────────────────────


def test_init_import_seed(vault_root: Path) -> None:
    pre_seed = generate_mnemonic()
    paths = run_init(
        goals="g1",
        vault_dir=vault_root,
        interactive=False,
        import_seed=pre_seed,
    )
    loaded = load_mnemonic_from_file(paths.root / SEED_FILENAME)
    assert loaded == pre_seed
    dna = json.loads(paths.dna.read_text(encoding="utf-8"))
    assert dna["has_seed"] is True


def test_init_import_invalid_seed_raises(vault_root: Path) -> None:
    with pytest.raises(InvalidMnemonicError):
        run_init(
            goals="g1",
            vault_dir=vault_root,
            interactive=False,
            import_seed="not valid mnemonic at all 12 words",
        )


# ── master_key_hash 跟 import_seed 派生一致 (确定性) ─────────────────────────


def test_init_same_imported_seed_same_master_hash(tmp_path: Path) -> None:
    pre_seed = generate_mnemonic()
    p1 = run_init(
        goals="g", vault_dir=tmp_path / "v1", interactive=False, import_seed=pre_seed
    )
    p2 = run_init(
        goals="g", vault_dir=tmp_path / "v2", interactive=False, import_seed=pre_seed
    )
    d1 = json.loads(p1.dna.read_text(encoding="utf-8"))
    d2 = json.loads(p2.dna.read_text(encoding="utf-8"))
    assert d1["master_key_hash"] == d2["master_key_hash"]


def test_init_different_imported_seed_different_master_hash(tmp_path: Path) -> None:
    seed_a = generate_mnemonic()
    seed_b = generate_mnemonic()
    p1 = run_init(
        goals="g", vault_dir=tmp_path / "va", interactive=False, import_seed=seed_a
    )
    p2 = run_init(
        goals="g", vault_dir=tmp_path / "vb", interactive=False, import_seed=seed_b
    )
    d1 = json.loads(p1.dna.read_text(encoding="utf-8"))
    d2 = json.loads(p2.dna.read_text(encoding="utf-8"))
    assert d1["master_key_hash"] != d2["master_key_hash"]


# ── CLI runner ───────────────────────────────────────────────────────────────


def _cli_app() -> typer.Typer:
    app = typer.Typer()
    app.command()(cli_init)
    return app


def test_cli_init_seed_visible_in_output(vault_root: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        _cli_app(), ["--goals", "g1,g2", "--vault-dir", str(vault_root)]
    )
    assert result.exit_code == 0, result.output
    # 终端应打印 12 词 seed (4x3 排版含 "1." 标号)
    assert "1." in result.output
    assert "12." in result.output
    assert "BIP-39" in result.output
    assert "seed" in result.output.lower()
    assert (vault_root / SEED_FILENAME).exists()


def test_cli_init_skip_seed_flag(vault_root: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        _cli_app(),
        ["--goals", "g1", "--vault-dir", str(vault_root), "--skip-seed"],
    )
    assert result.exit_code == 0, result.output
    assert not (vault_root / SEED_FILENAME).exists()


def test_cli_init_import_seed_flag(vault_root: Path) -> None:
    pre_seed = generate_mnemonic()
    runner = CliRunner()
    result = runner.invoke(
        _cli_app(),
        [
            "--goals", "g1",
            "--vault-dir", str(vault_root),
            "--import-seed", pre_seed,
        ],
    )
    assert result.exit_code == 0, result.output
    loaded = load_mnemonic_from_file(vault_root / SEED_FILENAME)
    assert loaded == pre_seed


def test_cli_init_invalid_import_seed_exit_2(vault_root: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        _cli_app(),
        [
            "--goals", "g1",
            "--vault-dir", str(vault_root),
            "--import-seed", "junk junk junk",
        ],
    )
    assert result.exit_code == 2, result.output
