"""tests · llm.deepseek (Phase 2 P2-4).

mock httpx, 不打真 API. 6 case + registry.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from sisoul.llm import PROVIDER_ALIASES, get_adapter
from sisoul.llm.base import LLMAdapterError
from sisoul.llm.deepseek import DeepSeekAdapter


class TestDeepSeekInit:
    def test_default_model(self):
        a = DeepSeekAdapter(api_key="sk-test")
        assert a.model == "deepseek-chat"
        assert a.base_url == "https://api.deepseek.com/v1"

    def test_custom_model(self):
        a = DeepSeekAdapter(api_key="sk-test", model="deepseek-reasoner")
        assert a.model == "deepseek-reasoner"


class TestDeepSeekNoKey:
    def test_no_key_raises(self):
        a = DeepSeekAdapter(api_key=None)
        backup = os.environ.pop("DEEPSEEK_API_KEY", None)
        try:
            with pytest.raises(LLMAdapterError, match="DEEPSEEK_API_KEY"):
                a._get_client()
        finally:
            if backup:
                os.environ["DEEPSEEK_API_KEY"] = backup

    def test_env_key_used(self):
        a = DeepSeekAdapter(api_key=None)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-from-env"}):
            client = a._get_client()
            assert client is not None


class TestDeepSeekChat:
    def _mk_response(self, text: str):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": text}}],
        }
        return resp

    def test_chat_returns_content(self):
        a = DeepSeekAdapter(api_key="sk-test")
        mock_client = MagicMock()
        mock_client.post.return_value = self._mk_response("Hello from DeepSeek!")
        a._client = mock_client
        out = a.chat([{"role": "user", "content": "hi"}])
        assert out == "Hello from DeepSeek!"
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["model"] == "deepseek-chat"

    def test_chat_passes_kwargs(self):
        a = DeepSeekAdapter(api_key="sk-test")
        mock_client = MagicMock()
        mock_client.post.return_value = self._mk_response("ok")
        a._client = mock_client
        a.chat([{"role": "user", "content": "hi"}], max_tokens=99, temperature=0.3)
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 99
        assert payload["temperature"] == 0.3


class TestDeepSeekError:
    def test_error_wrapped(self):
        a = DeepSeekAdapter(api_key="sk-test")
        mock_client = MagicMock()
        mock_client.post.side_effect = RuntimeError("boom")
        a._client = mock_client
        with pytest.raises(LLMAdapterError, match="DeepSeek"):
            a.chat([{"role": "user", "content": "hi"}])


class TestDeepSeekTokens:
    def test_count_tokens_empty(self):
        a = DeepSeekAdapter(api_key="sk-test")
        assert a.count_tokens("") == 0

    def test_count_tokens_positive(self):
        a = DeepSeekAdapter(api_key="sk-test")
        assert a.count_tokens("hello world hello world") > 0


class TestDeepSeekModels:
    def test_list_models(self):
        a = DeepSeekAdapter(api_key="sk-test")
        models = a.list_models()
        assert "deepseek-chat" in models
        assert "deepseek-reasoner" in models


class TestDeepSeekRegistry:
    def test_alias_deepseek(self):
        assert "deepseek" in PROVIDER_ALIASES
        a = get_adapter("deepseek", api_key="sk-test")
        assert isinstance(a, DeepSeekAdapter)

    def test_alias_ds(self):
        a = get_adapter("ds", api_key="sk-test")
        assert isinstance(a, DeepSeekAdapter)
