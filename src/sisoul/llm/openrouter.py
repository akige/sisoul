"""sisoul LLM adapter · OpenRouter (Phase 1 W6).

用 openai SDK + base_url=https://openrouter.ai/api/v1 实现.
OpenRouter 兼容 OpenAI Chat Completions API.
api_key: 优先 __init__ 传入, 其次读 OPENROUTER_API_KEY env.

默认 model: openai/gpt-4o (OpenRouter 路由格式: <provider>/<model>).

推荐 models via OpenRouter:
- openai/gpt-4o
- anthropic/claude-opus-4-7
- google/gemini-2.5-pro
- meta-llama/llama-3.2-3b-instruct (免费)
- mistralai/mistral-7b-instruct (免费)

TODO: OpenRouter 支持 HTTP-Referer + X-Title header (可选, 用于 analytics).
"""

from __future__ import annotations

import os
from typing import Iterator

from sisoul.llm.base import LLMAdapter, LLMAdapterError

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterAdapter(LLMAdapter):
    """OpenRouter adapter (复用 openai SDK, 改 base_url).

    OpenRouter 提供统一 API 代理: Claude / GPT / Gemini / Llama 等.
    适合 P2P 朋友共享时路由到不同 provider.

    支持 model (OpenRouter 格式 "<provider>/<model>"):
    - openai/gpt-4o (默认)
    - anthropic/claude-opus-4-7
    - google/gemini-2.5-pro
    - meta-llama/llama-3.2-3b-instruct (免费)

    用法:
        adapter = OpenRouterAdapter()  # 读 OPENROUTER_API_KEY
        response = adapter.chat([{"role": "user", "content": "hello"}])
    """

    DEFAULT_MODEL = "openai/gpt-4o"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        site_url: str | None = None,  # 可选: HTTP-Referer header
        site_name: str | None = None,  # 可选: X-Title header
    ) -> None:
        super().__init__(api_key=api_key, model=model)
        self.site_url = site_url or "http://localhost"
        self.site_name = site_name or "sisoul"
        self._client = None

    def _get_client(self):
        """懒加载 openai.OpenAI client 指向 OpenRouter."""
        if self._client is None:
            try:
                import openai
            except ImportError as e:
                raise LLMAdapterError(
                    "openai SDK 未安装 (OpenRouter 用 openai SDK). 请: pip install 'sisoul[llm]'",
                    provider="openrouter",
                    cause=e,
                ) from e

            key = self.api_key or os.environ.get("OPENROUTER_API_KEY")
            if not key:
                raise LLMAdapterError(
                    "OPENROUTER_API_KEY 未设置. 请 sisoul login --provider openrouter 或 "
                    "export OPENROUTER_API_KEY=sk-or-...",
                    provider="openrouter",
                )
            self._client = openai.OpenAI(
                api_key=key,
                base_url=OPENROUTER_BASE_URL,
                default_headers={
                    "HTTP-Referer": self.site_url,
                    "X-Title": self.site_name,
                },
            )
        return self._client

    def chat(self, messages: list[dict], **kwargs) -> str:
        """同步 chat via OpenRouter.

        Args:
            messages: list[{"role": ..., "content": ...}]
            **kwargs:
                max_tokens (int)
                temperature (float)

        Returns:
            assistant 回复字符串

        Raises:
            LLMAdapterError: OpenRouter API 错误 / key 无效
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
                f"OpenRouter API error: {e}",
                provider="openrouter",
                cause=e,
            ) from e

    def chat_stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        """流式 chat via OpenRouter. yield delta text chunks.

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
                f"OpenRouter stream error: {e}",
                provider="openrouter",
                cause=e,
            ) from e
