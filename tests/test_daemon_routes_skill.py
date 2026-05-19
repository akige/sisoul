"""tests for sisoul.daemon_routes.skill (波 6 dev-A).

FastAPI TestClient. self-loop borrow + proxy-chat with mock_forwarder.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.skill import skill_router
from sisoul.friend.skill_borrow import _ACTIVE_SESSIONS
from sisoul.friend.skill_ipfs import clear_mock_blob_cache


@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(skill_router)
    return a


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch):
    _ACTIVE_SESSIONS.clear()
    clear_mock_blob_cache()
    monkeypatch.setenv("HOME", str(tmp_path))
    # monkeypatch DEFAULT_SEED_FILE (frozen at import)
    from sisoul.identity import seed as seed_mod
    seed_path = tmp_path / ".sisoul" / "seed.txt"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    m = seed_mod.generate_mnemonic(strength=128)
    seed_mod.save_mnemonic_to_file(m, path=seed_path)
    monkeypatch.setattr(seed_mod, "DEFAULT_SEED_FILE", seed_path)
    monkeypatch.delenv("PINATA_JWT", raising=False)
    yield
    _ACTIVE_SESSIONS.clear()
    clear_mock_blob_cache()


# ── routes 注册 ────────────────────────────────────────────────────────────


def test_routes_registered():
    paths = [r.path for r in skill_router.routes]
    assert "/sisoul/skill/create" in paths
    assert "/sisoul/skill/list" in paths
    assert "/sisoul/skill/lend" in paths
    assert "/sisoul/skill/borrow" in paths
    assert "/sisoul/skill/sessions" in paths
    assert "/sisoul/skill/end-session" in paths
    assert "/sisoul/skill/proxy-chat" in paths


# ── create ────────────────────────────────────────────────────────────────


def test_create_endpoint(client):
    resp = client.post("/sisoul/skill/create", json={
        "name": "solidity-expert",
        "system_prompt": "You are a Solidity expert.",
        "description": "DeFi specialist",
        "version": "0.3.2",
        "personality_traits": ["pedantic", "security-paranoid"],
        "recommended_models": ["claude-opus-4-7"],
        "owner_did": "did:sisoul:bob",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["skill_id"] == "solidity-expert"
    assert data["owner_did"] == "did:sisoul:bob"
    assert data["qualified_name"] == "did:sisoul:bob:solidity-expert"
    assert data["fingerprint"]


def test_create_bad_input(client):
    resp = client.post("/sisoul/skill/create", json={
        "name": "",  # 空 name
        "system_prompt": "x",
    })
    assert resp.status_code == 400


# ── list ──────────────────────────────────────────────────────────────────


def test_list_empty(client):
    resp = client.get("/sisoul/skill/list")
    assert resp.status_code == 200
    data = resp.json()
    assert data["owned"] == []
    assert data["available_to_borrow"] == []


def test_list_after_create(client):
    client.post("/sisoul/skill/create", json={
        "name": "s1", "system_prompt": "sp", "owner_did": "bob",
    })
    client.post("/sisoul/skill/create", json={
        "name": "s2", "system_prompt": "sp", "owner_did": "bob",
    })
    resp = client.get("/sisoul/skill/list")
    data = resp.json()
    ids = sorted(s["skill_id"] for s in data["owned"])
    assert ids == ["s1", "s2"]


# ── lend ──────────────────────────────────────────────────────────────────


def test_lend_no_pin(client):
    client.post("/sisoul/skill/create", json={
        "name": "s", "system_prompt": "sp", "owner_did": "bob",
    })
    resp = client.post("/sisoul/skill/lend", json={
        "skill_id": "s", "max_duration_minutes": 30,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["skill_id"] == "s"
    assert data["max_duration_minutes"] == 30
    assert data["ipfs_cid"] is None


def test_lend_not_found(client):
    resp = client.post("/sisoul/skill/lend", json={"skill_id": "nonexistent"})
    assert resp.status_code == 404


# ── borrow (self-loop) ────────────────────────────────────────────────────


def test_borrow_self_loop(client):
    client.post("/sisoul/skill/create", json={
        "name": "self-skill",
        "system_prompt": "x",
        "owner_did": "self.local",
    })
    resp = client.post("/sisoul/skill/borrow", json={
        "qualified_name": "self.local:self-skill",
        "duration_minutes": 5,
        "borrower_did": "self.local",
        "skip_permission_check": True,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["skill_id"] == "self-skill"
    assert data["owner_did"] == "self.local"
    assert data["duration_minutes"] == 5


def test_borrow_with_duration_override(client):
    client.post("/sisoul/skill/create", json={
        "name": "s", "system_prompt": "x", "owner_did": "self.local",
    })
    resp = client.post("/sisoul/skill/borrow", json={
        "qualified_name": "self.local:s",
        "duration_minutes": 30,
        "duration_seconds_override": 2,
        "borrower_did": "self.local",
        "skip_permission_check": True,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["expires_at"] - data["started_at"] == 2


def test_borrow_remote_not_implemented(client):
    resp = client.post("/sisoul/skill/borrow", json={
        "qualified_name": "remote.user:remote-skill",
        "borrower_did": "self.local",
    })
    assert resp.status_code == 501


def test_borrow_bad_qualified_name(client):
    resp = client.post("/sisoul/skill/borrow", json={
        "qualified_name": "nocolon",
    })
    assert resp.status_code == 400


# ── sessions + end-session ────────────────────────────────────────────────


def test_sessions_then_end(client):
    client.post("/sisoul/skill/create", json={
        "name": "s", "system_prompt": "x", "owner_did": "self.local",
    })
    borrow = client.post("/sisoul/skill/borrow", json={
        "qualified_name": "self.local:s", "borrower_did": "self.local",
        "skip_permission_check": True,
    }).json()
    sid = borrow["session_id"]

    list_resp = client.get("/sisoul/skill/sessions", params={"own_did": "self.local"})
    assert list_resp.status_code == 200
    sessions = list_resp.json()["sessions"]
    assert any(s["session_id"] == sid for s in sessions)

    end_resp = client.post("/sisoul/skill/end-session", json={
        "session_id": sid, "reason": "test-cleanup",
    })
    assert end_resp.status_code == 200
    ended = end_resp.json()
    assert ended["status"] == "destroyed"
    assert ended["destroy_reason"] == "test-cleanup"


def test_end_session_not_found(client):
    resp = client.post("/sisoul/skill/end-session", json={"session_id": "bs_nope"})
    assert resp.status_code == 404


# ── proxy-chat ────────────────────────────────────────────────────────────


def test_proxy_chat_with_mock_forwarder(client):
    """proxy-chat 用 use_mock_forwarder=True, 不真打 LLM API."""
    client.post("/sisoul/skill/create", json={
        "name": "s",
        "system_prompt": "skill system prompt",
        "owner_did": "self.local",
        "recommended_models": ["claude-opus-4-7"],
    })
    borrow = client.post("/sisoul/skill/borrow", json={
        "qualified_name": "self.local:s", "borrower_did": "self.local",
        "skip_permission_check": True,
    }).json()
    sid = borrow["session_id"]

    resp = client.post("/sisoul/skill/proxy-chat", json={
        "session_id": sid,
        "prompt": "how to write Solidity contract?",
        "use_mock_forwarder": True,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "mock-forwarder echo" in data["text"]
    assert data["model_used"] == "claude-opus-4-7"
    assert data["session_id"] == sid
    assert data["skill_id"] == "s"


def test_proxy_chat_session_not_found(client):
    resp = client.post("/sisoul/skill/proxy-chat", json={
        "session_id": "bs_nope",
        "prompt": "x",
        "use_mock_forwarder": True,
    })
    assert resp.status_code == 404


def test_proxy_chat_after_end_returns_404(client):
    client.post("/sisoul/skill/create", json={
        "name": "s", "system_prompt": "x", "owner_did": "self.local",
    })
    borrow = client.post("/sisoul/skill/borrow", json={
        "qualified_name": "self.local:s", "borrower_did": "self.local",
        "skip_permission_check": True,
    }).json()
    sid = borrow["session_id"]

    client.post("/sisoul/skill/end-session", json={"session_id": sid})

    resp = client.post("/sisoul/skill/proxy-chat", json={
        "session_id": sid, "prompt": "x", "use_mock_forwarder": True,
    })
    assert resp.status_code == 404
