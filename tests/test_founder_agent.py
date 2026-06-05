"""Tests for sisoul founder-agent (vault + agent + daemon routes)."""
from __future__ import annotations
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def vault_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SISOUL_VAULT", str(tmp_path))
    fdir = tmp_path / "founder"
    (fdir / "cases").mkdir(parents=True)
    (fdir / "lessons").mkdir(parents=True)
    (fdir / "system_prompt.md").write_text(
        "You are sisoul's founder-agent. Cite cases."
    )
    case1 = {
        "id": "c1",
        "question": "Why no token?",
        "answer": "Per whitepaper section 4.10. Tokens cause governance capture. sisoul funds via grants.",
        "did_author": "did:key:z6MkFounder",
        "tags": ["governance", "no-token"],
        "created_at": "2026-06-05T00:00:00Z",
        "source": "whitepaper",
    }
    case2 = {
        "id": "c2",
        "question": "How does RSI safety work?",
        "answer": "SELF_PATH_GUARD prevents Godel self-mod. pytest gate. dry_run default true.",
        "did_author": "did:key:z6MkFounder",
        "tags": ["rsi", "safety"],
        "created_at": "2026-06-05T00:00:00Z",
    }
    (fdir / "cases/c1.json").write_text(json.dumps(case1))
    (fdir / "cases/c2.json").write_text(json.dumps(case2))
    lesson1 = {
        "id": "l1",
        "principle": "Token temptation is the default failure mode of Web3 infra projects.",
        "context": "Round 9 sprint discovered this.",
        "applies_to": ["governance", "design"],
        "established_at": "2026-06-05",
        "source": "round-9 sprint",
    }
    (fdir / "lessons/l1.json").write_text(json.dumps(lesson1))
    return tmp_path


def test_vault_loads_system_prompt(vault_dir):
    from sisoul.founder.vault import FounderVault

    vault = FounderVault()
    assert "founder-agent" in vault.system_prompt


def test_vault_loads_cases(vault_dir):
    from sisoul.founder.vault import FounderVault

    vault = FounderVault()
    cases = vault.all_cases()
    assert len(cases) == 2
    ids = {c.id for c in cases}
    assert ids == {"c1", "c2"}


def test_vault_loads_lessons(vault_dir):
    from sisoul.founder.vault import FounderVault

    vault = FounderVault()
    lessons = vault.all_lessons()
    assert len(lessons) == 1
    assert lessons[0].id == "l1"


def test_vault_skips_malformed_case(vault_dir):
    from sisoul.founder.vault import FounderVault

    (vault_dir / "founder/cases/bad.json").write_text("NOT JSON")
    vault = FounderVault()
    assert len(vault.all_cases()) == 2


def test_vault_recall_finds_topical_case(vault_dir):
    from sisoul.founder.vault import FounderVault

    vault = FounderVault()
    matches = vault.recall("token governance question", top_k=2)
    assert matches
    assert matches[0][0].id == "c1"


def test_vault_recall_empty_query(vault_dir):
    from sisoul.founder.vault import FounderVault

    vault = FounderVault()
    assert vault.recall("") == []


def test_agent_status_includes_vault_size(vault_dir):
    from sisoul.founder.agent import FounderAgent

    agent = FounderAgent()
    status = agent.status()
    assert status["vault_size"]["cases"] == 2
    assert status["vault_size"]["lessons"] == 1
    assert status["vault_size"]["has_system_prompt"] is True


def test_agent_chat_falls_back_to_retrieval(vault_dir):
    from sisoul.founder.agent import FounderAgent

    agent = FounderAgent()
    result = agent.chat("Why no token in sisoul?")
    assert result["mode"] == "retrieval-only"
    assert "section 4.10" in result["answer"]
    assert result["cases_recalled"] == ["c1"]


def test_agent_chat_with_real_adapter(vault_dir):
    from sisoul.founder.agent import FounderAgent

    class StubAdapter:
        provider_name = "stub-llm"

        def chat(self, messages, **kw):
            return f"Stub answer using {len(messages)} messages."

    agent = FounderAgent()
    result = agent.chat("Why no token?", adapter=StubAdapter())
    assert result["mode"] == "llm"
    assert result["provider"] == "stub-llm"
    assert "Stub answer" in result["answer"]


def test_agent_chat_logs_to_jsonl(vault_dir):
    from sisoul.founder.agent import FounderAgent

    agent = FounderAgent()
    agent.chat("Why no token?")
    log_file = vault_dir / "founder/chat/log.jsonl"
    assert log_file.exists()
    lines = log_file.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["mode"] == "retrieval-only"


def test_agent_adapter_error_falls_back(vault_dir):
    from sisoul.founder.agent import FounderAgent

    class BrokenAdapter:
        provider_name = "broken"

        def chat(self, messages, **kw):
            raise RuntimeError("provider down")

    agent = FounderAgent()
    result = agent.chat("Why no token?", adapter=BrokenAdapter())
    assert result["mode"] == "retrieval-only"
    assert "[retrieval-only" in result["answer"]


@pytest.fixture
def client(vault_dir):
    from sisoul.daemon_routes.founder import founder_router

    app = FastAPI()
    app.include_router(founder_router)
    return TestClient(app)


def test_route_status(client, vault_dir):
    r = client.get("/v1/founder/status")
    assert r.status_code == 200
    body = r.json()
    assert body["vault_size"]["cases"] == 2
    assert body["vault_size"]["lessons"] == 1


def test_route_chat_retrieval(client, vault_dir):
    r = client.post("/v1/founder/chat", json={"question": "Why no token?"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "retrieval-only"
    assert "section 4.10" in body["answer"]


def test_route_chat_validation(client, vault_dir):
    r = client.post("/v1/founder/chat", json={"question": ""})
    assert r.status_code == 422


def test_route_recall(client, vault_dir):
    r = client.post("/v1/founder/recall", json={"query": "rsi safety", "top_k": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "rsi safety"
    assert any(m["id"] == "c2" for m in body["matches"])


def test_route_cases_list(client, vault_dir):
    r = client.get("/v1/founder/cases")
    assert r.status_code == 200
    assert r.json()["count"] == 2


def test_route_lessons_list(client, vault_dir):
    r = client.get("/v1/founder/lessons")
    assert r.status_code == 200
    assert r.json()["count"] == 1
