"""Tests for v2 CLI commands (stats / case / debate / invite / health / demo)."""
from __future__ import annotations
import json
import subprocess
import sys

from typer.testing import CliRunner

from sisoul.cli import app


runner = CliRunner()


def test_sisoul_stats_runs():
    r = runner.invoke(app, ["stats"])
    assert r.exit_code == 0
    assert "sisoul stats" in r.stdout


def test_sisoul_stats_json():
    r = runner.invoke(app, ["stats", "--json"])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert "cases_count" in data
    assert "skills_count" in data


def test_sisoul_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0
    assert "sisoul" in r.stdout
    assert "1.0.0-alpha" in r.stdout


def test_sisoul_version_json():
    r = runner.invoke(app, ["--version-json"])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["version"] == "1.0.0-alpha"
    assert "modules" in data


def test_sisoul_case_help():
    r = runner.invoke(app, ["case", "--help"])
    assert r.exit_code == 0
    assert "list" in r.stdout
    assert "search" in r.stdout
    assert "show" in r.stdout
    assert "add" in r.stdout


def test_sisoul_debate_help():
    r = runner.invoke(app, ["debate", "--help"])
    assert r.exit_code == 0
    assert "agents" in r.stdout
    assert "rounds" in r.stdout


def test_sisoul_health_help():
    r = runner.invoke(app, ["health", "--help"])
    assert r.exit_code == 0
    assert "daemon" in r.stdout.lower()


def test_sisoul_demo_help():
    r = runner.invoke(app, ["demo", "--help"])
    assert r.exit_code == 0


def test_sisoul_invite_generates_text():
    r = runner.invoke(app, [
        "invite", "--did", "did:key:z6MkAlice",
        "--petname", "Alice",
    ])
    assert r.exit_code == 0
    assert "did:key:z6MkAlice" in r.stdout
    assert "sisoul" in r.stdout.lower()
    assert "Alice" in r.stdout


def test_sisoul_invite_json():
    r = runner.invoke(app, [
        "invite", "--did", "did:key:z6MkBob",
        "--petname", "Bob",
        "--json",
    ])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["invite_json"]["did"] == "did:key:z6MkBob"
    assert data["invite_json"]["petname_hint"] == "Bob"
    assert "sisoul://invite?" in data["short_url"]


def test_sisoul_invite_short_url():
    r = runner.invoke(app, [
        "invite", "--did", "did:key:z6MkCarol",
        "--petname", "Carol",
        "--short-url",
    ])
    assert r.exit_code == 0
    assert r.stdout.startswith("sisoul://invite?")
    assert "z6MkCarol" in r.stdout


def test_sisoul_invite_writes_file(tmp_path):
    output = tmp_path / "invite.txt"
    r = runner.invoke(app, [
        "invite", "--did", "did:key:z6MkDave",
        "--petname", "Dave",
        "--out", str(output),
    ])
    assert r.exit_code == 0
    assert output.exists()
    content = output.read_text()
    assert "did:key:z6MkDave" in content


def test_sisoul_cheatsheet():
    r = runner.invoke(app, ["cheatsheet"])
    assert r.exit_code == 0
    assert "Quick Reference" in r.stdout
    assert "sisoul init" in r.stdout
    assert "sisoul demo" in r.stdout
    assert "sisoul invite" in r.stdout
