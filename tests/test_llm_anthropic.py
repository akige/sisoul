"""tests/test_llm_anthropic.py — AnthropicAdapter 单元测试 (mock SDK).

全部测试 mock anthropic SDK, 不调真 API.
覆盖:
- chat() 正常路径
- chat() 错误处理 (APIError / 未知 exception)
- chat_stream() 正常路径
- chat() system message 分离
- api_key 从 env 读取
- api_key 未设置时抛 LLMAdapterError
- anthropic SDK 未安装时抛 LLMAdapterError
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from sisoul.llm.anthropic import AnthropicAdapter
from sisoul.llm.base import LLMAdapterError


class TestAnthropicAdapterInit:
    def test_default_model(self):
        adapter = AnthropicAdapter(api_key="test")
        assert adapter.model == "claude-opus-4-7"

    def test_custom_model(self):
        adapter = AnthropicAdapter(api_key="test", model="claude-sonnet-4-6")
        assert adapter.model == "claude-sonnet-4-6"

    def test_api_key_stored(self):
        adapter = AnthropicAdapter(api_key="sk-ant-test")
        assert adapter.api_key == "sk-ant-test"

    def test_client_initially_none(self):
        adapter = AnthropicAdapter(api_key="test")
        assert adapter._client is None  # lazy init


class TestAnthropicAdapterApiKey:
    def test_raises_if_no_key(self):
        """api_key=None + env 不设 → 抛 LLMAdapterError."""
        adapter = AnthropicAdapter(api_key=None)
        with patch.dict(os.environ, {}, clear=False):
            # 确保 env 没有 ANTHROPIC_API_KEY
            env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
            try:
                with pytest.raises(LLMAdapterError, match="ANTHROPIC_API_KEY"):
                    adapter._get_client()
            finally:
                if env_backup:
                    os.environ["ANTHROPIC_API_KEY"] = env_backup

    def test_reads_key_from_env(self):
        """api_key=None 但 env ANTHROPIC_API_KEY 有值 → 成功."""
        adapter = AnthropicAdapter(api_key=None)
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-from-env"}):
            with patch("sisoul.llm.anthropic.anthropic", mock_anthropic, create=True):
                with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
                    adapter._client = None
                    client = adapter._get_client()
                    assert client is mock_client

    def test_import_error_raises_adapter_error(self):
        """anthropic SDK 未安装 → 抛 LLMAdapterError."""
        adapter = AnthropicAdapter(api_key="test")
        adapter._client = None  # 重置 lazy init

        with patch("builtins.__import__", side_effect=ImportError("No module named 'anthropic'")):
            # 用更直接的 patch 方式
            pass  # 跳过这个 case, 用下面更可靠的方式

    def test_sdk_not_installed(self):
        """模拟 SDK 未安装: 覆盖 _get_client 触发 ImportError."""
        adapter = AnthropicAdapter(api_key="test")
        adapter._client = None

        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else None

        import builtins
        original = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("No module named 'anthropic'")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(LLMAdapterError, match="anthropic SDK 未安装"):
                adapter._get_client()


class TestAnthropicAdapterChat:
    def _make_mock_client(self, response_text: str) -> MagicMock:
        mock_client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=response_text)]
        mock_client.messages.create.return_value = mock_msg
        return mock_client

    def test_chat_returns_text(self):
        adapter = AnthropicAdapter(api_key="test")
        adapter._client = self._make_mock_client("Hello, world!")

        result = adapter.chat([{"role": "user", "content": "hi"}])
        assert result == "Hello, world!"

    def test_chat_passes_model(self):
        adapter = AnthropicAdapter(api_key="test", model="claude-sonnet-4-6")
        mock_client = self._make_mock_client("response")
        adapter._client = mock_client

        adapter.chat([{"role": "user", "content": "hi"}])
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == "claude-sonnet-4-6"

    def test_chat_passes_max_tokens(self):
        adapter = AnthropicAdapter(api_key="test")
        mock_client = self._make_mock_client("response")
        adapter._client = mock_client

        adapter.chat([{"role": "user", "content": "hi"}], max_tokens=5)
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 5

    def test_chat_default_max_tokens_1024(self):
        adapter = AnthropicAdapter(api_key="test")
        mock_client = self._make_mock_client("response")
        adapter._client = mock_client

        adapter.chat([{"role": "user", "content": "hi"}])
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 1024

    def test_chat_separates_system_message(self):
        """system role message → 提取为 system 参数 (Anthropic API 要求)."""
        adapter = AnthropicAdapter(api_key="test")
        mock_client = self._make_mock_client("response")
        adapter._client = mock_client

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hello"},
        ]
        adapter.chat(messages)
        call_kwargs = mock_client.messages.create.call_args
        # system 应该作为独立参数传入
        assert call_kwargs.kwargs.get("system") == "You are helpful."
        # messages 里不应该有 system role
        for msg in call_kwargs.kwargs["messages"]:
            assert msg["role"] != "system"

    def test_chat_no_system_message(self):
        """无 system message → 不传 system 参数."""
        adapter = AnthropicAdapter(api_key="test")
        mock_client = self._make_mock_client("response")
        adapter._client = mock_client

        adapter.chat([{"role": "user", "content": "hi"}])
        call_kwargs = mock_client.messages.create.call_args
        assert "system" not in call_kwargs.kwargs

    def test_chat_api_error_raises_adapter_error(self):
        """Anthropic APIError → 转 LLMAdapterError."""
        import sys
        mock_anthropic = MagicMock()

        class FakeAPIError(Exception):
            pass

        mock_anthropic.APIError = FakeAPIError

        adapter = AnthropicAdapter(api_key="test")
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = FakeAPIError("rate limit")
        adapter._client = mock_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            with pytest.raises(LLMAdapterError):
                adapter.chat([{"role": "user", "content": "hi"}])

    def test_chat_unexpected_error_raises_adapter_error(self):
        """非 Anthropic 错误 → 也转 LLMAdapterError."""
        import sys
        mock_anthropic = MagicMock()
        mock_anthropic.APIError = ValueError  # dummy

        adapter = AnthropicAdapter(api_key="test")
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("network error")
        adapter._client = mock_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            with pytest.raises(LLMAdapterError, match="Anthropic"):
                adapter.chat([{"role": "user", "content": "hi"}])


class TestAnthropicAdapterChatStream:
    def test_stream_yields_chunks(self):
        adapter = AnthropicAdapter(api_key="test")

        # mock stream context manager
        mock_client = MagicMock()
        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__enter__ = MagicMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__exit__ = MagicMock(return_value=False)
        mock_stream_ctx.text_stream = iter(["Hello", " ", "world"])
        mock_client.messages.stream.return_value = mock_stream_ctx
        adapter._client = mock_client

        chunks = list(adapter.chat_stream([{"role": "user", "content": "hi"}]))
        assert chunks == ["Hello", " ", "world"]

    def test_stream_empty(self):
        adapter = AnthropicAdapter(api_key="test")
        mock_client = MagicMock()
        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__enter__ = MagicMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__exit__ = MagicMock(return_value=False)
        mock_stream_ctx.text_stream = iter([])
        mock_client.messages.stream.return_value = mock_stream_ctx
        adapter._client = mock_client

        chunks = list(adapter.chat_stream([{"role": "user", "content": "hi"}]))
        assert chunks == []

    def test_stream_passes_system_message(self):
        """stream 也分离 system message."""
        adapter = AnthropicAdapter(api_key="test")
        mock_client = MagicMock()
        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__enter__ = MagicMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__exit__ = MagicMock(return_value=False)
        mock_stream_ctx.text_stream = iter(["ok"])
        mock_client.messages.stream.return_value = mock_stream_ctx
        adapter._client = mock_client

        messages = [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "hello"},
        ]
        list(adapter.chat_stream(messages))
        call_kwargs = mock_client.messages.stream.call_args
        assert call_kwargs.kwargs.get("system") == "Be concise."


class TestAnthropicEmbed:
    def test_embed_raises_not_implemented(self):
        adapter = AnthropicAdapter(api_key="test")
        with pytest.raises(NotImplementedError):
            adapter.embed("some text")
