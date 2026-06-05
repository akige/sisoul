"""Tests for v2 daemon routes (case_graph + skill_marketplace HTTP API)."""
from __future__ import annotations
import os
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from sisoul.daemon_routes.v2_case import router as case_router
from sisoul.daemon_routes.v2_skill import router as skill_router


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SISOUL_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("SISOUL_SKILLS_DIR", str(tmp_path / "skills"))
    a = FastAPI()
    a.include_router(case_router)
    a.include_router(skill_router)
    return TestClient(a)


# Case routes
def test_v2_case_add_and_get(app):
    r = app.post("/v2/case", json={
        "question": "rust async",
        "answer": "use tokio::select",
        "did_author": "did:key:z6MkAlice",
        "tags": ["rust"],
    })
    assert r.status_code == 200
    case_id = r.json()["id"]
    g = app.get(f"/v2/case/{case_id}")
    assert g.status_code == 200
    assert g.json()["question"] == "rust async"


def test_v2_case_search(app):
    for q in ["rust async tokio", "python asyncio", "rust borrow"]:
        app.post("/v2/case", json={
            "question": q, "answer": "stub", "did_author": "did:key:z6MkX",
        })
    r = app.get("/v2/case/search/?q=rust")
    assert r.status_code == 200
    data = r.json()
    assert data["is_hit"] is True
    assert data["query"] == "rust"


def test_v2_case_list(app):
    for i in range(3):
        app.post("/v2/case", json={
            "question": f"q{i}", "answer": "a", "did_author": "did:key:z6MkY",
        })
    r = app.get("/v2/case")
    assert r.status_code == 200
    assert r.json()["count"] == 3


def test_v2_case_404(app):
    r = app.get("/v2/case/nonexistent-id")
    assert r.status_code == 404


def test_v2_case_invalid_did(app):
    r = app.post("/v2/case", json={
        "question": "q", "answer": "a", "did_author": "not-a-did",
    })
    assert r.status_code == 400


# Skill routes
def test_v2_skill_install(app):
    r = app.post("/v2/skill/install", json={
        "name": "my-skill", "version": "0.1.0", "entry": "main.py", "runtime": "python",
        "ipfs_cid": "bafyabcdef", "author_did": "did:key:z6MkBob", "sigstore_sig": "sig",
        "skip_sigstore": True,
    })
    assert r.status_code == 200
    assert r.json()["skill_name"] == "my-skill"


def test_v2_skill_install_invalid_cid(app):
    r = app.post("/v2/skill/install", json={
        "name": "bad", "version": "0.1.0", "entry": "m.py", "runtime": "python",
        "ipfs_cid": "not-a-cid", "author_did": "did:key:z6Mk", "sigstore_sig": "",
    })
    assert r.status_code == 400


def test_v2_skill_list_after_install(app):
    app.post("/v2/skill/install", json={
        "name": "s1", "version": "0.1.0", "entry": "m.py", "runtime": "python",
        "ipfs_cid": "bafyX", "author_did": "did:key:z6MkX", "sigstore_sig": "s",
        "skip_sigstore": True,
    })
    r = app.get("/v2/skill/list")
    assert r.status_code == 200
    assert "s1" in r.json()["skills"]


def test_v2_skill_uninstall(app):
    app.post("/v2/skill/install", json={
        "name": "sX", "version": "0.1.0", "entry": "m.py", "runtime": "python",
        "ipfs_cid": "bafyZ", "author_did": "did:key:z6MkX", "sigstore_sig": "s",
        "skip_sigstore": True,
    })
    r = app.delete("/v2/skill/sX")
    assert r.status_code == 200
    r2 = app.delete("/v2/skill/sX")
    assert r2.status_code == 404


# ──────────────────────────────────────────────────────────────────────────────
# v2_more (provenance attest / debate / reputation) routes
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def v2_more_app():
    from sisoul.daemon_routes.v2_more import router
    from fastapi import FastAPI
    a = FastAPI()
    a.include_router(router)
    return TestClient(a)


def test_v2_provenance_attest(v2_more_app):
    r = v2_more_app.post("/v2/provenance/attest", json={
        "response_id": "r1",
        "query": "q",
        "answer": "a",
        "did_answerer": "did:key:z6MkAlice",
        "cited_cases": [{"source_id": "case-1", "did_author": "did:key:z6MkBob"}],
        "network": "mock",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["attestation_uid"].startswith("mock:")
    assert data["citation_count"] == 1


def test_v2_provenance_attest_invalid_did(v2_more_app):
    r = v2_more_app.post("/v2/provenance/attest", json={
        "response_id": "r", "query": "q", "answer": "a", "did_answerer": "not-did",
        "cited_cases": [],
    })
    assert r.status_code == 400


def test_v2_debate_run(v2_more_app):
    r = v2_more_app.post("/v2/debate/run", json={
        "query": "How to fix Rust async deadlock?",
        "agents": [
            {"did": "did:key:z6MkA", "petname": "Alice", "topic_reputation": 0.5},
            {"did": "did:key:z6MkB", "petname": "Bob", "topic_reputation": 0.8},
            {"did": "did:key:z6MkC", "petname": "Carol", "topic_reputation": 0.7},
        ],
    })
    assert r.status_code == 200
    data = r.json()
    assert "stub synthesized" in data["final_answer"]
    assert data["n_rounds"] == 9  # 3 agents × 3 rounds


def test_v2_debate_too_few_agents(v2_more_app):
    r = v2_more_app.post("/v2/debate/run", json={
        "query": "q",
        "agents": [{"did": "did:key:z6MkA"}],
    })
    assert r.status_code == 400


def test_v2_reputation_update_and_top_k(v2_more_app):
    # update Bob & Carol's rep on rust
    v2_more_app.post("/v2/reputation/update", json={"did": "did:key:z6MkRustExpertA", "topic": "rust", "score_delta": 0.4})
    v2_more_app.post("/v2/reputation/update", json={"did": "did:key:z6MkRustExpertB", "topic": "rust", "score_delta": 0.3})

    r = v2_more_app.post("/v2/reputation/top-k", json={
        "query": "How to use tokio",
        "topic": "rust",
        "candidates": ["did:key:z6MkRustExpertA", "did:key:z6MkRustExpertB", "did:key:z6MkNoob"],
        "top_k": 2,
    })
    assert r.status_code == 200
    data = r.json()
    assert "did:key:z6MkRustExpertA" in data["picked"]


# ──────────────────────────────────────────────────────────────────────────────
# v2 growth + lesson routes
# ──────────────────────────────────────────────────────────────────────────────


def test_v2_growth_write_and_last(v2_more_app, tmp_path, monkeypatch):
    monkeypatch.setenv("SISOUL_VAULT", str(tmp_path / "vault"))
    for i, day in enumerate(["2026-05-29", "2026-05-30", "2026-05-31"]):
        r = v2_more_app.post("/v2/growth/write", json={
            "date": day, "cases_added": i + 1, "chats_sent": (i + 1) * 2,
        })
        assert r.status_code == 200
    r = v2_more_app.get("/v2/growth/last?n=7")
    assert r.status_code == 200
    data = r.json()
    assert data["total_cases"] == 1 + 2 + 3
    assert data["avg_chats_per_day"] == (2 + 4 + 6) / 3


def test_v2_lesson_distill(v2_more_app):
    r = v2_more_app.post("/v2/lesson/distill", json={
        "did_owner": "did:key:z6MkAlice",
        "source_case_ids": ["case-a", "case-b", "case-c"],
        "topic": "rust",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["id"].startswith("lesson-")
    assert "rust" in data["title"]


def test_v2_lesson_distill_invalid_did(v2_more_app):
    r = v2_more_app.post("/v2/lesson/distill", json={
        "did_owner": "not-did", "source_case_ids": ["a", "b"],
    })
    assert r.status_code == 400


def test_v2_lesson_distill_too_few_cases(v2_more_app):
    r = v2_more_app.post("/v2/lesson/distill", json={
        "did_owner": "did:key:z6MkA", "source_case_ids": ["only-one"],
    })
    assert r.status_code == 400
