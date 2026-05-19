"""测试 daemon_routes.p2p — FastAPI TestClient (波 4 dev-A).

⚠️ router 命名规范验证: 必须 ``p2p_router`` (不是 ``router``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.p2p import p2p_router
from sisoul.identity import generate_mnemonic, save_mnemonic_to_file
from sisoul.p2p import get_node, set_node, stop_node


@pytest.fixture
def app():
    """构造 FastAPI app 仅含 p2p_router."""
    a = FastAPI()
    a.include_router(p2p_router)
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def vault_with_seed(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    seed = generate_mnemonic(strength=128)
    save_mnemonic_to_file(seed, vault / "seed.txt")
    return vault


@pytest.fixture(autouse=True)
def cleanup_node():
    yield
    n = get_node()
    if n is not None:
        try:
            asyncio.run(stop_node())
        except Exception:
            pass
    set_node(None)


# ── router 命名 ───────────────────────────────────────────────────────────────


def test_router_name_is_p2p_router():
    """强制规范: 主集成依赖 ``p2p_router`` 名."""
    from sisoul.daemon_routes import p2p as p2p_mod
    assert hasattr(p2p_mod, "p2p_router")
    assert p2p_mod.p2p_router.prefix == "/sisoul/p2p"


# ── GET /status ──────────────────────────────────────────────────────────────


class TestStatus:
    def test_status_no_node(self, client):
        res = client.get("/sisoul/p2p/status")
        assert res.status_code == 200
        data = res.json()
        assert data["running"] is False
        assert "libp2p_available" in data
        assert "aiortc_available" in data
        assert data["peers"] == []

    def test_status_after_start(self, client, vault_with_seed):
        client.post("/sisoul/p2p/start", json={
            "vault_dir": str(vault_with_seed), "port": 0, "transport": "inmem"
        })
        res = client.get("/sisoul/p2p/status")
        data = res.json()
        assert data["running"] is True
        assert data["transport"] == "inmem"
        assert data["peer_id"]


# ── POST /start ──────────────────────────────────────────────────────────────


class TestStart:
    def test_start_basic(self, client, vault_with_seed):
        res = client.post("/sisoul/p2p/start", json={
            "vault_dir": str(vault_with_seed), "port": 0, "transport": "inmem"
        })
        assert res.status_code == 201
        data = res.json()
        assert data["ok"] is True
        assert data["transport"] == "inmem"
        assert data["peer_id"]

    def test_start_missing_vault(self, client, tmp_path):
        res = client.post("/sisoul/p2p/start", json={
            "vault_dir": str(tmp_path / "nonexistent"), "port": 0
        })
        assert res.status_code == 404

    def test_double_start_conflict(self, client, vault_with_seed):
        client.post("/sisoul/p2p/start", json={
            "vault_dir": str(vault_with_seed), "transport": "inmem"
        })
        res = client.post("/sisoul/p2p/start", json={
            "vault_dir": str(vault_with_seed), "transport": "inmem"
        })
        assert res.status_code == 409


# ── POST /stop ───────────────────────────────────────────────────────────────


class TestStop:
    def test_stop_no_node(self, client):
        res = client.post("/sisoul/p2p/stop")
        assert res.status_code == 200
        assert "no-op" in res.json()["message"]

    def test_stop_after_start(self, client, vault_with_seed):
        client.post("/sisoul/p2p/start", json={
            "vault_dir": str(vault_with_seed), "transport": "inmem"
        })
        res = client.post("/sisoul/p2p/stop")
        assert res.status_code == 200
        assert res.json()["ok"] is True


# ── POST /add-peer + GET /peers ──────────────────────────────────────────────


class TestAddPeer:
    def test_add_then_get_peers(self, client, vault_with_seed):
        client.post("/sisoul/p2p/start", json={
            "vault_dir": str(vault_with_seed), "transport": "inmem"
        })
        res = client.post("/sisoul/p2p/add-peer", json={
            "multiaddr": "inmem://alice-test"
        })
        assert res.status_code == 201
        peer = res.json()
        assert peer["peer_id"] == "alice-test"

        res2 = client.get("/sisoul/p2p/peers")
        peers = res2.json()["peers"]
        assert any(p["peer_id"] == "alice-test" for p in peers)

    def test_add_peer_without_start(self, client):
        res = client.post("/sisoul/p2p/add-peer", json={"multiaddr": "inmem://x"})
        assert res.status_code == 409

    def test_get_peers_no_node(self, client):
        res = client.get("/sisoul/p2p/peers")
        assert res.status_code == 200
        assert res.json()["peers"] == []


# ── POST /sync ───────────────────────────────────────────────────────────────


class TestSync:
    def test_sync_no_node(self, client):
        res = client.post("/sisoul/p2p/sync", json={})
        assert res.status_code == 409

    def test_sync_no_peers_returns_empty(self, client, vault_with_seed):
        client.post("/sisoul/p2p/start", json={
            "vault_dir": str(vault_with_seed), "transport": "inmem"
        })
        res = client.post("/sisoul/p2p/sync", json={})
        assert res.status_code == 200
        assert res.json()["results"] == []
