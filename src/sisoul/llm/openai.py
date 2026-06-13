"""sisoul LLM adapter · OpenAI (Phase 1 W6).

官方 openai SDK 封装.
默认 model: gpt-4o.
api_key: 优先 __init__ 传入, 其次读 OPENAI_API_KEY env.

embed() 支持 text-embedding-3-small / text-embedding-3-large.

#6 dedup: chat / chat_stream 在 OpenAISDKCompatAdapter 基类 (与 OpenRouter 共享),
本类只留 provider-specific 的 _get_client (base_url-from-env) + embed.
"""

from __future__ import annotations

import os

from sisoul.llm._openai_compat_base import OpenAISDKCompatAdapter
from sisoul.llm.base import LLMAdapterError


class OpenAIAdapter(OpenAISDKCompatAdapter):
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

    PROVIDER = "openai"
    PROVIDER_LABEL = "OpenAI"
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
            # OPENAI_API_BASE / OPENAI_BASE_URL → 自建 / 兼容 endpoint
            # (lend proxy 场景: lender daemon 把 borrow 转给自己配置的 endpoint)
            base_url = os.environ.get("OPENAI_API_BASE") or os.environ.get(
                "OPENAI_BASE_URL"
            )
            if base_url:
                self._client = openai.OpenAI(api_key=key, base_url=base_url)
            else:
                self._client = openai.OpenAI(api_key=key)
        return self._client

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
