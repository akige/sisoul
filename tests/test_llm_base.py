"""tests/test_llm_base.py — LLMAdapter 抽象接口测试 (Phase 1 W5-W6).

测试 base class 契约:
- 抽象方法不能直接实例化
- LLMAdapterError 携带正确字段
- embed() 默认抛 NotImplementedError
- __repr__ 格式正确
- provider_name 格式正确
- get_adapter() 工厂函数正确映射
"""

from __future__ import annotations

import pytest

from sisoul.llm.base import LLMAdapter, LLMAdapterError
from sisoul.llm import (
    AnthropicAdapter,
    OpenAIAdapter,
    GeminiAdapter,
    OllamaAdapter,
    OpenRouterAdapter,
    get_adapter,
    PROVIDER_ALIASES,
)


# ---------------------------------------------------------------------------
# 具体子类用于测试 base 行为 (最小实现)
# ---------------------------------------------------------------------------

class _ConcreteAdapter(LLMAdapter):
    """可实例化的最小实现 (测试 base class 专用)."""

    DEFAULT_MODEL = "test-model"

    def chat(self, messages: list[dict], **kwargs) -> str:
        return "ok"

    def chat_stream(self, messages: list[dict], **kwargs):
        yield "ok"


# ---------------------------------------------------------------------------
# abstract class 测试
# ---------------------------------------------------------------------------

class TestLLMAdapterAbstract:
    def test_cannot_instantiate_abstract(self):
        """LLMAdapter 是抽象类, 不能直接实例化."""
        with pytest.raises(TypeError):
            LLMAdapter()  # type: ignore[abstract]

    def test_concrete_subclass_instantiable(self):
        """实现了 chat + chat_stream 的子类可以实例化."""
        adapter = _ConcreteAdapter()
        assert adapter is not None

    def test_default_model_used_when_none(self):
        adapter = _ConcreteAdapter()
        assert adapter.model == "test-model"

    def test_model_override(self):
        adapter = _ConcreteAdapter(model="custom-model")
        assert adapter.model == "custom-model"

    def test_api_key_stored(self):
        adapter = _ConcreteAdapter(api_key="test-key-123")
        assert adapter.api_key == "test-key-123"

    def test_api_key_none_default(self):
        adapter = _ConcreteAdapter()
        assert adapter.api_key is None

    def test_embed_raises_not_implemented(self):
        adapter = _ConcreteAdapter()
        with pytest.raises(NotImplementedError):
            adapter.embed("hello")

    def test_provider_name(self):
        adapter = _ConcreteAdapter()
        # _ConcreteAdapter → provider_name = "_concrete"
        assert "concrete" in adapter.provider_name.lower()

    def test_repr_contains_model(self):
        adapter = _ConcreteAdapter(model="my-model")
        r = repr(adapter)
        assert "my-model" in r
        assert "_ConcreteAdapter" in r


# ---------------------------------------------------------------------------
# LLMAdapterError 测试
# ---------------------------------------------------------------------------

class TestLLMAdapterError:
    def test_basic_message(self):
        err = LLMAdapterError("something failed")
        assert "something failed" in str(err)

    def test_provider_included_in_str(self):
        err = LLMAdapterError("api error", provider="anthropic")
        s = str(err)
        assert "[anthropic]" in s
        assert "api error" in s

    def test_cause_included_in_str(self):
        original = ValueError("original cause")
        err = LLMAdapterError("wrapper error", cause=original)
        s = str(err)
        assert "original cause" in s or "ValueError" in s

    def test_all_fields(self):
        original = RuntimeError("original")
        err = LLMAdapterError("msg", provider="openai", cause=original)
        assert err.provider == "openai"
        assert err.cause is original
        assert "openai" in str(err)

    def test_is_exception(self):
        err = LLMAdapterError("test")
        assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# chat / chat_stream 接口测试 (用 _ConcreteAdapter)
# ---------------------------------------------------------------------------

class TestAdapterInterface:
    def test_chat_returns_string(self):
        adapter = _ConcreteAdapter()
        result = adapter.chat([{"role": "user", "content": "hello"}])
        assert isinstance(result, str)

    def test_chat_stream_yields_strings(self):
        adapter = _ConcreteAdapter()
        chunks = list(adapter.chat_stream([{"role": "user", "content": "hello"}]))
        assert all(isinstance(c, str) for c in chunks)
        assert len(chunks) > 0


# ---------------------------------------------------------------------------
# get_adapter() 工厂函数测试
# ---------------------------------------------------------------------------

class TestGetAdapter:
    def test_get_anthropic_by_claude(self):
        adapter = get_adapter("claude")
        assert isinstance(adapter, AnthropicAdapter)

    def test_get_anthropic_by_anthropic_alias(self):
        adapter = get_adapter("anthropic")
        assert isinstance(adapter, AnthropicAdapter)

    def test_get_openai_by_openai(self):
        adapter = get_adapter("openai")
        assert isinstance(adapter, OpenAIAdapter)

    def test_get_openai_by_gpt_alias(self):
        adapter = get_adapter("gpt")
        assert isinstance(adapter, OpenAIAdapter)

    def test_get_gemini(self):
        adapter = get_adapter("gemini")
        assert isinstance(adapter, GeminiAdapter)

    def test_get_gemini_by_google_alias(self):
        adapter = get_adapter("google")
        assert isinstance(adapter, GeminiAdapter)

    def test_get_ollama(self):
        adapter = get_adapter("ollama")
        assert isinstance(adapter, OllamaAdapter)

    def test_get_ollama_by_local_alias(self):
        adapter = get_adapter("local")
        assert isinstance(adapter, OllamaAdapter)

    def test_get_openrouter(self):
        adapter = get_adapter("openrouter")
        assert isinstance(adapter, OpenRouterAdapter)

    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValueError, match="未知 LLM provider"):
            get_adapter("unknown-provider-xyz")

    def test_api_key_passed_to_adapter(self):
        adapter = get_adapter("claude", api_key="test-key")
        assert adapter.api_key == "test-key"

    def test_model_passed_to_adapter(self):
        adapter = get_adapter("openai", model="gpt-4o-mini")
        assert adapter.model == "gpt-4o-mini"

    def test_case_insensitive(self):
        adapter = get_adapter("CLAUDE")
        assert isinstance(adapter, AnthropicAdapter)

    def test_case_insensitive_openai(self):
        adapter = get_adapter("OpenAI")
        assert isinstance(adapter, OpenAIAdapter)

    def test_provider_aliases_dict_populated(self):
        assert len(PROVIDER_ALIASES) >= 9  # 至少 9 个别名


# ---------------------------------------------------------------------------
# 各 adapter 默认 model 测试
# ---------------------------------------------------------------------------

class TestAdapterDefaults:
    def test_anthropic_default_model(self):
        assert AnthropicAdapter.DEFAULT_MODEL == "claude-opus-4-7"

    def test_openai_default_model(self):
        assert OpenAIAdapter.DEFAULT_MODEL == "gpt-4o"

    def test_gemini_default_model(self):
        assert GeminiAdapter.DEFAULT_MODEL == "gemini-2.5-pro"

    def test_ollama_default_model(self):
        assert OllamaAdapter.DEFAULT_MODEL == "llama3.2"

    def test_openrouter_default_model(self):
        assert OpenRouterAdapter.DEFAULT_MODEL == "openai/gpt-4o"

    def test_adapters_have_default_model(self):
        for cls in [AnthropicAdapter, OpenAIAdapter, GeminiAdapter, OllamaAdapter, OpenRouterAdapter]:
            assert cls.DEFAULT_MODEL, f"{cls.__name__} 没有 DEFAULT_MODEL"
