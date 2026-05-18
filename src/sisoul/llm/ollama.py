"""sisoul LLM adapter · Ollama 本地 (Phase 1 W6).

官方 ollama Python client 封装.
默认 model: llama3.2 (最常用本地模型, 轻量).
无 api_key (本地 daemon).
base_url 默认 http://localhost:11434 (ollama 官方默认端口).

TODO: 如果用户改了 ollama 端口, 可通过 OLLAMA_HOST env 或 base_url 参数传入.
"""

from __future__ import annotations

from typing import Iterator

from sisoul.llm.base import LLMAdapter, LLMAdapterError


class OllamaAdapter(LLMAdapter):
    """Ollama 本地 LLM adapter (官方 ollama Python client).

    支持任何 ollama 已 pull 的 model:
    - llama3.2 (默认, 3B 轻量)
    - llama3.1:8b
    - mistral
    - deepseek-r1 等

    无 api_key (本地 daemon 无 auth).

    用法:
        adapter = OllamaAdapter()  # 连 localhost:11434
        response = adapter.chat([{"role": "user", "content": "hello"}])
    """

    DEFAULT_MODEL = "llama3.2"
    DEFAULT_BASE_URL = "http://localhost:11434"

    def __init__(
        self,
        api_key: str | None = None,  # 保留签名兼容 LLMAdapter, 实际不用
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(api_key=None, model=model)  # ollama 无 api_key
        self.base_url = base_url or self.DEFAULT_BASE_URL
        self._client = None

    def _get_client(self):
        """懒加载 ollama.Client."""
        if self._client is None:
            try:
                import ollama
            except ImportError as e:
                raise LLMAdapterError(
                    "ollama SDK 未安装. 请: pip install 'sisoul[llm]'",
                    provider="ollama",
                    cause=e,
                ) from e
            self._client = ollama.Client(host=self.base_url)
        return self._client

    def chat(self, messages: list[dict], **kwargs) -> str:
        """同步 chat via ollama.

        Args:
            messages: list[{"role": ..., "content": ...}] (system/user/assistant 均支持)
            **kwargs:
                温度等 options (传给 ollama options)
                stream=False (默认)

        Returns:
            assistant 回复字符串

        Raises:
            LLMAdapterError: ollama daemon 未运行 / 模型未 pull / 连接错误
        """
        client = self._get_client()

        # ollama options (temperature 等)
        options: dict = {}
        if "temperature" in kwargs:
            options["temperature"] = kwargs["temperature"]
        if "num_predict" in kwargs:
            options["num_predict"] = kwargs["num_predict"]
        # max_tokens → num_predict
        if "max_tokens" in kwargs and "num_predict" not in options:
            options["num_predict"] = kwargs["max_tokens"]

        try:
            response = client.chat(
                model=self.model,
                messages=messages,
                **({"options": options} if options else {}),
            )
            # ollama response: {"message": {"role": "assistant", "content": "..."}}
            return response["message"]["content"]
        except Exception as e:
            # 连接错误提示用户先 `ollama serve`
            err_str = str(e).lower()
            if "connection" in err_str or "refused" in err_str or "errno" in err_str:
                raise LLMAdapterError(
                    f"Ollama daemon 未运行 (连不上 {self.base_url}). "
                    "请先运行: ollama serve",
                    provider="ollama",
                    cause=e,
                ) from e
            raise LLMAdapterError(
                f"Ollama error: {e}",
                provider="ollama",
                cause=e,
            ) from e

    def chat_stream(self, messages: list[dict], **kwargs) -> Iterator[str]:
        """流式 chat. yield delta text chunks.

        Args:
            messages: 同 chat()
            **kwargs: temperature / max_tokens / num_predict

        Yields:
            str delta chunks
        """
        client = self._get_client()

        options: dict = {}
        if "temperature" in kwargs:
            options["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            options["num_predict"] = kwargs["max_tokens"]

        try:
            stream = client.chat(
                model=self.model,
                messages=messages,
                stream=True,
                **({"options": options} if options else {}),
            )
            for chunk in stream:
                delta = chunk.get("message", {}).get("content", "")
                if delta:
                    yield delta
        except Exception as e:
            raise LLMAdapterError(
                f"Ollama stream error: {e}",
                provider="ollama",
                cause=e,
            ) from e
