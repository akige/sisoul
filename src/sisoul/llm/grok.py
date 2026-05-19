"""sisoul LLM adapter · Grok (xAI) (Phase 2 P2-4).

xAI API endpoint: https://api.x.ai/v1 (OpenAI-compatible chat/completions).
env: XAI_API_KEY (优先 __init__ 传入).
默认 model: grok-2-latest.

不依赖额外 SDK; 直接 httpx 调 OpenAI-compatible endpoint. 避免给项目增 dep.
"""

from __future__ import annotations

import os
from typing import Iterator

from sisoul.llm.base import LLMAdapter, LLMAdapterError


class GrokAdapter(LLMAdapter):
    """xAI Grok adapter (OpenAI-compatible REST).

    支持 model:
    - grok-2-latest (默认)
    - grok-2 / grok-2-mini / grok-beta 等

    用法:
        adapter = GrokAdapter()  # 读 XAI_API_KEY
        response = adapter.chat([{"role": "user", "content": "hello"}])
    """

    DEFAULT_MODEL = "grok-2-latest"
    BASE_URL = "https://api.x.ai/v1"
    DEFAULT_TIMEOUT = 60.0

    # 公共已知 model 列表 (用户可自由传别的, 这里仅 helper)
    KNOWN_MODELS = (
        "grok-2-latest",
        "grok-2",
        "grok-2-mini",
        "grok-beta",
    )

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        super().__init__(api_key=api_key, model=model)
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        self._client = None

    def _get_client(self):
        """懒加载 httpx.Client. 缺 key → LLMAdapterError."""
        if self._client is None:
            try:
                import httpx
            except ImportError as e:
                raise LLMAdapterError(
                    "httpx 未安装 (sisoul base dep, 不应缺). 请 pip install httpx",
                    provider="grok",
                    cause=e,
                ) from e
            key = self.api_key or os.environ.get("XAI_API_KEY")
            if not key:
                raise LLMAdapterError(
                    "XAI_API_KEY 未设置. 请 sisoul login --provider grok 或 "
                    "export XAI_API_KEY=xai-...",
                    provider="grok",
                )
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    def chat(self, messages: list[dict], **kwargs) -> str:
        """同步 chat (POST /chat/completions, OpenAI format)."""
        client = self._get_client()
        payload: dict = {"model": self.model, "messages": messages}
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]

        try:
            resp = client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""
        except Exception as e:
            raise LLMAdapterError(
                f"Grok API error: {e}",
                provider="grok",
                cause=e,
            ) from e

    def chat_stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        """流式 chat (SSE OpenAI format)."""
        client = self._get_client()
        payload: dict = {"model": self.model, "messages": messages, "stream": True}
        if "max_tokens" in kwargs:
            payload["max_tokens"] = kwargs["max_tokens"]
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]

        try:
            with client.stream("POST", "/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    line_s = line.decode("utf-8") if isinstance(line, bytes) else line
                    if not line_s.startswith("data:"):
                        continue
                    data_s = line_s[5:].strip()
                    if data_s == "[DONE]":
                        return
                    try:
                        import json as _json
                        ev = _json.loads(data_s)
                        delta = ev["choices"][0].get("delta", {}).get("content")
                        if delta:
                            yield delta
                    except Exception:
                        continue
        except Exception as e:
            raise LLMAdapterError(
                f"Grok stream error: {e}",
                provider="grok",
                cause=e,
            ) from e

    def count_tokens(self, text: str) -> int:
        """token 估算 (无官方 tokenizer endpoint, 用 char/4 heuristic)."""
        if not text:
            return 0
        return max(1, len(text) // 4)

    def list_models(self) -> list[str]:
        """返回已知 model 列表 (静态; 真要拉 API 走 /models endpoint)."""
        return list(self.KNOWN_MODELS)
