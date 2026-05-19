"""tests for sisoul.cli_commands.lend (波 5 dev-D)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli_commands.lend import lend_app
from sisoul.friend.lend import request_lend


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """让默认 ~/.sisoul/lend.db 落到 tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    yield


def test_help(runner: CliRunner) -> None:
    r = runner.invoke(lend_app, ["--help"])
    assert r.exit_code == 0


def test_list_empty(runner: CliRunner) -> None:
    r = runner.invoke(lend_app, ["list", "--json"])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["count"] == 0


def test_approve_flow(runner: CliRunner) -> None:
    req = request_lend(
        "alice.eth", "bob.eth", "llm_quota", 100, "x", mode="per-request",
    )
    r = runner.invoke(lend_app, ["approve", req.id, "--json"])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["status"] == "approved"


def test_deny_flow(runner: CliRunner) -> None:
    req = request_lend(
        "alice.eth", "bob.eth", "llm_quota", 100, "x", mode="per-request",
    )
    r = runner.invoke(lend_app, ["deny", req.id, "--reason", "no", "--json"])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["status"] == "denied"
    assert data["denied_reason"] == "no"


def test_approve_not_found_exits_1(runner: CliRunner) -> None:
    r = runner.invoke(lend_app, ["approve", "lr_nonexistent"])
    assert r.exit_code == 1


def test_deny_not_found_exits_1(runner: CliRunner) -> None:
    r = runner.invoke(lend_app, ["deny", "lr_nonexistent"])
    assert r.exit_code == 1


def test_history(runner: CliRunner) -> None:
    request_lend("a.eth", "b.eth", "llm_quota", 1, "x", mode="strong-tie-auto")
    r = runner.invoke(lend_app, ["history", "--json"])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["count"] >= 1


def test_list_after_pending(runner: CliRunner) -> None:
    request_lend("a.eth", "b.eth", "llm_quota", 1, "x", mode="per-request")
    request_lend("c.eth", "b.eth", "llm_quota", 2, "y", mode="per-request")
    r = runner.invoke(lend_app, ["list", "--json"])
    data = json.loads(r.stdout)
    assert data["count"] == 2
