"""sisoul llm 模块 (Phase 1 W5-W6).

5 LLM adapter 统一入口:
- AnthropicAdapter (claude-opus-4-7 默认)
- OpenAIAdapter (gpt-4o 默认)
- GeminiAdapter (gemini-2.5-pro 默认)
- OllamaAdapter (llama3.2 默认, 本地无 key)
- OpenRouterAdapter (openai/gpt-4o 默认)

工厂函数:
- get_adapter(provider, api_key=None, model=None) → LLMAdapter

provider 别名映射:
- "claude" / "anthropic" → AnthropicAdapter
- "openai" / "gpt"       → OpenAIAdapter
- "gemini" / "google"    → GeminiAdapter
- "ollama" / "local"     → OllamaAdapter
- "openrouter"           → OpenRouterAdapter
"""

from __future__ import annotations

from sisoul.llm.base import LLMAdapter, LLMAdapterError
from sisoul.llm.anthropic import AnthropicAdapter
from sisoul.llm.openai import OpenAIAdapter
from sisoul.llm.gemini import GeminiAdapter
from sisoul.llm.ollama import OllamaAdapter
from sisoul.llm.openrouter import OpenRouterAdapter

__all__ = [
    "LLMAdapter",
    "LLMAdapterError",
    "AnthropicAdapter",
    "OpenAIAdapter",
    "GeminiAdapter",
    "OllamaAdapter",
    "OpenRouterAdapter",
    "get_adapter",
    "PROVIDER_ALIASES",
]

# provider 别名 → adapter class 映射
PROVIDER_ALIASES: dict[str, type[LLMAdapter]] = {
    "claude": AnthropicAdapter,
    "anthropic": AnthropicAdapter,
    "openai": OpenAIAdapter,
    "gpt": OpenAIAdapter,
    "gpt4o": OpenAIAdapter,
    "gemini": GeminiAdapter,
    "google": GeminiAdapter,
    "ollama": OllamaAdapter,
    "local": OllamaAdapter,
    "openrouter": OpenRouterAdapter,
}


def get_adapter(
    provider: str,
    api_key: str | None = None,
    model: str | None = None,
    **kwargs,
) -> LLMAdapter:
    """工厂函数: 按 provider 名称返回对应 LLMAdapter 实例.

    Args:
        provider: provider 名称或别名 (大小写不敏感).
                  支持: claude / anthropic / openai / gpt /
                        gemini / google / ollama / local / openrouter
        api_key: API key (None → 读 env)
        model: 模型名 (None → 用各 provider 默认)
        **kwargs: 传给 adapter __init__ 的额外参数
                  (e.g. OllamaAdapter base_url / OpenRouterAdapter site_url)

    Returns:
        LLMAdapter 实例

    Raises:
        ValueError: 未知 provider
    """
    key = provider.lower().strip()
    adapter_cls = PROVIDER_ALIASES.get(key)
    if adapter_cls is None:
        supported = sorted(set(PROVIDER_ALIASES.keys()))
        raise ValueError(
            f"未知 LLM provider: {provider!r}. "
            f"支持: {supported}"
        )
    return adapter_cls(api_key=api_key, model=model, **kwargs)
