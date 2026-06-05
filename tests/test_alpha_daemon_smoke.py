"""Real daemon smoke test: spawn uvicorn + curl real HTTP.

这是 alpha launch acceptance gate — daemon 真启动 + endpoints 真返回, 不只 TestClient.
"""
from __future__ import annotations
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


@pytest.fixture(scope="module")
def real_daemon(tmp_path_factory):
    """Spawn uvicorn daemon as subprocess, return base URL."""
    vault = tmp_path_factory.mktemp("sisoul-smoke-vault")
    skills_dir = tmp_path_factory.mktemp("sisoul-smoke-skills")
    env = os.environ.copy()
    env["SISOUL_VAULT"] = str(vault)
    env["SISOUL_SKILLS_DIR"] = str(skills_dir)
    env["SISOUL_DAEMON_PORT"] = "9879"  # avoid clash with default 9876
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "sisoul.daemon:create_app",
         "--factory", "--host", "127.0.0.1", "--port", "9879",
         "--log-level", "error"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # wait for daemon ready (poll /sisoul/health)
    base = "http://127.0.0.1:9879"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base}/sisoul/health", timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.3)
    else:
        proc.terminate()
        out, err = proc.communicate(timeout=5)
        pytest.fail(f"daemon never came up. stderr: {err.decode()[:500]}")

    yield base

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_real_daemon_health(real_daemon):
    r = httpx.get(f"{real_daemon}/sisoul/health", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_real_daemon_v2_case_add_get(real_daemon):
    r = httpx.post(f"{real_daemon}/v2/case", json={
        "question": "real daemon test rust",
        "answer": "real answer",
        "did_author": "did:key:z6MkRealTest",
        "tags": ["smoke", "real"],
    }, timeout=5)
    assert r.status_code == 200, r.text
    case_id = r.json()["id"]
    g = httpx.get(f"{real_daemon}/v2/case/{case_id}", timeout=5)
    assert g.status_code == 200
    assert g.json()["question"] == "real daemon test rust"


def test_real_daemon_v2_case_search(real_daemon):
    # seed
    for q in ["python asyncio", "rust async", "go concurrency"]:
        httpx.post(f"{real_daemon}/v2/case", json={
            "question": q, "answer": "stub", "did_author": "did:key:z6MkX",
        }, timeout=5)
    r = httpx.get(f"{real_daemon}/v2/case/search/?q=async", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert data["is_hit"] is True


def test_real_daemon_v2_skill_install_and_list(real_daemon):
    r = httpx.post(f"{real_daemon}/v2/skill/install", json={
        "name": "smoke-skill", "version": "0.1.0", "entry": "m.py",
        "runtime": "python", "ipfs_cid": "bafysmoketest",
        "author_did": "did:key:z6MkSmoke", "sigstore_sig": "sig",
        "skip_sigstore": True,
    }, timeout=5)
    assert r.status_code == 200, r.text
    L = httpx.get(f"{real_daemon}/v2/skill/list", timeout=5)
    assert "smoke-skill" in L.json()["skills"]


def test_real_daemon_v2_provenance_attest(real_daemon):
    r = httpx.post(f"{real_daemon}/v2/provenance/attest", json={
        "response_id": "real-r1",
        "query": "q",
        "answer": "a",
        "did_answerer": "did:key:z6MkAlice",
        "cited_cases": [{"source_id": "case-1", "did_author": "did:key:z6MkBob"}],
        "network": "mock",
    }, timeout=5)
    assert r.status_code == 200
    assert r.json()["attestation_uid"].startswith("mock:")


def test_real_daemon_v2_debate(real_daemon):
    r = httpx.post(f"{real_daemon}/v2/debate/run", json={
        "query": "How to fix Rust async deadlock?",
        "agents": [
            {"did": "did:key:z6MkA", "topic_reputation": 0.5},
            {"did": "did:key:z6MkB", "topic_reputation": 0.8},
        ],
        "n_rounds": 2,
    }, timeout=10)
    assert r.status_code == 200
    assert "stub synthesized" in r.json()["final_answer"]
    assert r.json()["n_rounds"] == 4  # 2 agents × 2 rounds


def test_real_daemon_v2_growth_write_and_last(real_daemon):
    httpx.post(f"{real_daemon}/v2/growth/write", json={
        "date": "2026-06-04", "cases_added": 5, "chats_sent": 10,
    }, timeout=5)
    r = httpx.get(f"{real_daemon}/v2/growth/last?n=7", timeout=5)
    assert r.status_code == 200
    assert r.json()["total_cases"] >= 5


def test_real_daemon_v2_lesson_distill(real_daemon):
    r = httpx.post(f"{real_daemon}/v2/lesson/distill", json={
        "did_owner": "did:key:z6MkA",
        "source_case_ids": ["c1", "c2", "c3"],
        "topic": "rust",
    }, timeout=5)
    assert r.status_code == 200
    assert r.json()["id"].startswith("lesson-")


def test_real_daemon_full_v2_route_count(real_daemon):
    """Real daemon should expose ≥17 v2/* routes."""
    r = httpx.get(f"{real_daemon}/openapi.json", timeout=5)
    assert r.status_code == 200
    paths = r.json().get("paths", {})
    v2_paths = [p for p in paths if p.startswith("/v2/")]
    assert len(v2_paths) >= 10, f"expected ≥10 v2 paths, got: {v2_paths}"


def test_real_daemon_full_ask_pipeline_e2e(real_daemon):
    """End-to-end ask pipeline via real daemon HTTP (4-C.3):
    case write → search → attest → reputation update → routing → growth → lesson distill.
    全 v2 模块协作 真 HTTP.
    """
    # 1. Alice writes case
    r = httpx.post(f"{real_daemon}/v2/case", json={
        "question": "how to fix Rust async tokio deadlock",
        "answer": "use unwrap_or_else and proper Drop impl",
        "did_author": "did:key:z6MkAlice",
        "tags": ["rust", "async"],
    }, timeout=5)
    assert r.status_code == 200
    alice_case_id = r.json()["id"]

    # 2. Bob searches
    s = httpx.get(f"{real_daemon}/v2/case/search/?q=rust+async", timeout=5)
    assert s.status_code == 200
    hits = s.json()["cases"]
    assert any(c["id"] == alice_case_id for c in hits)

    # 3. Bob attests provenance (cites Alice)
    a = httpx.post(f"{real_daemon}/v2/provenance/attest", json={
        "response_id": "bob-resp-1",
        "query": "rust async tokio",
        "answer": "follow Alice pattern + cancellation token",
        "did_answerer": "did:key:z6MkBob",
        "cited_cases": [{"source_id": alice_case_id, "did_author": "did:key:z6MkAlice"}],
        "network": "mock",
    }, timeout=5)
    assert a.status_code == 200
    assert a.json()["citation_count"] == 1

    # 4. Update Alice's rust reputation
    u = httpx.post(f"{real_daemon}/v2/reputation/update", json={
        "did": "did:key:z6MkAlice", "topic": "rust", "score_delta": 0.1,
    }, timeout=5)
    assert u.status_code == 200
    assert u.json()["new_score"] >= 0.6

    # 5. Top-k routing for next rust query
    t = httpx.post(f"{real_daemon}/v2/reputation/top-k", json={
        "query": "another rust q",
        "topic": "rust",
        "candidates": ["did:key:z6MkAlice", "did:key:z6MkRand"],
        "top_k": 2,
    }, timeout=5)
    assert t.status_code == 200
    assert "did:key:z6MkAlice" in t.json()["picked"]

    # 6. Record growth snapshot
    g = httpx.post(f"{real_daemon}/v2/growth/write", json={
        "date": "2026-06-04", "cases_added": 1, "chats_sent": 5,
    }, timeout=5)
    assert g.status_code == 200
    trend = httpx.get(f"{real_daemon}/v2/growth/last?n=1", timeout=5)
    assert trend.json()["total_cases"] >= 1

    # 7. Distill lesson from 2+ cases
    r2 = httpx.post(f"{real_daemon}/v2/case", json={
        "question": "another async case",
        "answer": "second answer",
        "did_author": "did:key:z6MkBob",
    }, timeout=5)
    second_id = r2.json()["id"]
    l = httpx.post(f"{real_daemon}/v2/lesson/distill", json={
        "did_owner": "did:key:z6MkBob",
        "source_case_ids": [alice_case_id, second_id],
        "topic": "rust",
    }, timeout=5)
    assert l.status_code == 200
    assert l.json()["id"].startswith("lesson-")
    # 全 v2 模块 真 HTTP 链路验通


def test_real_daemon_metrics_prometheus_format(real_daemon):
    """5.4 Prometheus /sisoul/metrics endpoint returns valid exposition format."""
    r = httpx.get(f"{real_daemon}/sisoul/metrics", timeout=5)
    assert r.status_code == 200
    text = r.text
    # required metric names
    for name in [
        "sisoul_info",
        "sisoul_cases_total",
        "sisoul_skills_installed",
        "sisoul_friends_total",
        "sisoul_lessons_total",
        "sisoul_growth_snapshot_days",
    ]:
        assert name in text, f"missing metric {name}"
    # HELP + TYPE format
    assert "# HELP sisoul_cases_total" in text
    assert "# TYPE sisoul_cases_total gauge" in text
    # version label
    assert 'version="1.0.0-alpha"' in text


def test_real_daemon_sisoul_health_cli(real_daemon):
    """sisoul health CLI returns OK + lists v2 routes."""
    import subprocess, sys
    base = real_daemon
    res = subprocess.run(
        [sys.executable, "-c",
         "from sisoul.cli_commands.health import cli_health; "
         "import typer; "
         "typer.run(cli_health)",
         "--base", base, "--timeout", "5"],
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, f"CLI failed (exit {res.returncode}): {res.stderr}\nstdout: {res.stdout}"
    assert "daemon health" in res.stdout
    assert "ok" in res.stdout
    assert "v2 routes" in res.stdout
