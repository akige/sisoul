"""sisoul LLM adapter · Grok (xAI) (Phase 2 P2-4).

xAI API endpoint: https://api.x.ai/v1 (OpenAI-compatible chat/completions).
env: XAI_API_KEY (优先 __init__ 传入).
默认 model: grok-2-latest.

不依赖额外 SDK; 直接 httpx 调 OpenAI-compatible endpoint. 避免给项目增 dep.

#6 dedup: OpenAI-compatible httpx 逻辑全在 OpenAICompatHTTPXAdapter 基类, 本类只
声明 provider-specific 常量. 行为与原手写实现完全等价.
"""

from __future__ import annotations

from sisoul.llm._openai_compat_base import OpenAICompatHTTPXAdapter


class GrokAdapter(OpenAICompatHTTPXAdapter):
    """xAI Grok adapter (OpenAI-compatible REST).

    支持 model:
    - grok-2-latest (默认)
    - grok-2 / grok-2-mini / grok-beta 等

    用法:
        adapter = GrokAdapter()  # 读 XAI_API_KEY
        response = adapter.chat([{"role": "user", "content": "hello"}])
    """

    PROVIDER = "grok"
    PROVIDER_LABEL = "Grok"
    API_KEY_ENV = "XAI_API_KEY"
    DEFAULT_MODEL = "grok-2-latest"
    BASE_URL = "https://api.x.ai/v1"
    KEY_EXAMPLE = "xai-..."
    DEFAULT_TIMEOUT = 60.0

    # 公共已知 model 列表 (用户可自由传别的, 这里仅 helper)
    KNOWN_MODELS = (
        "grok-2-latest",
        "grok-2",
        "grok-2-mini",
        "grok-beta",
    )
