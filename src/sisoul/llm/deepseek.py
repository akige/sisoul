"""sisoul LLM adapter · DeepSeek (Phase 2 P2-4).

DeepSeek API: https://api.deepseek.com/v1 (OpenAI-compatible chat/completions).
env: DEEPSEEK_API_KEY (优先 __init__ 传入).
默认 model: deepseek-chat.

#6 dedup: OpenAI-compatible httpx 逻辑全在 OpenAICompatHTTPXAdapter 基类, 本类只
声明 provider-specific 常量. 行为与原手写实现完全等价.
"""

from __future__ import annotations

from sisoul.llm._openai_compat_base import OpenAICompatHTTPXAdapter


class DeepSeekAdapter(OpenAICompatHTTPXAdapter):
    """DeepSeek adapter (OpenAI-compatible REST).

    支持 model:
    - deepseek-chat (默认, V3)
    - deepseek-reasoner (R1)
    - deepseek-coder

    用法:
        adapter = DeepSeekAdapter()  # 读 DEEPSEEK_API_KEY
        response = adapter.chat([{"role": "user", "content": "hello"}])
    """

    PROVIDER = "deepseek"
    PROVIDER_LABEL = "DeepSeek"
    API_KEY_ENV = "DEEPSEEK_API_KEY"
    DEFAULT_MODEL = "deepseek-chat"
    BASE_URL = "https://api.deepseek.com/v1"
    KEY_EXAMPLE = "sk-..."
    DEFAULT_TIMEOUT = 60.0

    KNOWN_MODELS = (
        "deepseek-chat",
        "deepseek-reasoner",
        "deepseek-coder",
    )
