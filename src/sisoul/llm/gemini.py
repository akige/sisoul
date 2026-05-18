"""sisoul LLM adapter · Google Gemini (Phase 1 W6).

官方 google-generativeai SDK 封装.
默认 model: gemini-2.5-pro.
api_key: 优先 __init__ 传入, 其次读 GEMINI_API_KEY env.

注意: google-generativeai messages 格式跟 OpenAI 略有区别:
- role 只有 "user" 和 "model" (不是 "assistant")
- system 用 system_instruction 参数

TODO: google-generativeai >= 0.8 开始部分 API 有变化, 锁 0.5-0.7 范围.
"""

from __future__ import annotations

import os
from typing import Iterator

from sisoul.llm.base import LLMAdapter, LLMAdapterError


class GeminiAdapter(LLMAdapter):
    """Google Gemini adapter (官方 google-generativeai SDK).

    支持 model:
    - gemini-2.5-pro (默认)
    - gemini-1.5-pro
    - gemini-1.5-flash
    - gemini-pro

    用法:
        adapter = GeminiAdapter()  # 读 GEMINI_API_KEY
        response = adapter.chat([{"role": "user", "content": "hello"}])
    """

    DEFAULT_MODEL = "gemini-2.5-pro"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        super().__init__(api_key=api_key, model=model)
        self._genai = None

    def _get_genai(self):
        """懒加载 google.generativeai + configure API key."""
        if self._genai is None:
            try:
                import google.generativeai as genai
            except ImportError as e:
                raise LLMAdapterError(
                    "google-generativeai SDK 未安装. 请: pip install 'sisoul[llm]'",
                    provider="gemini",
                    cause=e,
                ) from e

            key = self.api_key or os.environ.get("GEMINI_API_KEY")
            if not key:
                raise LLMAdapterError(
                    "GEMINI_API_KEY 未设置. 请 sisoul login --provider gemini 或 "
                    "export GEMINI_API_KEY=AIza...",
                    provider="gemini",
                )
            genai.configure(api_key=key)
            self._genai = genai
        return self._genai

    def _build_gemini_messages(
        self, messages: list[dict]
    ) -> tuple[str | None, list[dict]]:
        """把 OpenAI 格式 messages 转成 Gemini 格式.

        Returns:
            (system_instruction, gemini_messages)
            Gemini role: "user" | "model" (不是 "assistant")
        """
        system_instruction = None
        gemini_messages = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                system_instruction = content
            elif role == "assistant":
                gemini_messages.append({"role": "model", "parts": [content]})
            else:
                gemini_messages.append({"role": "user", "parts": [content]})
        return system_instruction, gemini_messages

    def chat(self, messages: list[dict], **kwargs) -> str:
        """同步 chat.

        Args:
            messages: list[{"role": ..., "content": ...}]
            **kwargs:
                max_tokens (int): 最大 output token 数 (映射到 max_output_tokens)
                temperature (float): 默认 1.0

        Returns:
            assistant 回复字符串

        Raises:
            LLMAdapterError: Gemini API 错误
        """
        genai = self._get_genai()
        system_instruction, gemini_messages = self._build_gemini_messages(messages)

        generation_config: dict = {}
        if "max_tokens" in kwargs:
            generation_config["max_output_tokens"] = kwargs["max_tokens"]
        if "temperature" in kwargs:
            generation_config["temperature"] = kwargs["temperature"]

        try:
            model_kwargs: dict = dict(model_name=self.model)
            if system_instruction:
                model_kwargs["system_instruction"] = system_instruction
            if generation_config:
                model_kwargs["generation_config"] = generation_config

            model = genai.GenerativeModel(**model_kwargs)
            response = model.generate_content(gemini_messages)
            return response.text
        except Exception as e:
            raise LLMAdapterError(
                f"Gemini API error: {e}",
                provider="gemini",
                cause=e,
            ) from e

    def chat_stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        """流式 chat. yield delta text chunks.

        Args:
            messages: 同 chat()
            **kwargs: max_tokens / temperature

        Yields:
            str delta chunks
        """
        genai = self._get_genai()
        system_instruction, gemini_messages = self._build_gemini_messages(messages)

        generation_config: dict = {}
        if "max_tokens" in kwargs:
            generation_config["max_output_tokens"] = kwargs["max_tokens"]
        if "temperature" in kwargs:
            generation_config["temperature"] = kwargs["temperature"]

        try:
            model_kwargs: dict = dict(model_name=self.model)
            if system_instruction:
                model_kwargs["system_instruction"] = system_instruction
            if generation_config:
                model_kwargs["generation_config"] = generation_config

            model = genai.GenerativeModel(**model_kwargs)
            response = model.generate_content(gemini_messages, stream=True)
            for chunk in response:
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            raise LLMAdapterError(
                f"Gemini stream error: {e}",
                provider="gemini",
                cause=e,
            ) from e
