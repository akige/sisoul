"""Phase 4 W54-W58 · 波 5 dev-B.

CLI: sisoul proxy {start,stop,status}.
"""

from __future__ import annotations

import json

import pytest
import typer
from typer.testing import CliRunner

from sisoul.cli_commands.proxy import proxy_app
from sisoul.friend.encrypted_proxy import (
    EncryptedProxy,
    derive_friend_session_keypair,
    get_global_proxy,
    set_global_proxy,
)
from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def clear_global_proxy():
    set_global_proxy(None)
    yield
    set_global_proxy(None)


@pytest.fixture
def make_app():
    """build a typer.Typer that mounts proxy_app for testing."""
    app = typer.Typer()
    app.add_typer(proxy_app, name="proxy")
    return app


# ── status ───────────────────────────────────────────────────────────────────


class TestStatus:
    def test_status_not_started(self, runner, make_app):
        result = runner.invoke(make_app, ["proxy", "status"])
        assert result.exit_code == 0
        assert "未启动" in result.stdout or "not" in result.stdout.lower()

    def test_status_json_not_started(self, runner, make_app):
        result = runner.invoke(make_app, ["proxy", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["running"] is False
        assert data["sessions"] == []

    def test_status_running(self, runner, make_app):
        # 注册 proxy
        master = mnemonic_to_master_key(generate_mnemonic(128))
        priv, pub = derive_friend_session_keypair(master, 0)
        proxy = EncryptedProxy(
            self_priv=priv, self_pub=pub,
            self_did="bob.sisoul.eth",
        )
        set_global_proxy(proxy)

        result = runner.invoke(make_app, ["proxy", "status"])
        assert result.exit_code == 0
        assert "bob.sisoul.eth" in result.stdout
        assert "active sessions" in result.stdout

    def test_status_json_running(self, runner, make_app):
        master = mnemonic_to_master_key(generate_mnemonic(128))
        priv, pub = derive_friend_session_keypair(master, 0)
        proxy = EncryptedProxy(
            self_priv=priv, self_pub=pub,
            self_did="bob.sisoul.eth",
        )
        set_global_proxy(proxy)

        result = runner.invoke(make_app, ["proxy", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["running"] is True
        assert data["self_did"] == "bob.sisoul.eth"
        assert len(data["pubkey_hex"]) == 64  # 32 bytes hex
        assert data["session_count"] == 0


# ── stop ─────────────────────────────────────────────────────────────────────


class TestStop:
    def test_stop_not_started(self, runner, make_app):
        result = runner.invoke(make_app, ["proxy", "stop"])
        assert result.exit_code == 0
        assert "no-op" in result.stdout or "未启动" in result.stdout

    def test_stop_clears_proxy(self, runner, make_app):
        master = mnemonic_to_master_key(generate_mnemonic(128))
        priv, pub = derive_friend_session_keypair(master, 0)
        proxy = EncryptedProxy(
            self_priv=priv, self_pub=pub,
            self_did="bob.sisoul.eth",
        )
        set_global_proxy(proxy)

        result = runner.invoke(make_app, ["proxy", "stop"])
        assert result.exit_code == 0
        assert "stopped" in result.stdout
        assert get_global_proxy() is None


# ── start ────────────────────────────────────────────────────────────────────


class TestStart:
    def test_start_no_seed_fails(self, runner, make_app, tmp_path, monkeypatch):
        from sisoul.identity import seed as seed_mod

        nonexistent = tmp_path / "no-seed.txt"
        monkeypatch.setattr(seed_mod, "DEFAULT_SEED_FILE", nonexistent)

        result = runner.invoke(make_app, ["proxy", "start"])
        assert result.exit_code == 1
        # typer.echo(..., err=True) 在 typer 新版下输出到 stderr (CliRunner 默认拼到 result.output)
        combined = (result.stdout or "") + (getattr(result, "stderr", "") or "") + (result.output or "")
        assert "seed" in combined.lower() or "init" in combined.lower()

    def test_start_with_seed(self, runner, make_app, tmp_path, monkeypatch):
        from sisoul.identity import seed as seed_mod

        seed_file = tmp_path / "seed.txt"
        mnemonic = generate_mnemonic(128)
        seed_mod.save_mnemonic_to_file(mnemonic, seed_file)
        monkeypatch.setattr(seed_mod, "DEFAULT_SEED_FILE", seed_file)

        result = runner.invoke(make_app, ["proxy", "start", "--friend-index", "0"])
        assert result.exit_code == 0, f"stderr: {result.stdout}"
        assert "注册完成" in result.stdout or "✅" in result.stdout
        assert "pubkey" in result.stdout

        proxy = get_global_proxy()
        assert proxy is not None
        assert len(proxy.self_pub.encode()) == 32

    def test_start_already_running(self, runner, make_app, tmp_path, monkeypatch):
        from sisoul.identity import seed as seed_mod

        seed_file = tmp_path / "seed.txt"
        mnemonic = generate_mnemonic(128)
        seed_mod.save_mnemonic_to_file(mnemonic, seed_file)
        monkeypatch.setattr(seed_mod, "DEFAULT_SEED_FILE", seed_file)

        # first
        result1 = runner.invoke(make_app, ["proxy", "start"])
        assert result1.exit_code == 0
        # second should fail
        result2 = runner.invoke(make_app, ["proxy", "start"])
        assert result2.exit_code == 1
        assert "已有" in result2.stdout or "already" in result2.stdout.lower()
