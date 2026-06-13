"""sisoul LLM adapter · OpenAI-compatible httpx base (#6 dedup).

多个 provider (DeepSeek / Grok / ... 任何 OpenAI-compatible chat/completions REST
endpoint) 的 adapter 逻辑 ~100% 相同: 懒加载 httpx.Client + Bearer auth → POST
``/chat/completions`` (sync + SSE stream) → char/4 token 估算 → 静态 model 列表.

本基类把这套逻辑收一处, 子类只声明几个 class 属性即可 (~15 行):

    class FooAdapter(OpenAICompatHTTPXAdapter):
        PROVIDER = "foo"
        PROVIDER_LABEL = "Foo"
        API_KEY_ENV = "FOO_API_KEY"
        DEFAULT_MODEL = "foo-large"
        BASE_URL = "https://api.foo.com/v1"
        KEY_EXAMPLE = "sk-..."
        KNOWN_MODELS = ("foo-large", "foo-mini")

行为与原各 provider 手写实现**完全等价** (方法名 / 错误信息 / payload 形状 / token
估算逻辑全部保持), 因此现有 provider 测试不回归.
"""

from __future__ import annotations

import os
from typing import Iterator

from sisoul.llm.base import LLMAdapter, LLMAdapterError


class OpenAICompatHTTPXAdapter(LLMAdapter):
    """OpenAI-compatible REST adapter 基类 (httpx, 无额外 SDK 依赖).

    子类必须 override 的 class 属性:
        PROVIDER:        provider 短名 (用于 LLMAdapterError.provider + login 提示)
        PROVIDER_LABEL:  人类可读名 (用于 "<Label> API error" 错误信息)
        API_KEY_ENV:     读 key 的 env 变量名
        DEFAULT_MODEL:   默认 model
        BASE_URL:        OpenAI-compatible base URL (含 /v1)
    可选:
        KEY_EXAMPLE:     错误信息里的示例 key 前缀 (默认 "sk-...")
        DEFAULT_TIMEOUT: httpx 超时 (默认 60.0)
        KNOWN_MODELS:    list_models() 返回的静态列表
    """

    PROVIDER: str = ""
    PROVIDER_LABEL: str = ""
    API_KEY_ENV: str = ""
    BASE_URL: str = ""
    KEY_EXAMPLE: str = "sk-..."
    DEFAULT_TIMEOUT: float = 60.0
    KNOWN_MODELS: tuple[str, ...] = ()

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
                    provider=self.PROVIDER,
                    cause=e,
                ) from e
            key = self.api_key or os.environ.get(self.API_KEY_ENV)
            if not key:
                raise LLMAdapterError(
                    f"{self.API_KEY_ENV} 未设置. 请 sisoul login --provider "
                    f"{self.PROVIDER} 或 export {self.API_KEY_ENV}={self.KEY_EXAMPLE}",
                    provider=self.PROVIDER,
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
                f"{self.PROVIDER_LABEL} API error: {e}",
                provider=self.PROVIDER,
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
                f"{self.PROVIDER_LABEL} stream error: {e}",
                provider=self.PROVIDER,
                cause=e,
            ) from e

    def count_tokens(self, text: str) -> int:
        """token 估算 heuristic (无官方 tokenizer endpoint; ~4 char/token)."""
        if not text:
            return 0
        return max(1, len(text) // 4)

    def list_models(self) -> list[str]:
        """返回已知 model 列表 (静态; 真要拉 API 走 /models endpoint)."""
        return list(self.KNOWN_MODELS)


class OpenAISDKCompatAdapter(LLMAdapter):
    """官方 ``openai`` SDK 路径的 OpenAI-compatible adapter 基类 (#6 dedup).

    OpenAIAdapter / OpenRouterAdapter 共享完全相同的 ``chat`` / ``chat_stream``
    实现 (都走 ``client.chat.completions.create``), 只是 ``_get_client`` 构造
    client 的细节不同 (base_url / 额外 header / embed 支持). 本基类收 chat/stream,
    子类只实现 ``_get_client`` + 声明 ``PROVIDER`` / ``PROVIDER_LABEL``.

    行为与原各 provider 手写实现**完全等价**.
    """

    PROVIDER: str = ""
    PROVIDER_LABEL: str = ""

    def _get_client(self):  # pragma: no cover - 子类必须实现
        raise NotImplementedError

    @staticmethod
    def _build_create_kwargs(model: str, messages: list[dict], stream: bool, kwargs: dict) -> dict:
        create_kwargs: dict = dict(model=model, messages=messages)
        if stream:
            create_kwargs["stream"] = True
        if "max_tokens" in kwargs:
            create_kwargs["max_tokens"] = kwargs["max_tokens"]
        if "temperature" in kwargs:
            create_kwargs["temperature"] = kwargs["temperature"]
        return create_kwargs

    def chat(self, messages: list[dict], **kwargs) -> str:
        """同步 chat. 返回 assistant 全文."""
        client = self._get_client()
        create_kwargs = self._build_create_kwargs(self.model, messages, False, kwargs)
        try:
            response = client.chat.completions.create(**create_kwargs)
            return response.choices[0].message.content or ""
        except Exception as e:
            raise LLMAdapterError(
                f"{self.PROVIDER_LABEL} API error: {e}",
                provider=self.PROVIDER,
                cause=e,
            ) from e

    def chat_stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        """流式 chat. yield delta text chunks."""
        client = self._get_client()
        create_kwargs = self._build_create_kwargs(self.model, messages, True, kwargs)
        try:
            stream = client.chat.completions.create(**create_kwargs)
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except Exception as e:
            raise LLMAdapterError(
                f"{self.PROVIDER_LABEL} stream error: {e}",
                provider=self.PROVIDER,
                cause=e,
            ) from e


__all__ = ["OpenAICompatHTTPXAdapter", "OpenAISDKCompatAdapter"]
