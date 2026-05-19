"""tests for sisoul.cli_commands.goals (Phase 1 W12)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli_commands.goals import (
    add_goal,
    goals_app,
    list_goals,
    update_progress,
)
from sisoul.cli_commands.init import run_init


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "sisoul-test"


def test_add_goal_creates_file_with_next_id(vault_root: Path) -> None:
    fp1 = add_goal("第一个目标", vault_dir=vault_root)
    fp2 = add_goal("第二个目标", vault_dir=vault_root)
    assert fp1.name == "goal-001.md"
    assert fp2.name == "goal-002.md"


def test_add_goal_empty_raises(vault_root: Path) -> None:
    with pytest.raises(ValueError):
        add_goal("   ", vault_dir=vault_root)


def test_list_goals_after_init(vault_root: Path) -> None:
    run_init(goals="A,B,C", vault_dir=vault_root, interactive=False)
    goals = list_goals(vault_dir=vault_root)
    assert len(goals) == 3
    assert {g["title"] for g in goals} == {"A", "B", "C"}
    assert all(g["progress"] == 0 for g in goals)


def test_list_goals_missing_vault_returns_empty(tmp_path: Path) -> None:
    assert list_goals(vault_dir=tmp_path / "nope") == []


def test_update_progress_clamp(vault_root: Path) -> None:
    add_goal("x", vault_dir=vault_root)
    old, new = update_progress("goal-001", 30, vault_dir=vault_root)
    assert (old, new) == (0, 30)
    old, new = update_progress("goal-001", 200, vault_dir=vault_root)
    assert new == 100  # clamp upper
    old, new = update_progress("goal-001", -500, vault_dir=vault_root)
    assert new == 0  # clamp lower


def test_update_progress_auto_complete(vault_root: Path) -> None:
    add_goal("x", vault_dir=vault_root)
    update_progress("goal-001", 100, vault_dir=vault_root)
    goals = list_goals(vault_dir=vault_root)
    assert goals[0]["status"] == "completed"


def test_update_progress_missing_id_raises(vault_root: Path) -> None:
    with pytest.raises(FileNotFoundError):
        update_progress("goal-999", 10, vault_dir=vault_root)


def test_cli_goals_add_and_list(vault_root: Path) -> None:
    runner = CliRunner()
    r1 = runner.invoke(
        goals_app, ["add", "新目标", "--vault-dir", str(vault_root)]
    )
    assert r1.exit_code == 0, r1.output
    assert "新目标已加" in r1.output

    r2 = runner.invoke(goals_app, ["list", "--vault-dir", str(vault_root)])
    assert r2.exit_code == 0, r2.output
    assert "新目标" in r2.output
    assert "0/100" in r2.output


def test_cli_goals_list_empty(vault_root: Path) -> None:
    runner = CliRunner()
    r = runner.invoke(goals_app, ["list", "--vault-dir", str(vault_root)])
    assert r.exit_code == 0
    assert "无长期目标" in r.output


def test_cli_goals_progress(vault_root: Path) -> None:
    add_goal("x", vault_dir=vault_root)
    runner = CliRunner()
    r = runner.invoke(
        goals_app,
        ["progress", "goal-001", "50", "--vault-dir", str(vault_root)],
    )
    assert r.exit_code == 0, r.output
    assert "0 → 50" in r.output


def test_cli_goals_progress_missing_id(vault_root: Path) -> None:
    runner = CliRunner()
    r = runner.invoke(
        goals_app,
        ["progress", "goal-999", "10", "--vault-dir", str(vault_root)],
    )
    assert r.exit_code == 1
