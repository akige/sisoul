"""sisoul LLM adapter · 抽象 base class (Phase 1 W5-W6).

§28 §1.1 模块 3: 5 LLM adapter, 只用于轻量 query + P2P 朋友共享 proxy.
不替代 Claude CLI / Codex CLI 等工具 chat.

设计要点:
- 同步接口为主 (sisoul 轻量 query 不需要 async)
- chat_stream 返回 Iterator[str] (chunk by chunk)
- embed 可选实现 (不是所有 provider 都支持)
- api_key 优先从 __init__ 传入, 其次读 env
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator


class LLMAdapter(ABC):
    """5 LLM adapter 共用抽象 base class.

    子类实现:
      AnthropicAdapter / OpenAIAdapter / GeminiAdapter / OllamaAdapter / OpenRouterAdapter

    每个子类:
    - __init__(api_key=None, model=None) — api_key 优先传入, None 则读 env
    - chat(messages, **kwargs) -> str   — 同步 unified 接口
    - chat_stream(messages, **kwargs)   — 流式 (yield chunks)
    - embed(text) -> list[float]        — 可选 (不是所有 provider 都实现)
    """

    DEFAULT_MODEL: str = ""  # 子类 override

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        """初始化 adapter.

        Args:
            api_key: LLM provider API key. None → 读 provider-specific env var.
            model: 模型名. None → 用 DEFAULT_MODEL.
        """
        self.api_key = api_key  # None 时子类各自读 env
        self.model = model or self.DEFAULT_MODEL

    @abstractmethod
    def chat(self, messages: list[dict], **kwargs) -> str:
        """同步 chat. 返回完整 assistant 回复字符串.

        Args:
            messages: OpenAI-format list[{"role": ..., "content": ...}]
            **kwargs: provider-specific (max_tokens / temperature / etc.)

        Returns:
            assistant 回复全文 (str)

        Raises:
            LLMAdapterError: provider API 错误 / key 无效 / 网络超时
        """
        ...

    @abstractmethod
    def chat_stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        """流式 chat. yield string chunks.

        Args:
            messages: 同 chat()
            **kwargs: 同 chat()

        Yields:
            str chunks (delta text)
        """
        ...

    def embed(self, text: str) -> list[float]:
        """文本 embedding. 可选实现.

        默认抛 NotImplementedError (不是所有 provider 都支持).
        支持 embed 的子类 override 此方法.

        Args:
            text: 待 embed 的文本

        Returns:
            float 向量

        Raises:
            NotImplementedError: 不支持此操作
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} 不支持 embed(). "
            "支持 embed 的 provider: OpenAIAdapter."
        )

    def chat_with_usage(
        self, messages: list[dict], **kwargs
    ) -> tuple[str, int, int]:
        """同 chat() 但返 (text, prompt_tokens, response_tokens).

        Wave B' P0-1: encrypted_proxy._default_forwarder 用此精确 token 计数.
        默认实现 chars/4 估算; AnthropicAdapter 等子类 override 真 usage 字段.
        """
        text = self.chat(messages, **kwargs)
        prompt_chars = 0
        for m in messages:
            c = m.get("content", "")
            if isinstance(c, str):
                prompt_chars += len(c)
        prompt_tokens = max(1, prompt_chars // 4)
        response_tokens = max(1, len(text) // 4)
        return text, prompt_tokens, response_tokens

    @property
    def provider_name(self) -> str:
        """provider 名称, 用于日志 / 错误信息."""
        return self.__class__.__name__.replace("Adapter", "").lower()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model!r})"


class LLMAdapterError(Exception):
    """LLM adapter 运行时错误 (API 错误 / 认证失败 / 网络超时等)."""

    def __init__(self, message: str, provider: str = "", cause: Exception | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.cause = cause

    def __str__(self) -> str:
        base = super().__str__()
        if self.provider:
            base = f"[{self.provider}] {base}"
        if self.cause:
            base = f"{base} (caused by: {type(self.cause).__name__}: {self.cause})"
        return base
