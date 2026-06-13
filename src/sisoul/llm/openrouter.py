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

site_url / app_title 参数可传 HTTP-Referer + X-Title header (OpenRouter analytics, 可选).

#6 dedup: chat / chat_stream 在 OpenAISDKCompatAdapter 基类 (与 OpenAI 共享),
本类只留 provider-specific 的 _get_client (base_url + analytics headers).
"""

from __future__ import annotations

import os

from sisoul.llm._openai_compat_base import OpenAISDKCompatAdapter
from sisoul.llm.base import LLMAdapterError

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterAdapter(OpenAISDKCompatAdapter):
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

    PROVIDER = "openrouter"
    PROVIDER_LABEL = "OpenRouter"
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
