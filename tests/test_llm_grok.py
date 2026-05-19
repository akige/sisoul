"""tests · llm.grok (Phase 2 P2-4).

mock httpx, 不打真 API. 覆盖 6 case:
1. init default model
2. no key raises
3. mock chat call
4. error handling
5. count_tokens
6. list_models
+ alias registry
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from sisoul.llm import PROVIDER_ALIASES, get_adapter
from sisoul.llm.base import LLMAdapterError
from sisoul.llm.grok import GrokAdapter


class TestGrokInit:
    def test_default_model(self):
        a = GrokAdapter(api_key="xai-test")
        assert a.model == "grok-2-latest"
        assert a.base_url == "https://api.x.ai/v1"

    def test_custom_model(self):
        a = GrokAdapter(api_key="xai-test", model="grok-2-mini")
        assert a.model == "grok-2-mini"

    def test_custom_base_url(self):
        a = GrokAdapter(api_key="xai-test", base_url="https://proxy.example/v1/")
        assert a.base_url == "https://proxy.example/v1"


class TestGrokNoKey:
    def test_no_key_raises(self):
        a = GrokAdapter(api_key=None)
        backup = os.environ.pop("XAI_API_KEY", None)
        try:
            with pytest.raises(LLMAdapterError, match="XAI_API_KEY"):
                a._get_client()
        finally:
            if backup:
                os.environ["XAI_API_KEY"] = backup

    def test_env_key_used(self):
        a = GrokAdapter(api_key=None)
        with patch.dict(os.environ, {"XAI_API_KEY": "xai-from-env"}):
            # 拿 client 不抛错 → 说明读到了 env
            client = a._get_client()
            assert client is not None


class TestGrokChat:
    def _mk_response(self, text: str):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": text}}],
        }
        return resp

    def test_chat_returns_content(self):
        a = GrokAdapter(api_key="xai-test")
        mock_client = MagicMock()
        mock_client.post.return_value = self._mk_response("Hello from Grok!")
        a._client = mock_client

        out = a.chat([{"role": "user", "content": "hi"}])
        assert out == "Hello from Grok!"
        mock_client.post.assert_called_once()
        # payload 必须含 model + messages
        kwargs = mock_client.post.call_args.kwargs
        assert kwargs["json"]["model"] == "grok-2-latest"
        assert kwargs["json"]["messages"][0]["content"] == "hi"

    def test_chat_passes_max_tokens(self):
        a = GrokAdapter(api_key="xai-test")
        mock_client = MagicMock()
        mock_client.post.return_value = self._mk_response("ok")
        a._client = mock_client
        a.chat([{"role": "user", "content": "hi"}], max_tokens=42, temperature=0.7)
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 42
        assert payload["temperature"] == 0.7


class TestGrokError:
    def test_http_error_wrapped(self):
        a = GrokAdapter(api_key="xai-test")
        mock_client = MagicMock()
        mock_client.post.side_effect = RuntimeError("network down")
        a._client = mock_client
        with pytest.raises(LLMAdapterError, match="Grok"):
            a.chat([{"role": "user", "content": "hi"}])


class TestGrokTokens:
    def test_count_tokens_empty(self):
        a = GrokAdapter(api_key="xai-test")
        assert a.count_tokens("") == 0

    def test_count_tokens_heuristic(self):
        a = GrokAdapter(api_key="xai-test")
        n = a.count_tokens("hello world " * 100)
        assert n > 0


class TestGrokModels:
    def test_list_models(self):
        a = GrokAdapter(api_key="xai-test")
        models = a.list_models()
        assert "grok-2-latest" in models
        assert isinstance(models, list)


class TestGrokRegistry:
    def test_alias_grok(self):
        assert "grok" in PROVIDER_ALIASES
        a = get_adapter("grok", api_key="xai-test")
        assert isinstance(a, GrokAdapter)

    def test_alias_xai(self):
        a = get_adapter("xai", api_key="xai-test")
        assert isinstance(a, GrokAdapter)
