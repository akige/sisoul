"""sisoul LLM adapter · OpenAI (Phase 1 W6).

官方 openai SDK 封装.
默认 model: gpt-4o.
api_key: 优先 __init__ 传入, 其次读 OPENAI_API_KEY env.

embed() 支持 text-embedding-3-small / text-embedding-3-large.
"""

from __future__ import annotations

import os
from typing import Iterator

from sisoul.llm.base import LLMAdapter, LLMAdapterError


class OpenAIAdapter(LLMAdapter):
    """OpenAI GPT adapter (官方 openai SDK).

    支持 model:
    - gpt-4o (默认)
    - gpt-4o-mini
    - gpt-4-turbo
    - gpt-3.5-turbo 等

    embed() 使用 text-embedding-3-small (可 override).

    用法:
        adapter = OpenAIAdapter()  # 读 OPENAI_API_KEY
        response = adapter.chat([{"role": "user", "content": "hello"}])
    """

    DEFAULT_MODEL = "gpt-4o"
    DEFAULT_EMBED_MODEL = "text-embedding-3-small"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        embed_model: str | None = None,
    ) -> None:
        super().__init__(api_key=api_key, model=model)
        self.embed_model = embed_model or self.DEFAULT_EMBED_MODEL
        self._client = None

    def _get_client(self):
        """懒加载 openai.OpenAI client."""
        if self._client is None:
            try:
                import openai
            except ImportError as e:
                raise LLMAdapterError(
                    "openai SDK 未安装. 请: pip install 'sisoul[llm]'",
                    provider="openai",
                    cause=e,
                ) from e

            key = self.api_key or os.environ.get("OPENAI_API_KEY")
            if not key:
                raise LLMAdapterError(
                    "OPENAI_API_KEY 未设置. 请 sisoul login --provider openai 或 "
                    "export OPENAI_API_KEY=sk-...",
                    provider="openai",
                )
            self._client = openai.OpenAI(api_key=key)
        return self._client

    def chat(self, messages: list[dict], **kwargs) -> str:
        """同步 chat. 返回 assistant 全文.

        Args:
            messages: list[{"role": ..., "content": ...}] (system/user/assistant 均支持)
            **kwargs:
                max_tokens (int): 默认不设 (OpenAI 默认)
                temperature (float): 默认 1.0

        Returns:
            assistant 回复字符串

        Raises:
            LLMAdapterError: OpenAI API 错误
        """
        client = self._get_client()

        create_kwargs: dict = dict(model=self.model, messages=messages)
        if "max_tokens" in kwargs:
            create_kwargs["max_tokens"] = kwargs["max_tokens"]
        if "temperature" in kwargs:
            create_kwargs["temperature"] = kwargs["temperature"]

        try:
            response = client.chat.completions.create(**create_kwargs)
            return response.choices[0].message.content or ""
        except Exception as e:
            raise LLMAdapterError(
                f"OpenAI API error: {e}",
                provider="openai",
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

        create_kwargs: dict = dict(model=self.model, messages=messages, stream=True)
        if "max_tokens" in kwargs:
            create_kwargs["max_tokens"] = kwargs["max_tokens"]
        if "temperature" in kwargs:
            create_kwargs["temperature"] = kwargs["temperature"]

        try:
            stream = client.chat.completions.create(**create_kwargs)
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            raise LLMAdapterError(
                f"OpenAI stream error: {e}",
                provider="openai",
                cause=e,
            ) from e

    def embed(self, text: str) -> list[float]:
        """文本 embedding (text-embedding-3-small 默认).

        Args:
            text: 待 embed 的文本

        Returns:
            float 向量

        Raises:
            LLMAdapterError: OpenAI API 错误
        """
        client = self._get_client()
        try:
            response = client.embeddings.create(
                model=self.embed_model,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            raise LLMAdapterError(
                f"OpenAI embed error: {e}",
                provider="openai",
                cause=e,
            ) from e
