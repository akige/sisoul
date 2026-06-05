"""tests for v3 RSI daemon HTTP endpoints (skeleton verification)."""
from __future__ import annotations
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from sisoul.daemon_routes.v3_rsi import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_rsi_status(client):
    r = client.get("/v3/rsi/status")
    assert r.status_code == 200
    j = r.json()
    assert j["framework"] == "sisoul-v3-rsi"
    assert j["version"].startswith("0.1.0")
    assert j["safety_boundary_active"] is True
    assert "components" in j
    # All 5 RSI modules must report 'loaded' (aws-us session A shipped them).
    for name in ("godel_agent", "alpha_evolve", "dspy_optimize", "evaluator", "federated_rsi"):
        assert name in j["components"]
        assert j["components"][name] == "loaded"


def test_rsi_iterate_godel(client):
    r = client.post("/v3/rsi/iterate", json={"mode": "godel", "dry_run": True})
    assert r.status_code == 200
    j = r.json()
    assert j["mode"] == "godel"
    assert j["iteration_id"].startswith("rsi-")
    assert j["accepted"] is False
    # pipeline reports either no-adapter (CI without API key) or skeleton reason
    assert "adapter" in j["reason"].lower() or "skeleton" in j["reason"]


def test_rsi_iterate_alpha_evolve(client):
    r = client.post("/v3/rsi/iterate", json={"mode": "alpha_evolve"})
    assert r.status_code == 200
    assert r.json()["mode"] == "alpha_evolve"


def test_rsi_iterate_dspy(client):
    r = client.post("/v3/rsi/iterate", json={"mode": "dspy"})
    assert r.status_code == 200
    assert r.json()["mode"] == "dspy"


def test_rsi_iterate_unknown_mode_400(client):
    r = client.post("/v3/rsi/iterate", json={"mode": "unknown"})
    assert r.status_code == 400


def test_rsi_history_empty(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SISOUL_VAULT", str(tmp_path))
    r = client.get("/v3/rsi/history")
    assert r.status_code == 200
    j = r.json()
    assert j["count"] == 0
    assert j["iterations"] == []


def test_rsi_history_from_jsonl(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SISOUL_VAULT", str(tmp_path))
    hist = tmp_path / "rsi" / "history.jsonl"
    hist.parent.mkdir(parents=True)
    rows = [
        {"iteration_id": "rsi-1", "mode": "godel", "started_at": "2026-06-05T00:00:00Z", "accepted": True, "fitness": 0.85},
        {"iteration_id": "rsi-2", "mode": "alpha_evolve", "started_at": "2026-06-05T00:01:00Z", "accepted": False, "fitness": 0.40},
    ]
    hist.write_text("\n".join(json.dumps(r) for r in rows))

    r = client.get("/v3/rsi/history")
    assert r.status_code == 200
    j = r.json()
    assert j["count"] == 2
    assert j["iterations"][0]["iteration_id"] == "rsi-1"
    assert j["iterations"][0]["accepted"] is True


def test_rsi_history_skips_malformed_lines(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SISOUL_VAULT", str(tmp_path))
    hist = tmp_path / "rsi" / "history.jsonl"
    hist.parent.mkdir(parents=True)
    hist.write_text(
        '{"iteration_id":"rsi-1","mode":"godel","started_at":"2026-06-05T00:00:00Z","accepted":true,"fitness":0.5}\n'
        'NOT_JSON_LINE\n'
        '{"iteration_id":"rsi-2","mode":"dspy","started_at":"2026-06-05T00:01:00Z","accepted":false,"fitness":null}\n'
    )
    r = client.get("/v3/rsi/history")
    assert r.status_code == 200
    assert r.json()["count"] == 2  # malformed line silently skipped


def test_rsi_gossip_without_transport_returns_error(client):
    r = client.post("/v3/rsi/gossip", json={"mutation": {"kind": "prompt", "code": "x"}})
    assert r.status_code == 200
    j = r.json()
    assert j["broadcast"] is False
    assert "transport" in j["error"].lower()


def test_rsi_peers_empty(client):
    r = client.get("/v3/rsi/peers")
    assert r.status_code == 200
    j = r.json()
    assert j["count"] == 0
    assert j["peer_mutations"] == []


def test_rsi_status_all_5_modules_loaded(client):
    """Sanity: all 5 RSI module imports succeed."""
    r = client.get("/v3/rsi/status")
    j = r.json()
    loaded = [name for name, state in j["components"].items() if state == "loaded"]
    assert len(loaded) == 5, f"expected 5 modules loaded, got {loaded}"
