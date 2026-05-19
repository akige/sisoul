"""tests/test_llm_ollama.py — OllamaAdapter 单元测试 (mock SDK).

全部测试 mock ollama Python client, 不调真本地 daemon.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sisoul.llm.ollama import OllamaAdapter
from sisoul.llm.base import LLMAdapterError


class TestOllamaAdapterInit:
    def test_default_model(self):
        adapter = OllamaAdapter()
        assert adapter.model == "llama3.2"

    def test_default_base_url(self):
        adapter = OllamaAdapter()
        assert adapter.base_url == "http://localhost:11434"

    def test_custom_model(self):
        adapter = OllamaAdapter(model="mistral")
        assert adapter.model == "mistral"

    def test_custom_base_url(self):
        adapter = OllamaAdapter(base_url="http://10.0.0.1:11434")
        assert adapter.base_url == "http://10.0.0.1:11434"

    def test_api_key_none(self):
        """ollama 无 api_key."""
        adapter = OllamaAdapter(api_key="ignored")
        assert adapter.api_key is None  # 强制 None

    def test_client_initially_none(self):
        adapter = OllamaAdapter()
        assert adapter._client is None


class TestOllamaGetClient:
    def test_get_client_returns_ollama_client(self):
        adapter = OllamaAdapter()
        mock_ollama = MagicMock()
        mock_client = MagicMock()
        mock_ollama.Client.return_value = mock_client

        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            adapter._client = None
            client = adapter._get_client()
            assert client is mock_client
            mock_ollama.Client.assert_called_once_with(host="http://localhost:11434")

    def test_get_client_uses_base_url(self):
        adapter = OllamaAdapter(base_url="http://custom:11434")
        mock_ollama = MagicMock()
        mock_client = MagicMock()
        mock_ollama.Client.return_value = mock_client

        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            adapter._client = None
            adapter._get_client()
            mock_ollama.Client.assert_called_once_with(host="http://custom:11434")

    def test_import_error_raises_adapter_error(self):
        adapter = OllamaAdapter()
        adapter._client = None

        import builtins
        original = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "ollama":
                raise ImportError("No module named 'ollama'")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(LLMAdapterError, match="ollama SDK 未安装"):
                adapter._get_client()


class TestOllamaAdapterChat:
    def _setup_adapter(self, response_text: str = "llama response") -> OllamaAdapter:
        adapter = OllamaAdapter()
        mock_client = MagicMock()
        mock_client.chat.return_value = {"message": {"role": "assistant", "content": response_text}}
        adapter._client = mock_client
        return adapter

    def test_chat_returns_text(self):
        adapter = self._setup_adapter("Hello from Ollama!")
        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert result == "Hello from Ollama!"

    def test_chat_passes_model(self):
        adapter = self._setup_adapter()
        adapter.chat([{"role": "user", "content": "hi"}])
        call_kwargs = adapter._client.chat.call_args
        assert call_kwargs.kwargs["model"] == "llama3.2"

    def test_chat_passes_custom_model(self):
        adapter = OllamaAdapter(model="mistral")
        mock_client = MagicMock()
        mock_client.chat.return_value = {"message": {"role": "assistant", "content": "ok"}}
        adapter._client = mock_client

        adapter.chat([{"role": "user", "content": "hi"}])
        call_kwargs = mock_client.chat.call_args
        assert call_kwargs.kwargs["model"] == "mistral"

    def test_chat_passes_messages(self):
        adapter = self._setup_adapter()
        messages = [{"role": "user", "content": "test message"}]
        adapter.chat(messages)
        call_kwargs = adapter._client.chat.call_args
        assert call_kwargs.kwargs["messages"] == messages

    def test_chat_passes_temperature_as_options(self):
        adapter = self._setup_adapter()
        adapter.chat([{"role": "user", "content": "hi"}], temperature=0.5)
        call_kwargs = adapter._client.chat.call_args
        assert call_kwargs.kwargs.get("options", {}).get("temperature") == 0.5

    def test_chat_maps_max_tokens_to_num_predict(self):
        adapter = self._setup_adapter()
        adapter.chat([{"role": "user", "content": "hi"}], max_tokens=100)
        call_kwargs = adapter._client.chat.call_args
        assert call_kwargs.kwargs.get("options", {}).get("num_predict") == 100

    def test_chat_no_options_when_no_kwargs(self):
        """无额外 kwargs → 不传 options (ollama 默认)."""
        adapter = self._setup_adapter()
        adapter.chat([{"role": "user", "content": "hi"}])
        call_kwargs = adapter._client.chat.call_args
        assert "options" not in call_kwargs.kwargs

    def test_chat_connection_error_hints_serve(self):
        """连接被拒 → 错误提示 'ollama serve'."""
        adapter = OllamaAdapter()
        mock_client = MagicMock()
        mock_client.chat.side_effect = ConnectionRefusedError("Connection refused")
        adapter._client = mock_client

        with pytest.raises(LLMAdapterError, match="ollama serve"):
            adapter.chat([{"role": "user", "content": "hi"}])

    def test_chat_generic_error_raises_adapter_error(self):
        adapter = OllamaAdapter()
        mock_client = MagicMock()
        mock_client.chat.side_effect = RuntimeError("model not found")
        adapter._client = mock_client

        with pytest.raises(LLMAdapterError):
            adapter.chat([{"role": "user", "content": "hi"}])


class TestOllamaAdapterChatStream:
    def test_stream_yields_chunks(self):
        adapter = OllamaAdapter()
        mock_client = MagicMock()
        chunks = [
            {"message": {"content": "Hello"}},
            {"message": {"content": " world"}},
            {"message": {"content": ""}},  # empty → filtered
        ]
        mock_client.chat.return_value = iter(chunks)
        adapter._client = mock_client

        result = list(adapter.chat_stream([{"role": "user", "content": "hi"}]))
        assert result == ["Hello", " world"]

    def test_stream_passes_stream_true(self):
        adapter = OllamaAdapter()
        mock_client = MagicMock()
        mock_client.chat.return_value = iter([])
        adapter._client = mock_client

        list(adapter.chat_stream([{"role": "user", "content": "hi"}]))
        call_kwargs = mock_client.chat.call_args
        assert call_kwargs.kwargs.get("stream") is True


class TestOllamaEmbed:
    def test_embed_raises_not_implemented(self):
        """Ollama adapter 不实现 embed (仅本地 LLM, 不做 embed)."""
        adapter = OllamaAdapter()
        with pytest.raises(NotImplementedError):
            adapter.embed("text")
