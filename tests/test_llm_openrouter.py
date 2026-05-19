"""tests/test_llm_openrouter.py — OpenRouterAdapter 单元测试 (mock SDK).

OpenRouter 用 openai SDK + 改 base_url. mock openai SDK.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from sisoul.llm.openrouter import OpenRouterAdapter, OPENROUTER_BASE_URL
from sisoul.llm.base import LLMAdapterError


def _make_mock_completion(text: str) -> MagicMock:
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_message = MagicMock()
    mock_message.content = text
    mock_choice.message = mock_message
    mock_response.choices = [mock_choice]
    return mock_response


def _make_mock_chunk(delta_text: str | None) -> MagicMock:
    chunk = MagicMock()
    choice = MagicMock()
    delta = MagicMock()
    delta.content = delta_text
    choice.delta = delta
    chunk.choices = [choice]
    return chunk


class TestOpenRouterAdapterInit:
    def test_default_model(self):
        adapter = OpenRouterAdapter(api_key="test")
        assert adapter.model == "openai/gpt-4o"

    def test_custom_model(self):
        adapter = OpenRouterAdapter(api_key="test", model="anthropic/claude-opus-4-7")
        assert adapter.model == "anthropic/claude-opus-4-7"

    def test_api_key_stored(self):
        adapter = OpenRouterAdapter(api_key="sk-or-test")
        assert adapter.api_key == "sk-or-test"

    def test_default_site_name(self):
        adapter = OpenRouterAdapter(api_key="test")
        assert adapter.site_name == "sisoul"

    def test_custom_site_name(self):
        adapter = OpenRouterAdapter(api_key="test", site_name="my-app")
        assert adapter.site_name == "my-app"


class TestOpenRouterAdapterApiKey:
    def test_raises_if_no_key(self):
        adapter = OpenRouterAdapter(api_key=None)
        env_backup = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            with pytest.raises(LLMAdapterError, match="OPENROUTER_API_KEY"):
                adapter._get_client()
        finally:
            if env_backup:
                os.environ["OPENROUTER_API_KEY"] = env_backup

    def test_reads_key_from_env(self):
        adapter = OpenRouterAdapter(api_key=None)
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-from-env"}):
            with patch.dict("sys.modules", {"openai": mock_openai}):
                adapter._client = None
                client = adapter._get_client()
                assert client is mock_client

    def test_client_uses_openrouter_base_url(self):
        adapter = OpenRouterAdapter(api_key="test")
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            adapter._client = None
            adapter._get_client()
            call_kwargs = mock_openai.OpenAI.call_args
            assert call_kwargs.kwargs["base_url"] == OPENROUTER_BASE_URL

    def test_client_has_referer_headers(self):
        adapter = OpenRouterAdapter(api_key="test", site_url="https://myapp.com", site_name="myapp")
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            adapter._client = None
            adapter._get_client()
            call_kwargs = mock_openai.OpenAI.call_args
            headers = call_kwargs.kwargs.get("default_headers", {})
            assert headers.get("HTTP-Referer") == "https://myapp.com"
            assert headers.get("X-Title") == "myapp"


class TestOpenRouterAdapterChat:
    def _setup_adapter(self, text: str = "openrouter response") -> OpenRouterAdapter:
        adapter = OpenRouterAdapter(api_key="test")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion(text)
        adapter._client = mock_client
        return adapter

    def test_chat_returns_text(self):
        adapter = self._setup_adapter("Hello from OpenRouter!")
        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert result == "Hello from OpenRouter!"

    def test_chat_passes_model(self):
        adapter = OpenRouterAdapter(api_key="test", model="meta-llama/llama-3.2-3b-instruct")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_completion("ok")
        adapter._client = mock_client

        adapter.chat([{"role": "user", "content": "hi"}])
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "meta-llama/llama-3.2-3b-instruct"

    def test_chat_passes_max_tokens(self):
        adapter = self._setup_adapter()
        adapter.chat([{"role": "user", "content": "hi"}], max_tokens=20)
        call_kwargs = adapter._client.chat.completions.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 20

    def test_chat_passes_temperature(self):
        adapter = self._setup_adapter()
        adapter.chat([{"role": "user", "content": "hi"}], temperature=0.3)
        call_kwargs = adapter._client.chat.completions.create.call_args
        assert call_kwargs.kwargs["temperature"] == 0.3

    def test_chat_error_raises_adapter_error(self):
        adapter = OpenRouterAdapter(api_key="test")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("OpenRouter down")
        adapter._client = mock_client

        with pytest.raises(LLMAdapterError, match="OpenRouter"):
            adapter.chat([{"role": "user", "content": "hi"}])


class TestOpenRouterAdapterChatStream:
    def test_stream_yields_chunks(self):
        adapter = OpenRouterAdapter(api_key="test")
        mock_client = MagicMock()
        chunks = [
            _make_mock_chunk("Hello"),
            _make_mock_chunk(" there"),
            _make_mock_chunk(None),
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)
        adapter._client = mock_client

        result = list(adapter.chat_stream([{"role": "user", "content": "hi"}]))
        assert result == ["Hello", " there"]

    def test_stream_passes_stream_true(self):
        adapter = OpenRouterAdapter(api_key="test")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter([])
        adapter._client = mock_client

        list(adapter.chat_stream([{"role": "user", "content": "hi"}]))
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("stream") is True


class TestOpenRouterEmbed:
    def test_embed_raises_not_implemented(self):
        adapter = OpenRouterAdapter(api_key="test")
        with pytest.raises(NotImplementedError):
            adapter.embed("text")
