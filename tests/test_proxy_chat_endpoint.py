"""Wave B' P0-1 · POST /sisoul/borrow/proxy-chat endpoint 测试.

覆盖:
- 跑通 7 LLM adapter mock forwarder (claude/openai/gemini/grok/deepseek/ollama/openrouter)
- 反向 case: lender_no_key (LLMAdapterError → 401)
- 反向 case: ForwarderNotInjectedError (env unset → 401)
- 反向 case: 解密失败 → 401
- 反向 case: proxy 未启动 → 409
- 隐私: 响应 body 不含 prompt 字串
- _default_forwarder 用 mock adapter 跑通 wiring
- chat_with_usage fallback (chars/4)
"""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.proxy import borrow_proxy_router
from sisoul.friend.encrypted_proxy import (
    EncryptedProxy,
    derive_friend_session_keypair,
    set_global_proxy,
)
from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key


@pytest.fixture
def app():
    a = FastAPI()
    a.include_router(borrow_proxy_router)
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_proxy_and_env(monkeypatch):
    set_global_proxy(None)
    monkeypatch.delenv("SISOUL_DEFAULT_FORWARDER_REAL", raising=False)
    yield
    set_global_proxy(None)


@pytest.fixture
def alice_keypair():
    master = mnemonic_to_master_key(generate_mnemonic(128))
    return derive_friend_session_keypair(master, 0)


def _build_bob_proxy(forwarder=None):
    master = mnemonic_to_master_key(generate_mnemonic(128))
    priv, pub = derive_friend_session_keypair(master, 0)
    proxy = EncryptedProxy(
        self_priv=priv, self_pub=pub,
        self_did="bob.sisoul.eth",
        llm_api_key="sk-bob-fake-key",
        forwarder=forwarder,
    )
    set_global_proxy(proxy)
    return proxy


def _make_request_body(
    alice_proxy, bob_proxy, prompt: str, provider: str = "anthropic",
    model: str = "claude-opus-4-7"
) -> dict:
    enc = alice_proxy.encrypt_for(bob_proxy.self_pub.encode(), prompt)
    return {
        "borrower_did": alice_proxy.self_did,
        "borrower_pubkey_hex": alice_proxy.self_pub.encode().hex(),
        "encrypted_prompt_b64": base64.b64encode(enc).decode(),
        "target_model": model,
        "provider": provider,
    }


class TestErrorPaths:
    def test_proxy_not_running_409(self, client, alice_keypair):
        _, alice_pub = alice_keypair
        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": alice_pub.encode().hex(),
            "encrypted_prompt_b64": base64.b64encode(b"x" * 50).decode(),
            "target_model": "claude-opus-4-7",
        }
        r = client.post("/sisoul/borrow/proxy-chat", json=body)
        assert r.status_code == 409

    def test_invalid_pubkey_hex(self, client):
        _build_bob_proxy(forwarder=lambda **kw: ("x", 1, 1))
        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": "not-hex!",
            "encrypted_prompt_b64": base64.b64encode(b"x" * 50).decode(),
            "target_model": "claude-opus-4-7",
        }
        r = client.post("/sisoul/borrow/proxy-chat", json=body)
        assert r.status_code == 400

    def test_wrong_pubkey_length(self, client):
        _build_bob_proxy(forwarder=lambda **kw: ("x", 1, 1))
        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": "aa" * 16,
            "encrypted_prompt_b64": base64.b64encode(b"x" * 50).decode(),
            "target_model": "claude-opus-4-7",
        }
        r = client.post("/sisoul/borrow/proxy-chat", json=body)
        assert r.status_code == 400
        assert "32B" in r.json()["detail"]

    def test_invalid_b64(self, client, alice_keypair):
        _build_bob_proxy(forwarder=lambda **kw: ("x", 1, 1))
        _, alice_pub = alice_keypair
        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": alice_pub.encode().hex(),
            "encrypted_prompt_b64": "!!!not-b64!!!",
            "target_model": "claude-opus-4-7",
        }
        r = client.post("/sisoul/borrow/proxy-chat", json=body)
        assert r.status_code == 400

    def test_decrypt_fail_401(self, client, alice_keypair):
        _build_bob_proxy(forwarder=lambda **kw: ("x", 1, 1))
        _, alice_pub = alice_keypair
        body = {
            "borrower_did": "alice.sisoul.eth",
            "borrower_pubkey_hex": alice_pub.encode().hex(),
            "encrypted_prompt_b64": base64.b64encode(b"\x00" * 100).decode(),
            "target_model": "claude-opus-4-7",
        }
        r = client.post("/sisoul/borrow/proxy-chat", json=body)
        assert r.status_code == 401


@pytest.fixture
def alice_proxy(alice_keypair):
    alice_priv, alice_pub = alice_keypair
    return EncryptedProxy(
        self_priv=alice_priv, self_pub=alice_pub,
        self_did="alice.sisoul.eth",
    )


SEVEN_PROVIDERS = [
    ("anthropic", "claude-opus-4-7"),
    ("openai", "gpt-4o"),
    ("gemini", "gemini-2.5-pro"),
    ("grok", "grok-2-latest"),
    ("deepseek", "deepseek-chat"),
    ("ollama", "llama3.2"),
    ("openrouter", "openai/gpt-4o"),
]


class TestSevenProvidersMockForwarder:
    @pytest.mark.parametrize("provider,model", SEVEN_PROVIDERS)
    def test_provider_e2e(self, client, alice_proxy, provider, model):
        def mock_forwarder(prompt, model, provider, api_key=None, **kw):
            return f"REPLY[{provider}/{model}]: {prompt[:20]}", 7, 11

        bob_proxy = _build_bob_proxy(forwarder=mock_forwarder)
        body = _make_request_body(
            alice_proxy, bob_proxy, "tell me a joke", provider=provider, model=model
        )
        r = client.post("/sisoul/borrow/proxy-chat", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["metadata"]["provider"] == provider
        assert data["metadata"]["target_model"] == model
        assert data["metadata"]["status"] == "completed"
        assert data["metadata"]["prompt_token_count"] == 7
        assert data["metadata"]["response_token_count"] == 11

        enc_resp = base64.b64decode(data["encrypted_response_b64"])
        plain = alice_proxy.decrypt_from(bob_proxy.self_pub.encode(), enc_resp)
        assert provider in plain.decode()


class TestLenderNoKeyReverseCase:
    def test_llm_adapter_error_returns_401_lender_no_key(self, client, alice_proxy):
        from sisoul.llm.base import LLMAdapterError

        def no_key_forwarder(prompt, model, provider, api_key=None, **kw):
            raise LLMAdapterError(
                "ANTHROPIC_API_KEY 未设置", provider="anthropic"
            )

        bob_proxy = _build_bob_proxy(forwarder=no_key_forwarder)
        body = _make_request_body(alice_proxy, bob_proxy, "any prompt")
        r = client.post("/sisoul/borrow/proxy-chat", json=body)
        assert r.status_code == 401
        detail = r.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["error"] == "lender_no_key"
        assert "key" in detail["reason"].lower()

    def test_forwarder_not_injected_env_unset_returns_4xx(self, client, alice_proxy, monkeypatch):
        monkeypatch.delenv("SISOUL_DEFAULT_FORWARDER_REAL", raising=False)
        bob_proxy = _build_bob_proxy(forwarder=None)
        body = _make_request_body(alice_proxy, bob_proxy, "ping")
        r = client.post("/sisoul/borrow/proxy-chat", json=body)
        assert r.status_code in (401, 502)

    def test_generic_forwarder_error_returns_502(self, client, alice_proxy):
        def boom_forwarder(prompt, model, provider, api_key=None, **kw):
            raise RuntimeError("backend network down")

        bob_proxy = _build_bob_proxy(forwarder=boom_forwarder)
        body = _make_request_body(alice_proxy, bob_proxy, "ping")
        r = client.post("/sisoul/borrow/proxy-chat", json=body)
        assert r.status_code == 502


class TestPrivacyGuarantees:
    def test_response_body_no_prompt_leak(self, client, alice_proxy):
        secret = "ULTRA_SECRET_TOKEN_X9Y8Z7"

        def echo_fwd(prompt, model, provider, api_key=None, **kw):
            return "OK", 5, 2

        bob_proxy = _build_bob_proxy(forwarder=echo_fwd)
        body = _make_request_body(alice_proxy, bob_proxy, secret)
        r = client.post("/sisoul/borrow/proxy-chat", json=body)
        assert r.status_code == 200
        assert secret not in r.text

    def test_metadata_no_prompt_substring(self, client, alice_proxy):
        secret = "PROMPTGUARD_TOKEN_QWER"

        def fwd(prompt, model, provider, api_key=None, **kw):
            return "fine", 5, 2

        bob_proxy = _build_bob_proxy(forwarder=fwd)
        body = _make_request_body(alice_proxy, bob_proxy, secret)
        r = client.post("/sisoul/borrow/proxy-chat", json=body)
        data = r.json()
        meta_str = str(data["metadata"])
        assert secret not in meta_str


class TestDefaultForwarderRealEnv:
    def test_default_forwarder_uses_real_adapter_with_env(
        self, client, alice_proxy, monkeypatch
    ):
        monkeypatch.setenv("SISOUL_DEFAULT_FORWARDER_REAL", "1")

        class FakeAdapter:
            def chat_with_usage(self, messages, **kw):
                return ("FAKE_LLM_RESPONSE", 42, 13)

        def fake_get_adapter(provider, api_key=None, model=None, **kw):
            return FakeAdapter()

        monkeypatch.setattr("sisoul.llm.get_adapter", fake_get_adapter)
        bob_proxy = _build_bob_proxy(forwarder=None)
        body = _make_request_body(alice_proxy, bob_proxy, "what is the weather?")
        r = client.post("/sisoul/borrow/proxy-chat", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["metadata"]["status"] == "completed"
        assert data["metadata"]["prompt_token_count"] == 42
        assert data["metadata"]["response_token_count"] == 13

        plain = alice_proxy.decrypt_from(
            bob_proxy.self_pub.encode(),
            base64.b64decode(data["encrypted_response_b64"]),
        )
        assert plain == b"FAKE_LLM_RESPONSE"


class TestChatWithUsageDefault:
    def test_default_chat_with_usage_chars_div_4(self):
        from sisoul.llm.base import LLMAdapter

        class StubAdapter(LLMAdapter):
            DEFAULT_MODEL = "stub-1"
            def chat(self, messages, **kw):
                return "12345678"  # 8 chars → 2 tokens
            def chat_stream(self, messages, **kw):
                yield "x"

        a = StubAdapter()
        text, p_tok, r_tok = a.chat_with_usage(
            [{"role": "user", "content": "abcdefghijklmnop"}]  # 16 chars → 4 tokens
        )
        assert text == "12345678"
        assert p_tok == 4
        assert r_tok == 2

    def test_default_chat_with_usage_min_1_token(self):
        from sisoul.llm.base import LLMAdapter

        class StubAdapter(LLMAdapter):
            DEFAULT_MODEL = "stub-1"
            def chat(self, messages, **kw):
                return ""
            def chat_stream(self, messages, **kw):
                yield ""

        a = StubAdapter()
        text, p_tok, r_tok = a.chat_with_usage([{"role": "user", "content": ""}])
        assert p_tok >= 1
        assert r_tok >= 1
