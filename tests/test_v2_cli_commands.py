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


def test_sisoul_completion_bash():
    r = runner.invoke(app, ["completion", "bash"])
    assert r.exit_code == 0
    assert "_sisoul_complete" in r.stdout
    assert "complete -F" in r.stdout


def test_sisoul_completion_zsh():
    r = runner.invoke(app, ["completion", "zsh"])
    assert r.exit_code == 0
    assert "compdef" in r.stdout


def test_sisoul_completion_fish():
    r = runner.invoke(app, ["completion", "fish"])
    assert r.exit_code == 0
    assert "complete -c sisoul" in r.stdout


def test_sisoul_completion_invalid():
    r = runner.invoke(app, ["completion", "powershell"])
    assert r.exit_code != 0


def test_sisoul_completion_install(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = runner.invoke(app, ["completion", "bash", "--install"])
    assert r.exit_code == 0
    assert "OK installed" in r.stdout
    target = tmp_path / ".bash_completion.d" / "sisoul"
    assert target.exists()
    content = target.read_text()
    assert "_sisoul_complete" in content


def test_sisoul_friend_discover_help():
    r = runner.invoke(app, ["friend-discover", "--help"])
    assert r.exit_code == 0
    assert "mDNS" in r.stdout or "scan" in r.stdout


def test_sisoul_backup_creates_zip(tmp_path):
    # Setup a fake vault
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "dna.json").write_text('{"v": 1}')
    (vault / "petnames.json").write_text("{}")
    out = tmp_path / "backup.zip"

    r = runner.invoke(app, ["backup", "--vault", str(vault), "--out", str(out)])
    assert r.exit_code == 0, f"stdout: {r.stdout}\nexc: {r.exception}"
    assert out.exists()
    import zipfile
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert any("dna.json" in n for n in names)
        assert "sisoul-backup-manifest.json" in names


def test_sisoul_backup_no_vault(tmp_path):
    nonexistent = tmp_path / "no-vault"
    out_path = tmp_path / "b.zip"
    r = runner.invoke(app, ["backup", "--vault", str(nonexistent), "--out", str(out_path)])
    assert r.exit_code == 1
    assert not out_path.exists()


def test_sisoul_self_check_skip_all(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "dna.json").write_text('{"v": 1}')
    monkeypatch.setenv("SISOUL_VAULT", str(vault))
    r = runner.invoke(app, ["self-check", "--skip-daemon", "--skip-pytest"])
    # exit 0 if all green; we tolerate non-zero too as long as command runs
    assert "alpha launch" in r.stdout.lower() or "ALL" in r.stdout or "FAILED" in r.stdout


def test_sisoul_self_check_json(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "dna.json").write_text('{"v": 1}')
    monkeypatch.setenv("SISOUL_VAULT", str(vault))
    r = runner.invoke(app, ["self-check", "--skip-daemon", "--skip-pytest", "--json"])
    import json as _json
    data = _json.loads(r.stdout)
    assert "checks" in data
    assert "alpha_launch_ready" in data
