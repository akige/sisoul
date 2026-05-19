"""tests for sisoul.cli_commands.ledger (波 5 dev-D)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli_commands.ledger import ledger_app
from sisoul.friend.ledger import ReciprocityLedger


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    yield


def _seed_imbalance(self_did: str = "alice.eth", friend: str = "bob.eth") -> None:
    led = ReciprocityLedger(self_did=self_did)
    try:
        led.record_usage(self_did, friend, "llm_quota", 10000, "claude-opus-4-7",
                         enqueue_onchain=False)
        led.record_usage(friend, self_did, "llm_quota", 100, "claude-sonnet",
                         enqueue_onchain=False)
    finally:
        led.close()


def test_help(runner: CliRunner) -> None:
    r = runner.invoke(ledger_app, ["--help"])
    assert r.exit_code == 0


def test_show_balanced_empty(runner: CliRunner) -> None:
    r = runner.invoke(ledger_app, [
        "show", "bob.eth", "--self-did", "alice.eth", "--json"
    ])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["balance"]["borrowed_total"] == 0


def test_show_with_data(runner: CliRunner) -> None:
    _seed_imbalance()
    r = runner.invoke(ledger_app, [
        "show", "bob.eth", "--self-did", "alice.eth", "--json"
    ])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["balance"]["borrowed_total"] == 10000
    assert data["balance"]["imbalance_warning"] is True


def test_imbalance(runner: CliRunner) -> None:
    _seed_imbalance()
    r = runner.invoke(ledger_app, [
        "imbalance", "--self-did", "alice.eth", "--json"
    ])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["count"] >= 1
    assert any(w["friend_did"] == "bob.eth" for w in data["warnings"])


def test_stats(runner: CliRunner) -> None:
    _seed_imbalance()
    r = runner.invoke(ledger_app, ["stats", "--json"])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["total"] >= 2


def test_friends(runner: CliRunner) -> None:
    _seed_imbalance()
    r = runner.invoke(ledger_app, [
        "friends", "--self-did", "alice.eth", "--json"
    ])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert "bob.eth" in data["friends"]


def test_record(runner: CliRunner) -> None:
    r = runner.invoke(ledger_app, [
        "record", "bob.eth", "llm_quota", "500",
        "--self-did", "alice.eth", "--direction", "borrow",
        "--no-onchain", "--json",
    ])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["amount"] == 500
    assert data["borrower_did"] == "alice.eth"


def test_record_invalid_direction(runner: CliRunner) -> None:
    r = runner.invoke(ledger_app, [
        "record", "bob.eth", "llm_quota", "1",
        "--self-did", "alice.eth", "--direction", "weird",
    ])
    assert r.exit_code == 1


def test_record_invalid_resource(runner: CliRunner) -> None:
    r = runner.invoke(ledger_app, [
        "record", "bob.eth", "weird", "1",
        "--self-did", "alice.eth",
    ])
    assert r.exit_code == 1
