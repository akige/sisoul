"""sisoul LLM adapter · newapi free-pool (sisoul founder-agent backend).

newapi (https://github.com/QuantumNous/new-api) is a self-hosted LLM gateway
that fronts multiple providers (Anthropic / OpenAI / Gemini / OpenRouter /
GitHub Copilot) behind a single OpenAI-compatible endpoint with priority-based
failover.

Configuration via env:
    SISOUL_NEWAPI_BASE_URL   default http://127.0.0.1:4000/v1  (override for remote gateway)
    SISOUL_NEWAPI_API_KEY    virtual key issued by newapi admin
    SISOUL_NEWAPI_MODEL      default "free-pool" (newapi-side abilities chain)

Why this adapter exists:
    The founder-agent (`@founder`) and three-machine demo agents (mac /
    remote-vps / wsl) run continuously and serve alpha testers. Using a per-call
    Anthropic key would be expensive and ties the persona to a single provider.
    The newapi free-pool routes each call through priority-failover:
        copilot-A (Pro+ 1500/mo)
            -> copilot-B (Pro 300/mo)
            -> gemini-flash-lite (Google free tier)
            -> openrouter open-source models (free)
    None of these are charged to the maintainer per-call, which makes
    @founder economically sustainable without a token (per whitepaper §4.10).

The wire protocol is OpenAI Chat Completions, so we reuse the openai SDK.
"""
from __future__ import annotations
import os
from typing import Iterator, Optional

from sisoul.llm.base import LLMAdapter, LLMAdapterError

DEFAULT_BASE_URL = "http://127.0.0.1:4000/v1"
DEFAULT_MODEL = "free-pool"


class NewapiAdapter(LLMAdapter):
    """OpenAI-compatible adapter pointing at newapi gateway."""

    provider_name = "newapi-freepool"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("SISOUL_NEWAPI_API_KEY")
        self.base_url = base_url or os.environ.get("SISOUL_NEWAPI_BASE_URL", DEFAULT_BASE_URL)
        self.model = model or os.environ.get("SISOUL_NEWAPI_MODEL", DEFAULT_MODEL)
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            if not self.api_key:
                raise LLMAdapterError(
                    "newapi adapter requires SISOUL_NEWAPI_API_KEY (virtual key). "
                    "Set the env var or pass api_key=. See docs/FOUNDER-AGENT.md "
                    "for how the team's free-pool gateway is configured."
                )
            try:
                import openai
            except ImportError as e:
                raise LLMAdapterError(
                    "newapi adapter requires the 'openai' Python package. "
                    "Install with: pip install 'sisoul[llm]'"
                ) from e
            self._client = openai.OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def chat(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs,
    ) -> str:
        client = self._ensure_client()
        try:
            resp = client.chat.completions.create(
                model=model or self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            raise LLMAdapterError(f"newapi.chat failed: {type(e).__name__}: {e}") from e

    def chat_stream(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        **kwargs,
    ) -> Iterator[str]:
        client = self._ensure_client()
        try:
            stream = client.chat.completions.create(
                model=model or self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                **kwargs,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        except Exception as e:
            raise LLMAdapterError(f"newapi.stream failed: {type(e).__name__}: {e}") from e


__all__ = ["NewapiAdapter", "DEFAULT_BASE_URL", "DEFAULT_MODEL"]
