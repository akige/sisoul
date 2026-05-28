"""sisoul LLM adapter · Anthropic (Claude) (Phase 1 W5).

官方 anthropic SDK 封装.
默认 model: claude-opus-4-7 (§28 §3.3 yaml 里用的也是这个).
api_key: 优先 __init__ 传入, 其次读 ANTHROPIC_API_KEY env.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Iterator

from sisoul.llm.base import LLMAdapter, LLMAdapterError

if TYPE_CHECKING:
    pass


class AnthropicAdapter(LLMAdapter):
    """Anthropic Claude adapter (官方 anthropic SDK).

    支持 model:
    - claude-opus-4-7  (默认, 最强)
    - claude-sonnet-4-6
    - claude-haiku-3-5 等 claude-* 系列

    用法:
        adapter = AnthropicAdapter()  # 读 ANTHROPIC_API_KEY
        response = adapter.chat([{"role": "user", "content": "hello"}])
    """

    DEFAULT_MODEL = "claude-opus-4-7"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        super().__init__(api_key=api_key, model=model)
        self._client = None  # lazy init, 避免 import 时需要真 key

    def _get_client(self):
        """懒加载 anthropic.Anthropic client."""
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise LLMAdapterError(
                    "anthropic SDK 未安装. 请: pip install 'sisoul[llm]'",
                    provider="anthropic",
                    cause=e,
                ) from e

            key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise LLMAdapterError(
                    "ANTHROPIC_API_KEY 未设置. 请 sisoul login --provider claude 或 "
                    "export ANTHROPIC_API_KEY=sk-ant-...",
                    provider="anthropic",
                )
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def chat(self, messages: list[dict], **kwargs) -> str:
        """同步 chat. 返回 assistant 全文.

        Args:
            messages: list[{"role": "user"/"assistant", "content": str}]
            **kwargs:
                max_tokens (int): 默认 1024
                temperature (float): 默认 1.0

        Returns:
            assistant 回复字符串

        Raises:
            LLMAdapterError: Anthropic API 错误
        """
        client = self._get_client()
        max_tokens = kwargs.get("max_tokens", 1024)
        temperature = kwargs.get("temperature", 1.0)

        # Anthropic messages API 不支持 system 在 messages 里 (用 top-level system 参数)
        # 这里做简单处理: 提取第一条 system message (如有)
        system_prompt = None
        filtered_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            else:
                filtered_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            create_kwargs = dict(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=filtered_messages,
            )
            if system_prompt is not None:
                create_kwargs["system"] = system_prompt

            response = client.messages.create(**create_kwargs)
            return response.content[0].text
        except Exception as e:
            # 捕获所有 anthropic exceptions 统一转 LLMAdapterError
            from anthropic import APIError
            if isinstance(e, APIError):
                raise LLMAdapterError(
                    f"Anthropic API error: {e}",
                    provider="anthropic",
                    cause=e,
                ) from e
            raise LLMAdapterError(
                f"Anthropic unexpected error: {e}",
                provider="anthropic",
                cause=e,
            ) from e

    def chat_with_usage(
        self, messages: list[dict], **kwargs
    ) -> tuple[str, int, int]:
        """Wave B' P0-1: 真 token 用量 (Anthropic SDK usage 字段)."""
        client = self._get_client()
        max_tokens = kwargs.get("max_tokens", 1024)
        temperature = kwargs.get("temperature", 1.0)

        system_prompt = None
        filtered_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            else:
                filtered_messages.append(
                    {"role": msg["role"], "content": msg["content"]}
                )

        try:
            create_kwargs = dict(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=filtered_messages,
            )
            if system_prompt is not None:
                create_kwargs["system"] = system_prompt

            response = client.messages.create(**create_kwargs)
            text = response.content[0].text
            usage = getattr(response, "usage", None)
            if usage is not None:
                in_tok = int(getattr(usage, "input_tokens", 0) or 0)
                out_tok = int(getattr(usage, "output_tokens", 0) or 0)
            else:
                in_tok = max(1, sum(len(m.get("content", "")) for m in messages) // 4)
                out_tok = max(1, len(text) // 4)
            return text, in_tok, out_tok
        except Exception as e:
            from anthropic import APIError
            if isinstance(e, APIError):
                raise LLMAdapterError(
                    f"Anthropic API error: {e}",
                    provider="anthropic",
                    cause=e,
                ) from e
            raise LLMAdapterError(
                f"Anthropic unexpected error: {e}",
                provider="anthropic",
                cause=e,
            ) from e

    def chat_stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        """流式 chat. yield delta text chunks.

        Args:
            messages: 同 chat()
            **kwargs: max_tokens / temperature

        Yields:
            str delta chunks
        """
        client = self._get_client()
        max_tokens = kwargs.get("max_tokens", 1024)
        temperature = kwargs.get("temperature", 1.0)

        system_prompt = None
        filtered_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            else:
                filtered_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            create_kwargs = dict(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=filtered_messages,
            )
            if system_prompt is not None:
                create_kwargs["system"] = system_prompt

            with client.messages.stream(**create_kwargs) as stream:
                for text in stream.text_stream:
                    yield text
        except Exception as e:
            raise LLMAdapterError(
                f"Anthropic stream error: {e}",
                provider="anthropic",
                cause=e,
            ) from e
