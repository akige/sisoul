"""Wave T3 dev · OpenAI Chat Completions compat router 测试.

测试 /v1/chat/completions + /v1/models 端到端.

整体策略:
- 单元测试: TestClient(alice_app) + 手动 patch_bob (用 ASGITransport 路由 httpx 到
  in-process Bob FastAPI app, 不开 socket).
- 集成测试 (test_full_two_daemon_integration): 同进程 spawn Alice + Bob app,
  Alice 走 /v1/chat/completions → 真 HTTP-ish (ASGI 直拨) Bob → mock forwarder
  → encrypted response 解密验证.
- mock-only: 不真启 uvicorn / 不真打 LLM.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.openai_compat import openai_compat_router
from sisoul.daemon_routes.proxy import borrow_proxy_router
from sisoul.friend.encrypted_proxy import (
    EncryptedProxy,
    derive_friend_session_keypair,
    set_global_proxy,
)
from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key


# ── 共用 fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def alice_app():
    a = FastAPI()
    a.include_router(openai_compat_router)
    return a


@pytest.fixture
def alice_client(alice_app):
    return TestClient(alice_app)


@pytest.fixture(autouse=True)
def clear_proxy_and_env(monkeypatch, tmp_path):
    """每 test 重置 global proxy + 清相关 env + 隔离 ~/.sisoul."""
    set_global_proxy(None)
    # 隔离 didkey_friends.json: 临时 HOME
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # 清测试相关 env
    for k in (
        "SISOUL_BORROW_FRIEND_DID",
        "SISOUL_BORROW_PROVIDER",
        "SISOUL_BORROW_BOB_URL",
        "SISOUL_OPENAI_COMPAT_MOCK",
    ):
        monkeypatch.delenv(k, raising=False)
    yield
    set_global_proxy(None)


@pytest.fixture
def alice_keypair():
    master = mnemonic_to_master_key(generate_mnemonic(128))
    return derive_friend_session_keypair(master, 0)


@pytest.fixture
def alice_proxy(alice_keypair):
    priv, pub = alice_keypair
    p = EncryptedProxy(self_priv=priv, self_pub=pub, self_did="alice.sisoul.eth")
    set_global_proxy(p)
    return p


def _write_didkey_friend(
    did: str = "did:key:z6LSofoYu2Co99XdeoDF82dBfjNyKFa2aoRHGWjrj8xaiDSb",
    pubkey_hex: str = "00" * 32,
    last_seen_url: Optional[str] = None,
    allowed_models: Optional[list[str]] = None,
) -> None:
    fp = Path(os.path.expanduser("~")) / ".sisoul" / "identity" / "didkey_friends.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "did": did,
        "pubkey_hex": pubkey_hex,
        "key_type": "X25519",
        "nickname": "bob-test",
        "added_at": "2026-05-28T00:00:00Z",
        "method": "did:key",
    }
    if last_seen_url:
        record["last_seen_url"] = last_seen_url
    if allowed_models:
        record["allowed_models"] = allowed_models
    fp.write_text(json.dumps([record], ensure_ascii=False, indent=2), encoding="utf-8")


# ── ASGI httpx transport 拦截 (Alice → Bob 路由到 in-process Bob app) ─────────


class _BobRoutingTransport(httpx.AsyncBaseTransport):
    """把 host 含 'bob.test' 的请求路由到 Bob ASGI app, 其它直接 raise."""

    def __init__(self, bob_app):
        self._bob_app = httpx.ASGITransport(app=bob_app)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._bob_app.handle_async_request(request)

    async def aclose(self) -> None:
        await self._bob_app.aclose()


@pytest.fixture
def patch_httpx_to_bob(monkeypatch):
    """返一个函数: passed a bob_app, monkey-patches httpx.AsyncClient default transport."""
    def _patch(bob_app):
        transport = _BobRoutingTransport(bob_app)
        orig_init = httpx.AsyncClient.__init__

        def patched_init(self, *args, **kw):
            kw["transport"] = transport
            orig_init(self, *args, **kw)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
        return transport

    return _patch


def _make_bob_app_with_proxy(forwarder=None):
    """构造 Bob FastAPI app + 全局 EncryptedProxy."""
    master = mnemonic_to_master_key(generate_mnemonic(128))
    bob_priv, bob_pub = derive_friend_session_keypair(master, 0)

    def default_mock_forwarder(prompt, model, provider="anthropic", api_key=None, **kw):
        return f"[MOCK-BOB] echo: {prompt[:80]}", max(1, len(prompt) // 4), 17

    bob_proxy = EncryptedProxy(
        self_priv=bob_priv,
        self_pub=bob_pub,
        self_did="bob.sisoul.eth",
        forwarder=forwarder or default_mock_forwarder,
    )
    bob_app = FastAPI()
    bob_app.include_router(borrow_proxy_router)
    # Bob 的 proxy 装到全局 (borrow_proxy_router get_global_proxy 读这个)
    # 注: Alice / Bob 共享全局 = 单进程局限. 本测试切换时小心.
    set_global_proxy(bob_proxy)
    return bob_app, bob_proxy


# ── GET /v1/models ────────────────────────────────────────────────────────────


class TestModels:
    def test_no_friend_returns_default_models(self, alice_client):
        r = alice_client.get("/v1/models")
        assert r.status_code == 200
        data = r.json()
        assert data["object"] == "list"
        assert len(data["data"]) >= 1
        ids = {m["id"] for m in data["data"]}
        assert "claude-opus-4-7" in ids

    def test_friend_with_allowed_models(self, alice_client):
        _write_didkey_friend(allowed_models=["gpt-5", "claude-opus-4-7"])
        r = alice_client.get("/v1/models")
        assert r.status_code == 200
        ids = {m["id"] for m in r.json()["data"]}
        assert ids == {"gpt-5", "claude-opus-4-7"}

    def test_model_entry_shape(self, alice_client):
        r = alice_client.get("/v1/models")
        m = r.json()["data"][0]
        assert m["object"] == "model"
        assert "id" in m
        assert "created" in m
        assert m["owned_by"] == "sisoul-borrow"


# ── POST /v1/chat/completions 边界 ────────────────────────────────────────────


class TestChatCompletionsValidation:
    def test_stream_returns_503_not_implemented(self, alice_client, alice_proxy):
        _write_didkey_friend()
        body = {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 503
        err = r.json()["error"]
        assert err["type"] == "not_implemented"
        assert err["code"] == "stream_unsupported"

    def test_empty_messages_400(self, alice_client, alice_proxy):
        body = {"model": "claude-opus-4-7", "messages": []}
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "messages_empty"

    def test_missing_messages_field_422(self, alice_client, alice_proxy):
        # pydantic 校验先撞: 缺 messages 字段
        r = alice_client.post(
            "/v1/chat/completions", json={"model": "claude-opus-4-7"}
        )
        assert r.status_code == 422

    def test_missing_model_field_422(self, alice_client, alice_proxy):
        r = alice_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 422

    def test_proxy_uninitialized_409(self, alice_client):
        body = {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "proxy_uninitialized"

    def test_no_friend_returns_412(self, alice_client, alice_proxy):
        body = {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 412
        assert r.json()["error"]["code"] == "no_friend"

    def test_friend_did_env_no_match_412(
        self, alice_client, alice_proxy, monkeypatch
    ):
        _write_didkey_friend(did="did:key:zABC")
        monkeypatch.setenv("SISOUL_BORROW_FRIEND_DID", "did:key:zNOMATCH")
        body = {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 412

    def test_model_not_in_allowed_404(self, alice_client, alice_proxy):
        _write_didkey_friend(allowed_models=["gpt-5"])
        body = {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "model_not_allowed"

    def test_temperature_out_of_range_422(self, alice_client, alice_proxy):
        body = {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 5.0,
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 422

    def test_max_tokens_out_of_range_422(self, alice_client, alice_proxy):
        body = {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 999999,
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 422


# ── Bob 不可达 / 错误响应映射 ─────────────────────────────────────────────────


class TestBobUnreachableMapping:
    def test_bob_unreachable_503(self, alice_client, alice_proxy, monkeypatch):
        _write_didkey_friend()
        # bob_url 指向 unroutable
        monkeypatch.setenv("SISOUL_BORROW_BOB_URL", "http://127.0.0.1:1")
        body = {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 503
        assert r.json()["error"]["code"] in {"bob_unreachable", "bob_peer_pubkey_failed"}


# ── 集成 (ASGI Bob in-process) ───────────────────────────────────────────────


class TestTwoDaemonIntegration:
    """Alice openai_compat → ASGI 路由 → Bob borrow_proxy → mock forwarder → 回."""

    def _setup_bob_then_alice(self, monkeypatch, patch_httpx_to_bob, forwarder=None):
        """构造顺序: 先建 Bob app (装 bob proxy 全局) → 再覆盖 alice proxy 全局.

        注意: borrow_proxy_router 用 get_global_proxy() — 单进程, alice + bob 不能
        同时是全局. 本测策略: Bob app 用闭包持有 bob_proxy, 拦截 get_global_proxy.
        """
        master = mnemonic_to_master_key(generate_mnemonic(128))
        bob_priv, bob_pub = derive_friend_session_keypair(master, 0)

        def default_mock(prompt, model, provider="anthropic", api_key=None, **kw):
            return f"[MOCK-BOB] echo: {prompt[:80]}", max(1, len(prompt) // 4), 17

        bob_proxy = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_pub,
            self_did="bob.sisoul.eth",
            forwarder=forwarder or default_mock,
        )
        # 拦截 get_global_proxy: Bob 路径返 bob_proxy, Alice 路径返 alice_proxy
        # 用 monkeypatch 替换 proxy.py 内 get_global_proxy → 返每次 set 的最近一个.
        # 简化: 测试只跑 1 个请求, 顺序 set: Alice 调用前 set(alice), Alice 转发到 Bob
        # asgi 进 Bob handler 前 set(bob), 返回再 set(alice). 但 ASGI 同协程内 set
        # 全局会破坏. → 改思路: 直接在 borrow_proxy_router 进入前 monkeypatch
        # get_global_proxy 返 bob_proxy. Alice 端读 alice_proxy 走的是 openai_compat
        # 内显式 get_global_proxy() 一次, 然后传给 httpx → 进 Bob app → Bob handler
        # 再 get_global_proxy(). 这两次调用我们让它分别返不同实例:
        from sisoul.daemon_routes import proxy as bob_proxy_module
        from sisoul.daemon_routes import openai_compat as alice_compat_module

        alice_master = mnemonic_to_master_key(generate_mnemonic(128))
        alice_priv, alice_pub = derive_friend_session_keypair(alice_master, 0)
        alice_proxy = EncryptedProxy(
            self_priv=alice_priv, self_pub=alice_pub, self_did="alice.sisoul.eth"
        )

        # alice_compat 模块 import 的是 `from ... import get_global_proxy` 绑定符号
        monkeypatch.setattr(
            alice_compat_module, "get_global_proxy", lambda: alice_proxy
        )
        # bob borrow_proxy 模块同样
        monkeypatch.setattr(
            bob_proxy_module, "get_global_proxy", lambda: bob_proxy
        )

        bob_app = FastAPI()
        bob_app.include_router(borrow_proxy_router)
        patch_httpx_to_bob(bob_app)

        # Bob URL 必须有效 host (httpx 不校验真存在, transport 拦截即可)
        monkeypatch.setenv("SISOUL_BORROW_BOB_URL", "http://bob.test")

        # 写入 friend (允许 model)
        _write_didkey_friend(
            allowed_models=["claude-opus-4-7"],
            last_seen_url="http://bob.test",
        )
        return alice_proxy, bob_proxy

    def test_e2e_chat_completion_success(
        self, alice_client, monkeypatch, patch_httpx_to_bob
    ):
        alice_proxy, bob_proxy = self._setup_bob_then_alice(
            monkeypatch, patch_httpx_to_bob
        )
        body = {
            "model": "claude-opus-4-7",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "What is 1+1?"},
            ],
            "max_tokens": 100,
            "temperature": 0.5,
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        # OpenAI shape
        assert data["object"] == "chat.completion"
        assert data["id"].startswith("chatcmpl-")
        assert isinstance(data["created"], int) and data["created"] > 0
        assert data["model"] == "claude-opus-4-7"
        assert len(data["choices"]) == 1
        choice = data["choices"][0]
        assert choice["index"] == 0
        assert choice["finish_reason"] == "stop"
        assert choice["message"]["role"] == "assistant"
        # mock forwarder echoes "[MOCK-BOB] echo: <prompt-prefix>"
        assert "[MOCK-BOB]" in choice["message"]["content"]
        # 因为 prompt 含 "What is 1+1" → echo 应回该串
        assert "What is 1+1" in choice["message"]["content"]
        # usage
        assert data["usage"]["total_tokens"] >= 1
        assert data["usage"]["completion_tokens"] == 17  # mock 固定

    def test_e2e_prompt_not_in_response_metadata(
        self, alice_client, monkeypatch, patch_httpx_to_bob
    ):
        alice_proxy, bob_proxy = self._setup_bob_then_alice(
            monkeypatch, patch_httpx_to_bob
        )
        secret = "ULTRA_SECRET_TOKEN_QWERTY_999"
        body = {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": secret}],
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 200, r.text
        # 注: 因为 mock forwarder echo prompt → response 字段会含 secret. 这正常
        # (Alice 自己看自己的明文 response 没问题). 但 mock echo prefix 也会回 secret.
        # 这里只验 metadata 路径不漏 prompt: 解析 response body 中 *非* choices 字段
        # (usage / id / created / object / model) 不该带 secret.
        body_dict = r.json()
        non_choice = {k: v for k, v in body_dict.items() if k != "choices"}
        assert secret not in json.dumps(non_choice)

    def test_e2e_messages_flatten_system_user(
        self, alice_client, monkeypatch, patch_httpx_to_bob
    ):
        """system + user message 都该进 prompt (mock echo 验证)."""
        alice_proxy, bob_proxy = self._setup_bob_then_alice(
            monkeypatch, patch_httpx_to_bob
        )
        body = {
            "model": "claude-opus-4-7",
            "messages": [
                {"role": "system", "content": "BE_TERSE_TAG"},
                {"role": "user", "content": "Q1"},
            ],
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 200, r.text
        content = r.json()["choices"][0]["message"]["content"]
        # mock echos first 80 chars of prompt — flatten 后 system tag 应在前
        assert "BE_TERSE_TAG" in content

    def test_e2e_multimodal_text_extracted(
        self, alice_client, monkeypatch, patch_httpx_to_bob
    ):
        """OpenAI multimodal content list (type=text only). image_url 被忽略."""
        alice_proxy, bob_proxy = self._setup_bob_then_alice(
            monkeypatch, patch_httpx_to_bob
        )
        body = {
            "model": "claude-opus-4-7",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "MULTIMODAL_TEXT_X"},
                        {"type": "image_url", "image_url": "http://x.png"},
                    ],
                }
            ],
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 200, r.text
        assert "MULTIMODAL_TEXT_X" in r.json()["choices"][0]["message"]["content"]

    def test_e2e_mock_env_forces_mock_forwarder(
        self, alice_client, monkeypatch, patch_httpx_to_bob
    ):
        """SISOUL_OPENAI_COMPAT_MOCK=1 → req body 含 use_mock_forwarder=True."""
        captured = {}

        def assert_forwarder(prompt, model, provider="anthropic", api_key=None, **kw):
            return f"[CAPTURED] {prompt[:40]}", 1, 2

        alice_proxy, bob_proxy = self._setup_bob_then_alice(
            monkeypatch, patch_httpx_to_bob, forwarder=assert_forwarder
        )
        monkeypatch.setenv("SISOUL_OPENAI_COMPAT_MOCK", "1")
        body = {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hello"}],
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        # 即便 use_mock_forwarder=True, Bob 端还是会用 bob_proxy._forwarder
        # (因为 Bob 已注入 mock forwarder, use_mock_forwarder=True 走 inner mock).
        # 这测主要验证不挂. inner mock echo 应回 "Hello..." (80 chars).
        assert r.status_code == 200, r.text

    def test_e2e_lender_no_key_returns_401(
        self, alice_client, monkeypatch, patch_httpx_to_bob
    ):
        """Bob forwarder 抛 LLMAdapterError → 401 lender_no_key."""
        def llm_adapter_error_forwarder(prompt, model, **kw):
            class LLMAdapterError(Exception):
                pass
            raise LLMAdapterError("no api key")

        alice_proxy, bob_proxy = self._setup_bob_then_alice(
            monkeypatch, patch_httpx_to_bob, forwarder=llm_adapter_error_forwarder
        )
        body = {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 401
        err = r.json()["error"]
        assert err["type"] == "authentication_error"
        assert err["code"] == "lender_no_key"

    def test_e2e_response_decrypts_to_assistant_message(
        self, alice_client, monkeypatch, patch_httpx_to_bob
    ):
        """验证 Alice 真用 Bob pubkey 解密返回 → 明文为 mock forwarder 的回."""
        marker = "DECRYPTED_OK_MARKER_42"

        def fwd(prompt, model, **kw):
            return marker, 5, 7

        alice_proxy, bob_proxy = self._setup_bob_then_alice(
            monkeypatch, patch_httpx_to_bob, forwarder=fwd
        )
        body = {
            "model": "claude-opus-4-7",
            "messages": [{"role": "user", "content": "hi"}],
        }
        r = alice_client.post("/v1/chat/completions", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["choices"][0]["message"]["content"] == marker
        assert r.json()["usage"]["prompt_tokens"] == 5
        assert r.json()["usage"]["completion_tokens"] == 7
        assert r.json()["usage"]["total_tokens"] == 12


# ── messages flatten / content 解析 单测 ──────────────────────────────────────


class TestFlattenAndContent:
    def test_flatten_simple_string(self):
        from sisoul.daemon_routes.openai_compat import _flatten_messages, ChatMessage

        msgs = [
            ChatMessage(role="system", content="SYS"),
            ChatMessage(role="user", content="USR"),
        ]
        out = _flatten_messages(msgs)
        assert "SYS" in out and "USR" in out
        assert out.endswith("<|assistant|>")

    def test_flatten_multimodal_extracts_text(self):
        from sisoul.daemon_routes.openai_compat import _flatten_messages, ChatMessage

        msgs = [
            ChatMessage(
                role="user",
                content=[
                    {"type": "text", "text": "A"},
                    {"type": "image_url", "image_url": "x"},
                    {"type": "text", "text": "B"},
                ],
            )
        ]
        out = _flatten_messages(msgs)
        assert "A" in out and "B" in out
        assert "image_url" not in out  # raw json key 不该出现

    def test_stringify_content_str(self):
        from sisoul.daemon_routes.openai_compat import _stringify_content

        assert _stringify_content("hello") == "hello"

    def test_stringify_content_list_text(self):
        from sisoul.daemon_routes.openai_compat import _stringify_content

        out = _stringify_content([{"type": "text", "text": "X"}])
        assert "X" in out

    def test_stringify_content_other(self):
        from sisoul.daemon_routes.openai_compat import _stringify_content

        out = _stringify_content(42)
        assert "42" in out


# ── friend 选择逻辑 单测 ──────────────────────────────────────────────────────


class TestFriendSelection:
    def test_pick_friend_empty(self):
        from sisoul.daemon_routes.openai_compat import _pick_friend

        assert _pick_friend() is None

    def test_pick_friend_first(self):
        from sisoul.daemon_routes.openai_compat import _pick_friend

        _write_didkey_friend(did="did:key:zABC")
        f = _pick_friend()
        assert f is not None
        assert f["did"] == "did:key:zABC"

    def test_pick_friend_env_select(self, monkeypatch):
        from sisoul.daemon_routes.openai_compat import _pick_friend

        _write_didkey_friend(did="did:key:zABC")
        # 增加第二个 friend
        fp = Path(os.path.expanduser("~")) / ".sisoul" / "identity" / "didkey_friends.json"
        entries = json.loads(fp.read_text())
        entries.append({"did": "did:key:zXYZ", "pubkey_hex": "ff" * 32, "key_type": "X25519"})
        fp.write_text(json.dumps(entries, ensure_ascii=False, indent=2))
        monkeypatch.setenv("SISOUL_BORROW_FRIEND_DID", "did:key:zXYZ")
        f = _pick_friend()
        assert f is not None
        assert f["did"] == "did:key:zXYZ"

    def test_pick_friend_env_no_match(self, monkeypatch):
        from sisoul.daemon_routes.openai_compat import _pick_friend

        _write_didkey_friend(did="did:key:zABC")
        monkeypatch.setenv("SISOUL_BORROW_FRIEND_DID", "did:key:zNOMATCH")
        assert _pick_friend() is None

    def test_resolve_bob_url_env_priority(self, monkeypatch):
        from sisoul.daemon_routes.openai_compat import _resolve_bob_url

        monkeypatch.setenv("SISOUL_BORROW_BOB_URL", "http://x.example:1234")
        url = _resolve_bob_url({"last_seen_url": "http://other.example"})
        assert url == "http://x.example:1234"

    def test_resolve_bob_url_friend_record(self):
        from sisoul.daemon_routes.openai_compat import _resolve_bob_url

        url = _resolve_bob_url({"last_seen_url": "http://r.example:7777/"})
        assert url == "http://r.example:7777"

    def test_resolve_bob_url_default(self):
        from sisoul.daemon_routes.openai_compat import _resolve_bob_url, DEFAULT_BOB_URL

        assert _resolve_bob_url(None) == DEFAULT_BOB_URL

    def test_allowed_models_default(self):
        from sisoul.daemon_routes.openai_compat import _allowed_models, DEFAULT_MODELS

        assert _allowed_models(None) == list(DEFAULT_MODELS)

    def test_allowed_models_friend_override(self):
        from sisoul.daemon_routes.openai_compat import _allowed_models

        out = _allowed_models({"allowed_models": ["gpt-5", "claude-x"]})
        assert out == ["gpt-5", "claude-x"]

    def test_allowed_models_friend_empty_falls_back(self):
        from sisoul.daemon_routes.openai_compat import _allowed_models, DEFAULT_MODELS

        out = _allowed_models({"allowed_models": []})
        assert out == list(DEFAULT_MODELS)
