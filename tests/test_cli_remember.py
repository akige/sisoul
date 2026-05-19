"""tests for sisoul.cli_commands.remember (Phase 1 W11)."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from sisoul.cli_commands.remember import cli_remember, run_remember
from sisoul.vault import VaultPaths, list_files, load_frontmatter, read_file


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "sisoul-test"


def test_remember_writes_file_with_frontmatter(vault_root: Path) -> None:
    fp = run_remember("我用 Tailwind CSS", vault_dir=vault_root)
    assert fp.exists()
    assert fp.parent == VaultPaths(root=vault_root).preferences_dir
    text = read_file(fp)
    meta, body = load_frontmatter(text)
    assert meta["scope"] == "default"
    assert meta["verified"] is False
    assert "timestamp" in meta
    assert "Tailwind" in body


def test_remember_appends_same_day_file(vault_root: Path) -> None:
    fp1 = run_remember("偏好 A", vault_dir=vault_root)
    fp2 = run_remember("偏好 B", vault_dir=vault_root)
    # 同日 → 同一文件
    assert fp1 == fp2
    text = read_file(fp1)
    # 应含 2 个 frontmatter block
    assert text.count("---") >= 4
    assert "偏好 A" in text
    assert "偏好 B" in text


def test_remember_empty_text_raises(vault_root: Path) -> None:
    with pytest.raises(ValueError):
        run_remember("", vault_dir=vault_root)
    with pytest.raises(ValueError):
        run_remember("   ", vault_dir=vault_root)


def test_remember_creates_preferences_dir(vault_root: Path) -> None:
    assert not vault_root.exists()
    run_remember("x", vault_dir=vault_root)
    assert VaultPaths(root=vault_root).preferences_dir.is_dir()


def test_remember_custom_scope(vault_root: Path) -> None:
    fp = run_remember("project-specific 偏好", scope="project", vault_dir=vault_root)
    meta, _ = load_frontmatter(read_file(fp))
    assert meta["scope"] == "project"


def test_cli_remember_via_runner(vault_root: Path) -> None:
    app = typer.Typer()
    app.command()(cli_remember)
    runner = CliRunner()
    result = runner.invoke(
        app, ["我用 Vue", "--vault-dir", str(vault_root)]
    )
    assert result.exit_code == 0, result.output
    assert "偏好已写入" in result.output
    files = list_files(VaultPaths(root=vault_root).preferences_dir, "*.md")
    assert len(files) == 1
