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
