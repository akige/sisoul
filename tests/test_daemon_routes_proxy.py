"""Phase 4 W54-W58 · 波 5 dev-B.

daemon HTTP route: POST /sisoul/proxy/forward, GET /sessions, POST /end-session.
"""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.proxy import proxy_router
from sisoul.friend.encrypted_proxy import (
    EncryptedProxy,
    derive_friend_session_keypair,
    set_global_proxy,
)
from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key


@pytest.fixture
def app():
    a = FastAPI()
    a.include_router(proxy_router)
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_proxy():
    set_global_proxy(None)
    yield
    set_global_proxy(None)


@pytest.fixture
def alice_keypair():
    master = mnemonic_to_master_key(generate_mnemonic(128))
    return derive_friend_session_keypair(master, 0)


@pytest.fixture
def bob_proxy():
    master = mnemonic_to_master_key(generate_mnemonic(128))
    priv, pub = derive_friend_session_keypair(master, 0)

    def mock_forwarder(prompt, model, provider="anthropic", api_key=None, **kw):
        return f"REPLY[{model}]={prompt[:30]}", len(prompt) // 4, 20

    proxy = EncryptedProxy(
        self_priv=priv, self_pub=pub,
        self_did="bob.sisoul.eth",
        forwarder=mock_forwarder,
    )
    set_global_proxy(proxy)
    return proxy


# ── GET /sessions ────────────────────────────────────────────────────────────


class TestGetSessions:
    def test_not_running(self, client):
        r = client.get("/sisoul/proxy/sessions")
        assert r.status_code == 200
        data = r.json()
        assert data["running"] is False
        assert data["sessions"] == []

    def test_running_empty(self, client, bob_proxy):
        r = client.get("/sisoul/proxy/sessions")
        assert r.status_code == 200
        data = r.json()
        assert data["running"] is True
        assert data["self_did"] == "bob.sisoul.eth"
        assert len(data["pubkey_hex"]) == 64
        assert data["sessions"] == []


# ── POST /forward ────────────────────────────────────────────────────────────


class TestPostForward:
    def test_proxy_not_running_409(self, client, alice_keypair):
        alice_priv, alice_pub = alice_keypair
        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": alice_pub.encode().hex(),
            "encrypted_prompt_b64": base64.b64encode(b"x" * 50).decode(),
            "target_model": "claude-opus-4-7",
        }
        r = client.post("/sisoul/proxy/forward", json=body)
        assert r.status_code == 409

    def test_invalid_pubkey_hex(self, client, bob_proxy):
        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": "not-hex!",
            "encrypted_prompt_b64": base64.b64encode(b"x" * 50).decode(),
            "target_model": "claude-opus-4-7",
        }
        r = client.post("/sisoul/proxy/forward", json=body)
        assert r.status_code == 400

    def test_wrong_pubkey_length(self, client, bob_proxy):
        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": "aa" * 16,  # 16B not 32B
            "encrypted_prompt_b64": base64.b64encode(b"x" * 50).decode(),
            "target_model": "claude-opus-4-7",
        }
        r = client.post("/sisoul/proxy/forward", json=body)
        assert r.status_code == 400
        assert "32B" in r.json()["detail"]

    def test_invalid_b64(self, client, bob_proxy, alice_keypair):
        _, alice_pub = alice_keypair
        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": alice_pub.encode().hex(),
            "encrypted_prompt_b64": "!!!not-b64!!!",
            "target_model": "claude-opus-4-7",
        }
        r = client.post("/sisoul/proxy/forward", json=body)
        assert r.status_code == 400

    def test_decrypt_fail_401(self, client, bob_proxy, alice_keypair):
        _, alice_pub = alice_keypair
        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": alice_pub.encode().hex(),
            "encrypted_prompt_b64": base64.b64encode(b"\x00" * 100).decode(),
            "target_model": "claude-opus-4-7",
        }
        r = client.post("/sisoul/proxy/forward", json=body)
        assert r.status_code == 401

    def test_forward_e2e_success(self, client, bob_proxy, alice_keypair):
        alice_priv, alice_pub = alice_keypair
        # Alice 加密 prompt 给 Bob (用 alice 自己 proxy)
        alice = EncryptedProxy(
            self_priv=alice_priv, self_pub=alice_pub,
            self_did="alice.sisoul.eth",
        )
        prompt = "What is 2+2?"
        enc = alice.encrypt_for(bob_proxy.self_pub.encode(), prompt)

        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": alice_pub.encode().hex(),
            "encrypted_prompt_b64": base64.b64encode(enc).decode(),
            "target_model": "claude-opus-4-7",
            "provider": "anthropic",
        }
        r = client.post("/sisoul/proxy/forward", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "encrypted_response_b64" in data
        assert data["metadata"]["status"] == "completed"
        assert data["metadata"]["borrower_did"] == "alice.sisoul.eth"
        assert data["metadata"]["lender_did"] == "bob.sisoul.eth"

        # Alice 解 response
        enc_resp = base64.b64decode(data["encrypted_response_b64"])
        resp = alice.decrypt_from(bob_proxy.self_pub.encode(), enc_resp)
        assert b"REPLY" in resp

    def test_forward_metadata_no_prompt_leak(self, client, bob_proxy, alice_keypair):
        alice_priv, alice_pub = alice_keypair
        alice = EncryptedProxy(
            self_priv=alice_priv, self_pub=alice_pub,
            self_did="alice.sisoul.eth",
        )
        prompt = "ULTRA_SECRET_TOKEN_QWERTYUIOP_9876"
        enc = alice.encrypt_for(bob_proxy.self_pub.encode(), prompt)
        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": alice_pub.encode().hex(),
            "encrypted_prompt_b64": base64.b64encode(enc).decode(),
            "target_model": "claude-opus-4-7",
        }
        r = client.post("/sisoul/proxy/forward", json=body)
        assert r.status_code == 200
        # response body 不该含 prompt 字串 (encrypted_response_b64 是密文, prompt 不可见)
        body_str = r.text
        assert "ULTRA_SECRET_TOKEN" not in body_str


# ── POST /end-session ────────────────────────────────────────────────────────


class TestEndSession:
    def test_not_running_409(self, client):
        r = client.post(
            "/sisoul/proxy/end-session", json={"session_id": "abc"}
        )
        assert r.status_code == 409

    def test_nonexistent_session_returns_ok(self, client, bob_proxy):
        r = client.post(
            "/sisoul/proxy/end-session", json={"session_id": "nonexistent"}
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["metadata"] is None

    def test_end_real_session(self, client, bob_proxy, alice_keypair):
        alice_priv, alice_pub = alice_keypair
        alice = EncryptedProxy(
            self_priv=alice_priv, self_pub=alice_pub,
            self_did="alice.sisoul.eth",
        )
        enc = alice.encrypt_for(bob_proxy.self_pub.encode(), "p")
        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": alice_pub.encode().hex(),
            "encrypted_prompt_b64": base64.b64encode(enc).decode(),
            "target_model": "claude-opus-4-7",
        }
        r1 = client.post("/sisoul/proxy/forward", json=body)
        assert r1.status_code == 200
        sid = r1.json()["metadata"]["session_id"]

        r2 = client.post(
            "/sisoul/proxy/end-session", json={"session_id": sid}
        )
        assert r2.status_code == 200
        assert r2.json()["ok"] is True
        # metadata 字段中 status 是 "completed" (end_session 重置为 completed)
        assert r2.json()["metadata"] is not None
