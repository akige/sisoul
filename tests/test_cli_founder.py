"""Tests for `sisoul founder` CLI subcommands."""
from __future__ import annotations
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli_commands.founder import cli_founder

runner = CliRunner()


@pytest.fixture
def vault_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SISOUL_VAULT", str(tmp_path))
    fdir = tmp_path / "founder"
    (fdir / "cases").mkdir(parents=True)
    (fdir / "lessons").mkdir(parents=True)
    (fdir / "system_prompt.md").write_text("You are sisoul founder-agent. Cite cases.")
    (fdir / "cases/c1.json").write_text(json.dumps({
        "id": "c1",
        "question": "Why no token?",
        "answer": "Per whitepaper section 4.10. Tokens cause governance capture.",
        "did_author": "did:key:z6MkFounder",
        "tags": ["governance", "no-token"],
        "created_at": "2026-06-05T00:00:00Z",
    }))
    (fdir / "lessons/l1.json").write_text(json.dumps({
        "id": "l1",
        "principle": "Token temptation is the default failure mode.",
        "context": "Round 9 lesson.",
        "applies_to": ["governance"],
        "established_at": "2026-06-05",
    }))
    return tmp_path


def test_founder_status(vault_dir):
    result = runner.invoke(cli_founder, ["status"])
    assert result.exit_code == 0
    assert "Vault root" in result.stdout
    assert "cases=1" in result.stdout
    assert "lessons=1" in result.stdout


def test_founder_chat_retrieval_fallback(vault_dir):
    result = runner.invoke(cli_founder, ["chat", "Why no token?"])
    assert result.exit_code == 0
    assert "@founder" in result.stdout
    assert "section 4.10" in result.stdout
    assert "Recalled cases" in result.stdout
    assert "c1" in result.stdout


def test_founder_chat_no_record(vault_dir):
    result = runner.invoke(
        cli_founder, ["chat", "Why no token?", "--no-record"]
    )
    assert result.exit_code == 0
    # Log file should not exist when --no-record
    log_file = vault_dir / "founder/chat/log.jsonl"
    assert not log_file.exists()


def test_founder_chat_records_by_default(vault_dir):
    runner.invoke(cli_founder, ["chat", "Why no token?"])
    log_file = vault_dir / "founder/chat/log.jsonl"
    assert log_file.exists()
    lines = log_file.read_text().splitlines()
    assert len(lines) == 1


def test_founder_recall(vault_dir):
    result = runner.invoke(cli_founder, ["recall", "token governance"])
    assert result.exit_code == 0
    assert "c1" in result.stdout


def test_founder_recall_no_match(vault_dir):
    result = runner.invoke(cli_founder, ["recall", "xyzzy nonsense xxxx"])
    assert result.exit_code == 0
    assert "No cases matched" in result.stdout


def test_founder_recall_top_k(vault_dir):
    result = runner.invoke(cli_founder, ["recall", "token", "--top-k", "1"])
    assert result.exit_code == 0
    assert "c1" in result.stdout


def test_founder_cases_list(vault_dir):
    result = runner.invoke(cli_founder, ["cases"])
    assert result.exit_code == 0
    assert "1 cases" in result.stdout
    assert "c1" in result.stdout


def test_founder_lessons_list(vault_dir):
    result = runner.invoke(cli_founder, ["lessons"])
    assert result.exit_code == 0
    assert "l1" in result.stdout
    assert "Token temptation" in result.stdout


def test_founder_history_empty(vault_dir):
    result = runner.invoke(cli_founder, ["history"])
    assert result.exit_code == 0
    assert "No chat history" in result.stdout


def test_founder_history_after_chat(vault_dir):
    runner.invoke(cli_founder, ["chat", "Why no token?"])
    result = runner.invoke(cli_founder, ["history", "--last", "5"])
    assert result.exit_code == 0
    assert "Last 1 founder chats" in result.stdout
    assert "retrieval-only" in result.stdout


def test_founder_init_from_seed(tmp_path, monkeypatch):
    monkeypatch.setenv("SISOUL_VAULT", str(tmp_path / "vault"))
    seed = tmp_path / "seed"
    (seed / "cases").mkdir(parents=True)
    (seed / "system_prompt.md").write_text("seed prompt")
    (seed / "cases/c1.json").write_text(json.dumps({
        "id": "c1", "question": "q", "answer": "a", "did_author": "d",
        "tags": [], "created_at": "t",
    }))

    result = runner.invoke(cli_founder, ["init", "--from", str(seed)])
    assert result.exit_code == 0
    assert "initialized" in result.stdout.lower()
    assert (tmp_path / "vault/founder/system_prompt.md").read_text() == "seed prompt"


def test_founder_init_existing_no_force_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("SISOUL_VAULT", str(tmp_path / "vault"))
    fdir = tmp_path / "vault/founder"
    fdir.mkdir(parents=True)
    seed = tmp_path / "seed"
    seed.mkdir()

    result = runner.invoke(cli_founder, ["init", "--from", str(seed)])
    assert result.exit_code == 1
    assert "Already initialized" in result.stdout


def test_founder_init_force_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv("SISOUL_VAULT", str(tmp_path / "vault"))
    fdir = tmp_path / "vault/founder"
    fdir.mkdir(parents=True)
    (fdir / "old.txt").write_text("old")
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "system_prompt.md").write_text("new prompt")

    result = runner.invoke(cli_founder, ["init", "--from", str(seed), "--force"])
    assert result.exit_code == 0
    assert not (fdir / "old.txt").exists()  # old wiped
    assert (fdir / "system_prompt.md").read_text() == "new prompt"
