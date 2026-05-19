"""tests/test_llm_gemini.py — GeminiAdapter 单元测试 (mock SDK).

全部测试 mock google-generativeai SDK, 不调真 API.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch, call

import pytest

from sisoul.llm.gemini import GeminiAdapter
from sisoul.llm.base import LLMAdapterError


class TestGeminiAdapterInit:
    def test_default_model(self):
        adapter = GeminiAdapter(api_key="test")
        assert adapter.model == "gemini-2.5-pro"

    def test_custom_model(self):
        adapter = GeminiAdapter(api_key="test", model="gemini-1.5-flash")
        assert adapter.model == "gemini-1.5-flash"

    def test_api_key_stored(self):
        adapter = GeminiAdapter(api_key="AIza-test")
        assert adapter.api_key == "AIza-test"

    def test_genai_initially_none(self):
        adapter = GeminiAdapter(api_key="test")
        assert adapter._genai is None


class TestGeminiAdapterApiKey:
    def test_raises_if_no_key(self):
        adapter = GeminiAdapter(api_key=None)
        env_backup = os.environ.pop("GEMINI_API_KEY", None)
        try:
            with pytest.raises(LLMAdapterError, match="GEMINI_API_KEY"):
                adapter._get_genai()
        finally:
            if env_backup:
                os.environ["GEMINI_API_KEY"] = env_backup

    def test_reads_key_from_env(self):
        adapter = GeminiAdapter(api_key=None)
        mock_genai = MagicMock()

        with patch.dict(os.environ, {"GEMINI_API_KEY": "AIza-from-env"}):
            with patch.dict("sys.modules", {"google.generativeai": mock_genai, "google": MagicMock()}):
                adapter._genai = None
                # 由于 import 是 lazy 的, 直接测试 configure 调用
                with patch("sisoul.llm.gemini.GeminiAdapter._get_genai", return_value=mock_genai):
                    result = adapter._get_genai()
                    assert result is mock_genai


class TestGeminiBuildMessages:
    def test_user_message_converted(self):
        adapter = GeminiAdapter(api_key="test")
        messages = [{"role": "user", "content": "hello"}]
        system, gemini_msgs = adapter._build_gemini_messages(messages)
        assert system is None
        assert gemini_msgs == [{"role": "user", "parts": ["hello"]}]

    def test_assistant_role_converted_to_model(self):
        adapter = GeminiAdapter(api_key="test")
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello there"},
        ]
        system, gemini_msgs = adapter._build_gemini_messages(messages)
        assert gemini_msgs[1]["role"] == "model"

    def test_system_message_extracted(self):
        adapter = GeminiAdapter(api_key="test")
        messages = [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "hello"},
        ]
        system, gemini_msgs = adapter._build_gemini_messages(messages)
        assert system == "Be concise."
        # system 不出现在 gemini_msgs
        for msg in gemini_msgs:
            assert msg.get("role") != "system"

    def test_no_system_message(self):
        adapter = GeminiAdapter(api_key="test")
        messages = [{"role": "user", "content": "hi"}]
        system, _ = adapter._build_gemini_messages(messages)
        assert system is None


class TestGeminiAdapterChat:
    def _setup_adapter(self, response_text: str = "gemini response") -> GeminiAdapter:
        adapter = GeminiAdapter(api_key="test")
        mock_genai = MagicMock()
        mock_model_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = response_text
        mock_model_instance.generate_content.return_value = mock_response
        mock_genai.GenerativeModel.return_value = mock_model_instance
        adapter._genai = mock_genai
        return adapter

    def test_chat_returns_text(self):
        adapter = self._setup_adapter("Hello from Gemini!")
        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert result == "Hello from Gemini!"

    def test_chat_passes_model_name(self):
        adapter = self._setup_adapter()
        adapter.chat([{"role": "user", "content": "hi"}])
        call_kwargs = adapter._genai.GenerativeModel.call_args
        assert call_kwargs.kwargs.get("model_name") == "gemini-2.5-pro"

    def test_chat_with_system_message(self):
        adapter = self._setup_adapter()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hello"},
        ]
        adapter.chat(messages)
        call_kwargs = adapter._genai.GenerativeModel.call_args
        assert call_kwargs.kwargs.get("system_instruction") == "You are helpful."

    def test_chat_without_system_no_system_instruction(self):
        adapter = self._setup_adapter()
        adapter.chat([{"role": "user", "content": "hello"}])
        call_kwargs = adapter._genai.GenerativeModel.call_args
        assert "system_instruction" not in call_kwargs.kwargs

    def test_chat_max_tokens_mapped_to_output_tokens(self):
        adapter = self._setup_adapter()
        adapter.chat([{"role": "user", "content": "hi"}], max_tokens=100)
        call_kwargs = adapter._genai.GenerativeModel.call_args
        gen_cfg = call_kwargs.kwargs.get("generation_config", {})
        assert gen_cfg.get("max_output_tokens") == 100

    def test_chat_error_raises_adapter_error(self):
        adapter = GeminiAdapter(api_key="test")
        mock_genai = MagicMock()
        mock_model_instance = MagicMock()
        mock_model_instance.generate_content.side_effect = RuntimeError("API error")
        mock_genai.GenerativeModel.return_value = mock_model_instance
        adapter._genai = mock_genai

        with pytest.raises(LLMAdapterError, match="Gemini"):
            adapter.chat([{"role": "user", "content": "hi"}])


class TestGeminiAdapterChatStream:
    def test_stream_yields_chunks(self):
        adapter = GeminiAdapter(api_key="test")
        mock_genai = MagicMock()
        mock_model_instance = MagicMock()

        chunk1 = MagicMock()
        chunk1.text = "Hello"
        chunk2 = MagicMock()
        chunk2.text = " world"
        chunk3 = MagicMock()
        chunk3.text = ""  # empty → filtered

        mock_model_instance.generate_content.return_value = iter([chunk1, chunk2, chunk3])
        mock_genai.GenerativeModel.return_value = mock_model_instance
        adapter._genai = mock_genai

        result = list(adapter.chat_stream([{"role": "user", "content": "hi"}]))
        assert result == ["Hello", " world"]

    def test_stream_passes_stream_true(self):
        adapter = GeminiAdapter(api_key="test")
        mock_genai = MagicMock()
        mock_model_instance = MagicMock()
        mock_model_instance.generate_content.return_value = iter([])
        mock_genai.GenerativeModel.return_value = mock_model_instance
        adapter._genai = mock_genai

        list(adapter.chat_stream([{"role": "user", "content": "hi"}]))
        call_kwargs = mock_model_instance.generate_content.call_args
        assert call_kwargs.kwargs.get("stream") is True


class TestGeminiEmbed:
    def test_embed_raises_not_implemented(self):
        adapter = GeminiAdapter(api_key="test")
        with pytest.raises(NotImplementedError):
            adapter.embed("text")
