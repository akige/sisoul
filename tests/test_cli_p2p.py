"""测试 cli_commands.p2p — 6 子命令 (波 4 dev-A)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli_commands.p2p import p2p_app
from sisoul.identity import generate_mnemonic, save_mnemonic_to_file
from sisoul.p2p import get_node, set_node, stop_node

runner = CliRunner()


@pytest.fixture
def vault_with_seed(tmp_path):
    """建一个带 seed.txt 的临时 vault dir."""
    vault = tmp_path / "vault"
    vault.mkdir()
    seed = generate_mnemonic(strength=128)
    save_mnemonic_to_file(seed, vault / "seed.txt")
    return vault


@pytest.fixture(autouse=True)
def cleanup_node():
    """每个 test 前后清空全局 node (防 cross-test 污染)."""
    yield
    node = get_node()
    if node is not None:
        try:
            asyncio.run(stop_node())
        except Exception:
            pass
    set_node(None)


# ── status (未 start) ────────────────────────────────────────────────────────


class TestStatusNotRunning:
    def test_status_no_node(self):
        res = runner.invoke(p2p_app, ["status"])
        assert res.exit_code == 0
        assert "not running" in res.stdout

    def test_status_json_no_node(self):
        res = runner.invoke(p2p_app, ["status", "--json"])
        assert res.exit_code == 0
        import json
        data = json.loads(res.stdout)
        assert data["running"] is False
        assert "libp2p_available" in data


# ── stop (未 start) ──────────────────────────────────────────────────────────


class TestStopWhenNotRunning:
    def test_stop_noop(self):
        res = runner.invoke(p2p_app, ["stop"])
        assert res.exit_code == 0
        assert "未 running" in res.stdout


# ── start + stop ─────────────────────────────────────────────────────────────


class TestStartStop:
    def test_start_with_vault_then_stop(self, vault_with_seed):
        res = runner.invoke(p2p_app, ["start", "--vault-dir", str(vault_with_seed), "--transport", "inmem"])
        assert res.exit_code == 0, res.stdout
        assert "started" in res.stdout
        assert "transport:" in res.stdout
        assert "inmem" in res.stdout

        # status 反映 running
        res2 = runner.invoke(p2p_app, ["status"])
        assert "running" in res2.stdout

        # stop
        res3 = runner.invoke(p2p_app, ["stop"])
        assert res3.exit_code == 0
        assert "stopped" in res3.stdout

    def test_start_missing_vault(self, tmp_path):
        res = runner.invoke(p2p_app, ["start", "--vault-dir", str(tmp_path / "nonexistent")])
        assert res.exit_code == 1
        assert "vault 不存在" in res.stdout or "vault 不存在" in res.stderr


# ── add-peer / list-peers ────────────────────────────────────────────────────


class TestAddListPeer:
    def test_add_then_list(self, vault_with_seed):
        runner.invoke(p2p_app, ["start", "--vault-dir", str(vault_with_seed), "--transport", "inmem"])
        res = runner.invoke(p2p_app, ["add-peer", "inmem://test-peer-id"])
        assert res.exit_code == 0, res.stdout
        assert "peer added" in res.stdout

        res2 = runner.invoke(p2p_app, ["list-peers"])
        assert "test-peer-id" in res2.stdout

        # JSON
        res3 = runner.invoke(p2p_app, ["list-peers", "--json"])
        import json
        data = json.loads(res3.stdout)
        assert any(p["peer_id"] == "test-peer-id" for p in data)

    def test_add_peer_without_start(self):
        res = runner.invoke(p2p_app, ["add-peer", "inmem://x"])
        assert res.exit_code == 1
        assert "未 running" in res.stdout or "未 running" in res.stderr

    def test_list_peers_empty(self, vault_with_seed):
        runner.invoke(p2p_app, ["start", "--vault-dir", str(vault_with_seed), "--transport", "inmem"])
        res = runner.invoke(p2p_app, ["list-peers"])
        assert "no peers" in res.stdout


# ── sync-now ─────────────────────────────────────────────────────────────────


class TestSyncNow:
    def test_sync_now_without_start(self):
        res = runner.invoke(p2p_app, ["sync-now"])
        assert res.exit_code == 1
        assert "未 running" in res.stdout or "未 running" in res.stderr

    def test_sync_now_no_peers(self, vault_with_seed):
        runner.invoke(p2p_app, ["start", "--vault-dir", str(vault_with_seed), "--transport", "inmem"])
        res = runner.invoke(p2p_app, ["sync-now"])
        assert res.exit_code == 0
        assert "无 known peer" in res.stdout
