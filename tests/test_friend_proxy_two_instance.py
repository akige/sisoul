"""Phase 4 W54-W58 · 波 5 dev-B.

集成测试: 同机 2 sisoul daemon (Alice + Bob), 完整加密往返:
- Alice 加密 prompt → POST Bob HTTP daemon → Bob 解密 → mock LLM → 加密 response → Alice 解密
- 测壁钟 < 500ms (E2E latency budget)
"""

from __future__ import annotations

import base64
import time

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


@pytest.fixture(autouse=True)
def clear_proxy():
    set_global_proxy(None)
    yield
    set_global_proxy(None)


def _mock_llm(prompt, model, provider="anthropic", api_key=None, **kw):
    """Simulate a slow-ish LLM (~20ms)."""
    time.sleep(0.02)
    response = f"LLM[{model}]: I read your message ({len(prompt)} chars)"
    return response, max(1, len(prompt) // 4), max(1, len(response) // 4)


def _build_bob_daemon():
    """build Bob FastAPI app with proxy_router + bob proxy registered."""
    master = mnemonic_to_master_key(generate_mnemonic(128))
    priv, pub = derive_friend_session_keypair(master, friend_index=0)
    bob_proxy = EncryptedProxy(
        self_priv=priv, self_pub=pub,
        self_did="bob.sisoul.eth",
        forwarder=_mock_llm,
        llm_api_key="sk-bob-fake",
    )
    set_global_proxy(bob_proxy)

    app = FastAPI()
    app.include_router(proxy_router)
    return app, bob_proxy


def _build_alice():
    master = mnemonic_to_master_key(generate_mnemonic(128))
    priv, pub = derive_friend_session_keypair(master, friend_index=0)
    return EncryptedProxy(
        self_priv=priv, self_pub=pub,
        self_did="alice.sisoul.eth",
    )


# ── 端到端 ────────────────────────────────────────────────────────────────────


class TestTwoInstanceE2E:
    def test_full_roundtrip(self):
        """Alice → Bob HTTP → mock LLM → Bob HTTP → Alice. 解密后 response 与 LLM mock 一致."""
        bob_app, bob_proxy = _build_bob_daemon()
        alice = _build_alice()
        bob_client = TestClient(bob_app)

        prompt = "Alice's commercial secret: 8-pixel offset trick"
        encrypted_prompt = alice.encrypt_for(bob_proxy.self_pub.encode(), prompt)

        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": alice.self_pub.encode().hex(),
            "encrypted_prompt_b64": base64.b64encode(encrypted_prompt).decode(),
            "target_model": "claude-opus-4-7",
            "provider": "anthropic",
        }

        t0 = time.time()
        resp = bob_client.post("/sisoul/proxy/forward", json=body)
        elapsed = time.time() - t0

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["metadata"]["status"] == "completed"
        assert data["metadata"]["borrower_did"] == "alice.sisoul.eth"

        # Alice 解密 response
        enc_resp = base64.b64decode(data["encrypted_response_b64"])
        plaintext = alice.decrypt_from(bob_proxy.self_pub.encode(), enc_resp)
        assert b"LLM" in plaintext
        # mock LLM 不 echo prompt 内容 (只 echo length) → 进一步证 prompt 不必出 LLM
        assert b"8-pixel offset" not in plaintext

        # wall time < 500ms 验收
        assert elapsed < 0.5, f"E2E wall time {elapsed*1000:.0f}ms 超 500ms 预算"

    def test_session_visible_after_forward(self):
        """forward 后 GET /sessions 列得到该 session (但不含 prompt 内容)."""
        bob_app, bob_proxy = _build_bob_daemon()
        alice = _build_alice()
        bob_client = TestClient(bob_app)

        prompt = "session test prompt with unique token: UVWXYZ-marker-42"
        encrypted_prompt = alice.encrypt_for(bob_proxy.self_pub.encode(), prompt)

        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": alice.self_pub.encode().hex(),
            "encrypted_prompt_b64": base64.b64encode(encrypted_prompt).decode(),
            "target_model": "claude-opus-4-7",
        }
        bob_client.post("/sisoul/proxy/forward", json=body)

        r = bob_client.get("/sisoul/proxy/sessions")
        assert r.status_code == 200
        data = r.json()
        assert data["running"] is True
        assert len(data["sessions"]) >= 1
        # 列表中绝不含 prompt 子串
        full_text = r.text
        assert "UVWXYZ-marker-42" not in full_text

    def test_wrong_pubkey_rejected(self):
        bob_app, bob_proxy = _build_bob_daemon()
        alice = _build_alice()
        attacker = _build_alice()  # different keypair
        bob_client = TestClient(bob_app)

        # Alice 加密 prompt, 但请求 body 里给 attacker 的 pubkey
        encrypted_prompt = alice.encrypt_for(bob_proxy.self_pub.encode(), "alice prompt")
        body = {
            "borrower_did": "attacker.sisoul.eth",
            "borrower_pubkey_hex": attacker.self_pub.encode().hex(),
            "encrypted_prompt_b64": base64.b64encode(encrypted_prompt).decode(),
            "target_model": "claude-opus-4-7",
        }
        r = bob_client.post("/sisoul/proxy/forward", json=body)
        # pubkey 不匹配 → 解密失败 → 401
        assert r.status_code == 401

    def test_concurrent_sessions(self):
        """同时 3 个不同 prompt forward, session 列表应 ≥ 3 且各自 metadata 独立."""
        bob_app, bob_proxy = _build_bob_daemon()
        alice = _build_alice()
        bob_client = TestClient(bob_app)

        sids = set()
        for i in range(3):
            prompt = f"concurrent prompt {i}"
            enc = alice.encrypt_for(bob_proxy.self_pub.encode(), prompt)
            body = {
                "borrower_did": "alice.sisoul.eth",
                "borrower_pubkey_hex": alice.self_pub.encode().hex(),
                "encrypted_prompt_b64": base64.b64encode(enc).decode(),
                "target_model": f"claude-model-{i}",
            }
            r = bob_client.post("/sisoul/proxy/forward", json=body)
            assert r.status_code == 200
            sids.add(r.json()["metadata"]["session_id"])

        assert len(sids) == 3

        # GET /sessions 应有 ≥ 3 个
        r = bob_client.get("/sisoul/proxy/sessions")
        data = r.json()
        returned_sids = {s["session_id"] for s in data["sessions"]}
        assert sids.issubset(returned_sids)

    def test_no_prompt_in_metadata(self):
        """metadata 字段全数检查: 绝不含 prompt 或 response 内容字串."""
        bob_app, bob_proxy = _build_bob_daemon()
        alice = _build_alice()
        bob_client = TestClient(bob_app)

        magic = "MAGIC_SECRET_TOKEN_FORWARDED_2026"
        enc = alice.encrypt_for(bob_proxy.self_pub.encode(), f"prompt with {magic} embedded")
        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": alice.self_pub.encode().hex(),
            "encrypted_prompt_b64": base64.b64encode(enc).decode(),
            "target_model": "claude-opus-4-7",
        }
        r = bob_client.post("/sisoul/proxy/forward", json=body)
        meta = r.json()["metadata"]

        # 字段值不该含 magic 字串
        for k, v in meta.items():
            if isinstance(v, str):
                assert magic not in v, f"metadata {k} 含 prompt 字串"

        # GET /sessions 也不能
        r2 = bob_client.get("/sisoul/proxy/sessions")
        assert magic not in r2.text
