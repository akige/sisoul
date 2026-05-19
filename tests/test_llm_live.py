"""tests/test_llm_live.py — LLM provider 真实 API 集成测试.

默认全部 SKIP. 设 SISOUL_TEST_LIVE=1 才跑真 API.

用法:
    SISOUL_TEST_LIVE=1 pytest tests/test_llm_live.py -v

需要设置对应 env var:
    ANTHROPIC_API_KEY=sk-ant-...
    OPENAI_API_KEY=sk-...
    GEMINI_API_KEY=AIza...
    OPENROUTER_API_KEY=sk-or-...
    # ollama 需要本地运行: ollama serve

每个 test 调真 API, 验证:
1. chat() 返回非空字符串
2. chat_stream() yield 至少 1 个 chunk
3. 数值 sanity: 全 0 / 全空 → FAIL (§J-2 真验收)
"""

from __future__ import annotations

import os
import pytest

# 真 API 测试默认 SKIP
LIVE = os.environ.get("SISOUL_TEST_LIVE", "").lower() in ("1", "true", "yes")
skip_unless_live = pytest.mark.skipif(not LIVE, reason="SISOUL_TEST_LIVE=1 才跑真 API")


@skip_unless_live
class TestAnthropicLive:
    def test_chat_pong(self):
        from sisoul.llm.anthropic import AnthropicAdapter
        adapter = AnthropicAdapter()  # 读 ANTHROPIC_API_KEY
        response = adapter.chat(
            [{"role": "user", "content": "say 'pong' exactly"}],
            max_tokens=10,
        )
        assert response, "response 不能为空"
        assert len(response) > 0
        print(f"\n[Anthropic live] response: {response!r}")

    def test_chat_stream_yields(self):
        from sisoul.llm.anthropic import AnthropicAdapter
        adapter = AnthropicAdapter()
        chunks = list(adapter.chat_stream(
            [{"role": "user", "content": "say 'hello'"}],
            max_tokens=10,
        ))
        assert len(chunks) > 0, "stream 必须 yield 至少 1 chunk"
        full = "".join(chunks)
        assert full.strip(), "stream 全文不能为空"
        print(f"\n[Anthropic live stream] chunks={len(chunks)}, full={full!r}")


@skip_unless_live
class TestOpenAILive:
    def test_chat_pong(self):
        from sisoul.llm.openai import OpenAIAdapter
        adapter = OpenAIAdapter()
        response = adapter.chat(
            [{"role": "user", "content": "say 'pong' exactly"}],
            max_tokens=10,
        )
        assert response, "response 不能为空"
        print(f"\n[OpenAI live] response: {response!r}")

    def test_embed_returns_vector(self):
        from sisoul.llm.openai import OpenAIAdapter
        adapter = OpenAIAdapter()
        vec = adapter.embed("hello world")
        assert len(vec) > 0, "embedding 必须非空"
        assert any(v != 0.0 for v in vec), "embedding 不能全 0 (sanity check)"
        print(f"\n[OpenAI live embed] dim={len(vec)}, first3={vec[:3]}")


@skip_unless_live
class TestGeminiLive:
    def test_chat_pong(self):
        from sisoul.llm.gemini import GeminiAdapter
        adapter = GeminiAdapter()
        response = adapter.chat(
            [{"role": "user", "content": "say 'pong' exactly"}],
            max_tokens=10,
        )
        assert response, "response 不能为空"
        print(f"\n[Gemini live] response: {response!r}")


@skip_unless_live
class TestOllamaLive:
    def test_chat_pong(self):
        from sisoul.llm.ollama import OllamaAdapter
        adapter = OllamaAdapter()
        try:
            response = adapter.chat(
                [{"role": "user", "content": "say 'pong' exactly, one word"}],
                max_tokens=10,
            )
            assert response, "response 不能为空"
            print(f"\n[Ollama live] response: {response!r}")
        except Exception as e:
            if "ollama serve" in str(e):
                pytest.skip("ollama daemon 未运行, 请 `ollama serve`")
            raise


@skip_unless_live
class TestOpenRouterLive:
    def test_chat_pong(self):
        from sisoul.llm.openrouter import OpenRouterAdapter
        adapter = OpenRouterAdapter()  # 用 openai/gpt-4o 默认
        response = adapter.chat(
            [{"role": "user", "content": "say 'pong' exactly"}],
            max_tokens=10,
        )
        assert response, "response 不能为空"
        print(f"\n[OpenRouter live] response: {response!r}")
