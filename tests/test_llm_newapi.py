"""Tests for NewapiAdapter (founder-agent LLM backend, free-pool gateway)."""
from __future__ import annotations
import pytest


def test_newapi_adapter_provider_name():
    from sisoul.llm.newapi import NewapiAdapter

    adapter = NewapiAdapter(api_key="test")
    assert adapter.provider_name == "newapi-freepool"


def test_newapi_adapter_defaults_from_env(monkeypatch):
    monkeypatch.delenv("SISOUL_NEWAPI_API_KEY", raising=False)
    monkeypatch.delenv("SISOUL_NEWAPI_BASE_URL", raising=False)
    monkeypatch.delenv("SISOUL_NEWAPI_MODEL", raising=False)
    from sisoul.llm.newapi import NewapiAdapter, DEFAULT_BASE_URL, DEFAULT_MODEL

    adapter = NewapiAdapter()
    assert adapter.base_url == DEFAULT_BASE_URL
    assert adapter.model == DEFAULT_MODEL
    assert adapter.api_key is None


def test_newapi_adapter_env_override(monkeypatch):
    monkeypatch.setenv("SISOUL_NEWAPI_API_KEY", "test-key-1234")
    monkeypatch.setenv("SISOUL_NEWAPI_BASE_URL", "https://gateway.example.com/v1")
    monkeypatch.setenv("SISOUL_NEWAPI_MODEL", "custom-model")
    from sisoul.llm.newapi import NewapiAdapter

    adapter = NewapiAdapter()
    assert adapter.api_key == "test-key-1234"
    assert adapter.base_url == "https://gateway.example.com/v1"
    assert adapter.model == "custom-model"


def test_newapi_adapter_explicit_args_override_env(monkeypatch):
    monkeypatch.setenv("SISOUL_NEWAPI_API_KEY", "env-key")
    from sisoul.llm.newapi import NewapiAdapter

    adapter = NewapiAdapter(api_key="explicit-key")
    assert adapter.api_key == "explicit-key"


def test_newapi_adapter_no_api_key_raises_on_use(monkeypatch):
    monkeypatch.delenv("SISOUL_NEWAPI_API_KEY", raising=False)
    from sisoul.llm.newapi import NewapiAdapter
    from sisoul.llm.base import LLMAdapterError

    adapter = NewapiAdapter()
    with pytest.raises(LLMAdapterError) as exc_info:
        adapter.chat([{"role": "user", "content": "hi"}])
    assert "SISOUL_NEWAPI_API_KEY" in str(exc_info.value)


def test_newapi_adapter_chat_mocked_openai_call(monkeypatch):
    from sisoul.llm.newapi import NewapiAdapter

    adapter = NewapiAdapter(api_key="test-key")

    class FakeChoice:
        def __init__(self, content):
            self.message = type("M", (), {"content": content})()

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    class FakeChatCompletions:
        def create(self, **kw):
            assert kw["model"] == "free-pool"
            assert kw["messages"][0]["role"] == "user"
            return FakeResponse("Hello from newapi")

    class FakeChat:
        completions = FakeChatCompletions()

    class FakeClient:
        chat = FakeChat()

    # Inject fake client to avoid hitting real network
    adapter._client = FakeClient()

    result = adapter.chat([{"role": "user", "content": "test"}])
    assert result == "Hello from newapi"


def test_founder_agent_default_adapter_resolves_newapi(monkeypatch):
    """FounderAgent without explicit adapter and provider=newapi-freepool resolves NewapiAdapter."""
    monkeypatch.setenv("SISOUL_NEWAPI_API_KEY", "test-stub-key")
    from sisoul.founder.agent import FounderAgent, FounderConfig
    from sisoul.llm.newapi import NewapiAdapter

    cfg = FounderConfig(provider="newapi-freepool")
    agent = FounderAgent(config=cfg)
    adapter = agent._default_adapter()
    assert isinstance(adapter, NewapiAdapter)
    assert adapter.api_key == "test-stub-key"


def test_founder_agent_default_adapter_unknown_provider_returns_none():
    from sisoul.founder.agent import FounderAgent, FounderConfig

    cfg = FounderConfig(provider="unknown-provider-xyz")
    agent = FounderAgent(config=cfg)
    assert agent._default_adapter() is None
