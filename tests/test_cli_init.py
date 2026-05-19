"""tests for sisoul.cli_commands.init (Phase 1 W3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from sisoul.cli_commands.init import (
    InitAbort,
    cli_init,
    run_init,
)
from sisoul.vault import load_frontmatter, read_file


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "sisoul-test"


def test_run_init_creates_dna_and_goals(vault_root: Path) -> None:
    paths = run_init(
        goals="目标A,目标B,目标C", vault_dir=vault_root, interactive=False
    )
    assert paths.dna.exists()
    dna = json.loads(read_file(paths.dna))
    assert "sisoul_version" in dna
    assert "vault_created_at" in dna
    assert "master_key_hash" in dna
    assert len(dna["master_key_hash"]) == 16

    goal_files = sorted(paths.goals_dir.glob("goal-*.md"))
    assert len(goal_files) == 3
    titles = []
    for fp in goal_files:
        meta, _ = load_frontmatter(read_file(fp))
        assert meta["progress"] == 0
        assert meta["status"] == "active"
        titles.append(meta["title"])
    assert titles == ["目标A", "目标B", "目标C"]


def test_run_init_creates_empty_subdirs(vault_root: Path) -> None:
    paths = run_init(goals="x", vault_dir=vault_root, interactive=False)
    assert paths.preferences_dir.is_dir()
    assert paths.chat_history_dir.is_dir()
    # preferences 默认空
    assert list(paths.preferences_dir.iterdir()) == []


def test_run_init_abort_on_existing(vault_root: Path) -> None:
    run_init(goals="a", vault_dir=vault_root, interactive=False)
    with pytest.raises(InitAbort):
        run_init(goals="b", vault_dir=vault_root, interactive=False)


def test_run_init_force_overrides(vault_root: Path) -> None:
    run_init(goals="a", vault_dir=vault_root, interactive=False)
    # force 不抛
    paths = run_init(
        goals="b,c", vault_dir=vault_root, interactive=False, force=True
    )
    assert paths.dna.exists()


def test_run_init_validates_goal_count(tmp_path: Path) -> None:
    # 用独立 vault_root 避免第一次失败遗留 dir
    # 0 个 (空 string 全过滤掉)
    with pytest.raises(typer.BadParameter):
        run_init(goals=",,,", vault_dir=tmp_path / "v1", interactive=False)
    # 4 个 (超 MAX_GOALS=3)
    with pytest.raises(typer.BadParameter):
        run_init(goals="a,b,c,d", vault_dir=tmp_path / "v2", interactive=False)


def test_cli_init_via_runner(vault_root: Path) -> None:
    """通过 typer.testing.CliRunner 走 CLI 层."""
    app = typer.Typer()
    app.command()(cli_init)
    runner = CliRunner()
    result = runner.invoke(
        app, ["--goals", "a,b", "--vault-dir", str(vault_root)]
    )
    assert result.exit_code == 0, result.output
    assert "vault 已建" in result.output
    assert (vault_root / "dna.json").exists()


def test_cli_init_abort_exit_code(vault_root: Path) -> None:
    run_init(goals="x", vault_dir=vault_root, interactive=False)
    app = typer.Typer()
    app.command()(cli_init)
    runner = CliRunner()
    result = runner.invoke(
        app, ["--goals", "y", "--vault-dir", str(vault_root)]
    )
    assert result.exit_code == 1
