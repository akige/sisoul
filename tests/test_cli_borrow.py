"""tests for sisoul.cli_commands.borrow (波 5 dev-D)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli_commands.borrow import borrow_app
from sisoul.friend.borrow import (
    ProxyResult,
    _reset_proxy_sessions_for_test,
    set_mock_proxy,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _clean() -> None:
    set_mock_proxy(None)
    _reset_proxy_sessions_for_test()
    yield
    set_mock_proxy(None)
    _reset_proxy_sessions_for_test()


def test_help(runner: CliRunner) -> None:
    r = runner.invoke(borrow_app, ["--help"])
    assert r.exit_code == 0
    assert "borrow" in r.stdout.lower()


def test_run_strong_tie_json(runner: CliRunner, tmp_path: Path,
                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    set_mock_proxy(lambda **kw: ProxyResult(
        text="ok", tokens_used=10, model_used="claude-opus-4-7",
        method="injected-mock"
    ))
    r = runner.invoke(borrow_app, [
        "run", "bob.eth", "llm_quota", "100",
        "--force-mode", "strong-tie-auto",
        "--json",
        "--no-onchain",
    ])
    assert r.exit_code == 0, r.stdout
    data = json.loads(r.stdout)
    assert data["status"] == "completed"
    assert data["lender_did"] == "bob.eth"


def test_proxy_start(runner: CliRunner) -> None:
    r = runner.invoke(borrow_app, [
        "proxy", "bob.eth", "--provider", "anthropic", "--json",
    ])
    assert r.exit_code == 0, r.stdout
    data = json.loads(r.stdout)
    assert data["session"]["lender_did"] == "bob.eth"
    assert "ANTHROPIC_BASE_URL=" in data["env_hint"]


def test_proxy_list_empty(runner: CliRunner) -> None:
    r = runner.invoke(borrow_app, ["proxy-list", "--json"])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["count"] == 0


def test_proxy_lifecycle(runner: CliRunner) -> None:
    r1 = runner.invoke(borrow_app, ["proxy", "bob.eth", "--provider", "anthropic", "--json"])
    sid = json.loads(r1.stdout)["session"]["session_id"]
    r2 = runner.invoke(borrow_app, ["proxy-list", "--json"])
    assert json.loads(r2.stdout)["count"] == 1
    r3 = runner.invoke(borrow_app, ["proxy-stop", sid, "--json"])
    assert r3.exit_code == 0
    assert json.loads(r3.stdout)["status"] == "stopped"


def test_proxy_stop_not_found(runner: CliRunner) -> None:
    r = runner.invoke(borrow_app, ["proxy-stop", "ps_nonexistent"])
    assert r.exit_code == 1


def test_run_emergency_denied(runner: CliRunner, tmp_path: Path,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    r = runner.invoke(borrow_app, [
        "run", "bob.eth", "llm_quota", "100",
        "--force-mode", "emergency-only",
        "--json",
        "--no-onchain",
    ])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["status"] == "lender-denied"
