"""tests/test_init_wizard.py — P2-EF 首启 wizard 5 步 (非交互路径).

5 步 wizard:
  [1/5] petname (env: SISOUL_INIT_PETNAME, 默认 hostname)
  [2/5] did:key 自动生 + 显前 12 + 后 4
  [3/5] LLM provider (env: SISOUL_INIT_PROVIDER, default/custom/skip)
  [4/5] daemon mode (env: SISOUL_INIT_DAEMON, background/foreground)
  [5/5] QR 输出 (env: SISOUL_INIT_QR, SISOUL_INIT_QR_OUT)

测试均用 --non-interactive + env 注入.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sisoul.cli import app
from sisoul.cli_commands.init import run_wizard

runner = CliRunner()


# ── run_wizard 直接调 (非 CLI) ────────────────────────────────────────────────


def test_run_wizard_writes_config_json(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    monkeypatch.setenv("SISOUL_INIT_PETNAME", "TestUser")
    monkeypatch.setenv("SISOUL_INIT_PROVIDER", "default")
    monkeypatch.setenv("SISOUL_INIT_DAEMON", "foreground")
    monkeypatch.setenv("SISOUL_INIT_QR", "1")

    cfg = run_wizard(vault_dir=vault, non_interactive=True)
    assert cfg["petname"] == "TestUser"
    assert cfg["provider"] == "default"
    assert cfg["daemon_mode"] == "foreground"
    assert cfg["did_preview"].startswith("did:key:z")
    assert cfg["vault_dir"] == str(vault)

    # wizard.json 持久化
    persisted = json.loads((vault / "wizard.json").read_text())
    assert persisted == cfg


def test_run_wizard_qr_png_generated(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault2"
    monkeypatch.setenv("SISOUL_INIT_PETNAME", "QrUser")
    monkeypatch.setenv("SISOUL_INIT_PROVIDER", "skip")
    monkeypatch.setenv("SISOUL_INIT_DAEMON", "background")
    monkeypatch.setenv("SISOUL_INIT_QR", "1")
    qr_out = tmp_path / "wizqr.png"
    monkeypatch.setenv("SISOUL_INIT_QR_OUT", str(qr_out))

    cfg = run_wizard(vault_dir=vault, non_interactive=True)
    assert cfg["qr_path"] == str(qr_out)
    assert qr_out.exists()
    assert qr_out.stat().st_size > 100


def test_run_wizard_skip_qr(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault3"
    monkeypatch.setenv("SISOUL_INIT_QR", "0")
    monkeypatch.setenv("SISOUL_INIT_PETNAME", "NoQr")
    monkeypatch.setenv("SISOUL_INIT_PROVIDER", "custom:openai")
    monkeypatch.setenv("SISOUL_INIT_DAEMON", "background")

    cfg = run_wizard(vault_dir=vault, non_interactive=True)
    assert cfg["qr_path"] is None
    assert cfg["provider"] == "custom:openai"


def test_run_wizard_invalid_env_falls_back(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault4"
    monkeypatch.setenv("SISOUL_INIT_PROVIDER", "garbage")
    monkeypatch.setenv("SISOUL_INIT_DAEMON", "garbage")
    monkeypatch.setenv("SISOUL_INIT_QR", "0")

    cfg = run_wizard(vault_dir=vault, non_interactive=True)
    # 非法 → fallback 默认
    assert cfg["provider"] == "skip"
    assert cfg["daemon_mode"] == "background"


# ── CLI: sisoul init --wizard --non-interactive ──────────────────────────────


def test_cli_init_wizard_non_interactive(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "wizvault"
    monkeypatch.setenv("SISOUL_INIT_PETNAME", "CliWiz")
    monkeypatch.setenv("SISOUL_INIT_PROVIDER", "default")
    monkeypatch.setenv("SISOUL_INIT_DAEMON", "background")
    monkeypatch.setenv("SISOUL_INIT_QR", "0")

    r = runner.invoke(
        app,
        [
            "init",
            "--wizard",
            "--non-interactive",
            "--vault-dir",
            str(vault),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "[1/5]" in r.output
    assert "[2/5]" in r.output
    assert "[3/5]" in r.output
    assert "[4/5]" in r.output
    assert "[5/5]" in r.output
    assert "CliWiz" in r.output
    assert "wizard 完成" in r.output
    assert (vault / "wizard.json").exists()


def test_cli_init_classic_still_works(tmp_path: Path) -> None:
    """关键: 加 wizard 不能破坏老 init 路径 (有 --goals 走 run_init)."""
    vault = tmp_path / "classic"
    r = runner.invoke(
        app,
        [
            "init",
            "--goals",
            "学 rust,跑步,读书",
            "--vault-dir",
            str(vault),
            "--skip-seed",
        ],
    )
    assert r.exit_code == 0, r.output
    assert (vault / "dna.json").exists()
    assert (vault / "goals").exists()
