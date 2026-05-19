"""测试 cli_commands.peer — relay-mode toggle + probe-stun (Wave A #16)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli_commands.peer import peer_app

runner = CliRunner()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "p2p").mkdir(parents=True)
    return tmp_path


class TestRelayMode:
    def test_status_default_off(self, vault):
        result = runner.invoke(peer_app, ["relay-mode", "status", "--vault-dir", str(vault)])
        assert result.exit_code == 0
        assert "OFF" in result.stdout

    def test_on_then_status(self, vault):
        r = runner.invoke(peer_app, ["relay-mode", "on", "--vault-dir", str(vault)])
        assert r.exit_code == 0
        assert "ON" in r.stdout

        r = runner.invoke(peer_app, ["relay-mode", "status", "--vault-dir", str(vault)])
        assert r.exit_code == 0
        assert "ON" in r.stdout

    def test_on_off_roundtrip(self, vault):
        runner.invoke(peer_app, ["relay-mode", "on", "--vault-dir", str(vault)])
        r = runner.invoke(peer_app, ["relay-mode", "off", "--vault-dir", str(vault)])
        assert r.exit_code == 0
        assert "OFF" in r.stdout
        state_file = vault / "p2p" / "relay_state.json"
        assert state_file.exists()
        st = json.loads(state_file.read_text())
        assert st["enabled"] is False

    def test_status_json(self, vault):
        runner.invoke(peer_app, ["relay-mode", "on", "--vault-dir", str(vault)])
        r = runner.invoke(peer_app, ["relay-mode", "status", "--vault-dir", str(vault), "--json"])
        assert r.exit_code == 0
        data = json.loads(r.stdout)
        assert data["enabled"] is True
        assert data["since_ts"] > 0

    def test_invalid_action(self, vault):
        r = runner.invoke(peer_app, ["relay-mode", "wat", "--vault-dir", str(vault)])
        assert r.exit_code != 0


class TestProbeStun:
    def test_probe_stun_json_mock(self, monkeypatch):
        """mock probe_stun_pool, 验 CLI 正确序列化."""
        import sisoul.cli_commands.peer as peer_mod
        from sisoul.p2p.stun_pool import StunProbeResult

        async def fake_probe(urls, timeout_sec=5.0):
            return [
                StunProbeResult(url=urls[0], alive=True, latency_ms=20.0, reflexive_ip="1.2.3.4", reflexive_port=5000),
            ]

        async def fake_pool(urls, timeout_sec=5.0):
            return await fake_probe(urls, timeout_sec)

        monkeypatch.setattr("sisoul.p2p.stun_pool.probe_stun_pool", fake_pool)
        monkeypatch.setattr("sisoul.p2p.stun_pool.load_stun_pool_from_env", lambda: ["stun:fake:3478"])

        r = runner.invoke(peer_app, ["probe-stun", "--json"])
        assert r.exit_code == 0
        data = json.loads(r.stdout)
        assert len(data) == 1
        assert data[0]["alive"] is True
        assert data[0]["reflexive_ip"] == "1.2.3.4"
