"""tests/test_llm_openai.py — OpenAIAdapter 单元测试 (mock SDK).

全部测试 mock openai SDK, 不调真 API.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from sisoul.llm.openai import OpenAIAdapter
from sisoul.llm.base import LLMAdapterError


def _make_mock_chat_completion(text: str) -> MagicMock:
    """构造 mock openai ChatCompletion response."""
    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_message = MagicMock()
    mock_message.content = text
    mock_choice.message = mock_message
    mock_response.choices = [mock_choice]
    return mock_response


def _make_mock_stream_chunk(delta_text: str | None) -> MagicMock:
    """构造 mock openai stream chunk."""
    chunk = MagicMock()
    choice = MagicMock()
    delta = MagicMock()
    delta.content = delta_text
    choice.delta = delta
    chunk.choices = [choice]
    return chunk


class TestOpenAIAdapterInit:
    def test_default_model(self):
        adapter = OpenAIAdapter(api_key="test")
        assert adapter.model == "gpt-4o"

    def test_custom_model(self):
        adapter = OpenAIAdapter(api_key="test", model="gpt-4o-mini")
        assert adapter.model == "gpt-4o-mini"

    def test_embed_model_default(self):
        adapter = OpenAIAdapter(api_key="test")
        assert adapter.embed_model == "text-embedding-3-small"

    def test_custom_embed_model(self):
        adapter = OpenAIAdapter(api_key="test", embed_model="text-embedding-3-large")
        assert adapter.embed_model == "text-embedding-3-large"


class TestOpenAIAdapterApiKey:
    def test_raises_if_no_key(self):
        """api_key=None + env 没有 → LLMAdapterError."""
        adapter = OpenAIAdapter(api_key=None)
        env_backup = os.environ.pop("OPENAI_API_KEY", None)
        try:
            with pytest.raises(LLMAdapterError, match="OPENAI_API_KEY"):
                adapter._get_client()
        finally:
            if env_backup:
                os.environ["OPENAI_API_KEY"] = env_backup

    def test_reads_key_from_env(self):
        adapter = OpenAIAdapter(api_key=None)
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-from-env"}):
            with patch.dict("sys.modules", {"openai": mock_openai}):
                adapter._client = None
                client = adapter._get_client()
                assert client is mock_client


class TestOpenAIAdapterChat:
    def _setup_adapter(self, text: str = "response") -> OpenAIAdapter:
        adapter = OpenAIAdapter(api_key="test")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_chat_completion(text)
        adapter._client = mock_client
        return adapter

    def test_chat_returns_text(self):
        adapter = self._setup_adapter("Hello!")
        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert result == "Hello!"

    def test_chat_passes_model(self):
        adapter = OpenAIAdapter(api_key="test", model="gpt-4o-mini")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_mock_chat_completion("ok")
        adapter._client = mock_client

        adapter.chat([{"role": "user", "content": "hi"}])
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "gpt-4o-mini"

    def test_chat_passes_max_tokens(self):
        adapter = self._setup_adapter()
        adapter.chat([{"role": "user", "content": "hi"}], max_tokens=5)
        call_kwargs = adapter._client.chat.completions.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 5

    def test_chat_no_max_tokens_by_default(self):
        """默认不传 max_tokens (OpenAI 自己默认)."""
        adapter = self._setup_adapter()
        adapter.chat([{"role": "user", "content": "hi"}])
        call_kwargs = adapter._client.chat.completions.create.call_args
        assert "max_tokens" not in call_kwargs.kwargs

    def test_chat_passes_temperature(self):
        adapter = self._setup_adapter()
        adapter.chat([{"role": "user", "content": "hi"}], temperature=0.5)
        call_kwargs = adapter._client.chat.completions.create.call_args
        assert call_kwargs.kwargs["temperature"] == 0.5

    def test_chat_passes_system_message_unchanged(self):
        """OpenAI 支持 system role 在 messages 里, 不需要分离."""
        adapter = self._setup_adapter()
        messages = [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "hello"},
        ]
        adapter.chat(messages)
        call_kwargs = adapter._client.chat.completions.create.call_args
        assert call_kwargs.kwargs["messages"] == messages

    def test_chat_error_raises_adapter_error(self):
        adapter = OpenAIAdapter(api_key="test")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("API down")
        adapter._client = mock_client

        with pytest.raises(LLMAdapterError, match="OpenAI"):
            adapter.chat([{"role": "user", "content": "hi"}])


class TestOpenAIAdapterChatStream:
    def test_stream_yields_chunks(self):
        adapter = OpenAIAdapter(api_key="test")
        mock_client = MagicMock()
        chunks = [
            _make_mock_stream_chunk("Hello"),
            _make_mock_stream_chunk(" world"),
            _make_mock_stream_chunk(None),  # None delta (filtered out)
        ]
        mock_client.chat.completions.create.return_value = iter(chunks)
        adapter._client = mock_client

        result = list(adapter.chat_stream([{"role": "user", "content": "hi"}]))
        assert result == ["Hello", " world"]  # None filtered out

    def test_stream_passes_stream_true(self):
        adapter = OpenAIAdapter(api_key="test")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter([])
        adapter._client = mock_client

        list(adapter.chat_stream([{"role": "user", "content": "hi"}]))
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs.kwargs.get("stream") is True


class TestOpenAIAdapterEmbed:
    def test_embed_returns_vector(self):
        adapter = OpenAIAdapter(api_key="test")
        mock_client = MagicMock()
        mock_embed_resp = MagicMock()
        mock_embed_data = MagicMock()
        mock_embed_data.embedding = [0.1, 0.2, 0.3]
        mock_embed_resp.data = [mock_embed_data]
        mock_client.embeddings.create.return_value = mock_embed_resp
        adapter._client = mock_client

        result = adapter.embed("hello world")
        assert result == [0.1, 0.2, 0.3]

    def test_embed_uses_default_model(self):
        adapter = OpenAIAdapter(api_key="test")
        mock_client = MagicMock()
        mock_embed_resp = MagicMock()
        mock_embed_data = MagicMock()
        mock_embed_data.embedding = [0.1]
        mock_embed_resp.data = [mock_embed_data]
        mock_client.embeddings.create.return_value = mock_embed_resp
        adapter._client = mock_client

        adapter.embed("test")
        call_kwargs = mock_client.embeddings.create.call_args
        assert call_kwargs.kwargs["model"] == "text-embedding-3-small"

    def test_embed_error_raises_adapter_error(self):
        adapter = OpenAIAdapter(api_key="test")
        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = RuntimeError("quota exceeded")
        adapter._client = mock_client

        with pytest.raises(LLMAdapterError, match="OpenAI embed"):
            adapter.embed("test")
