"""tests for sisoul.cli_commands.status (Phase 1 W3)."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from sisoul.cli_commands.init import run_init
from sisoul.cli_commands.status import cli_status, render_status


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "sisoul-test"


def test_render_status_missing_vault(tmp_path: Path) -> None:
    out = render_status(vault_dir=tmp_path / "nope")
    assert "vault exists" in out
    assert "run `sisoul init`" in out
    assert "0 bytes" in out


def test_render_status_after_init(vault_root: Path) -> None:
    run_init(goals="目标1,目标2", vault_dir=vault_root, interactive=False)
    out = render_status(vault_dir=vault_root)
    assert "sisoul status" in out
    assert "2 active" in out
    assert "目标1" in out
    assert "目标2" in out
    assert "dna.sisoul_version" in out
    # daemon 测试时一般不在跑
    assert "daemon" in out


def test_render_status_shows_vault_size(vault_root: Path) -> None:
    run_init(goals="x", vault_dir=vault_root, interactive=False)
    out = render_status(vault_dir=vault_root)
    # 应有 bytes 行 (>0)
    assert " bytes |" in out


def test_render_status_no_goals_section_when_empty(tmp_path: Path) -> None:
    out = render_status(vault_dir=tmp_path / "nope")
    assert "## 长期目标" not in out


def test_cli_status_via_runner(vault_root: Path) -> None:
    run_init(goals="a", vault_dir=vault_root, interactive=False)
    app = typer.Typer()
    app.command()(cli_status)
    runner = CliRunner()
    result = runner.invoke(app, ["--vault-dir", str(vault_root)])
    assert result.exit_code == 0, result.output
    assert "sisoul status" in result.output
