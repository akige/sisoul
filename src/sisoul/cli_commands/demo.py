"""sisoul demo · 一键演示 v2.0 智能体网络全链路.

跑完用户能看到 case 检索 / provenance attest / reputation routing / debate / lesson 全栈协作.
Foundation: 用 mock 数据 + mock LLM. v2.0 ship 后接真 LLM/EAS/IPFS.
"""
from __future__ import annotations
import os
import time

import typer
import httpx


def cli_demo(
    base: str = typer.Option(None, "--base", help="daemon base URL"),
    delay: float = typer.Option(0.5, "--delay", help="sleep between steps (sec)"),
) -> None:
    """End-to-end demo: write case → search → attest → rep → debate → lesson."""
    base_url = base or os.environ.get("SISOUL_DAEMON_BASE", "http://127.0.0.1:9876")

    def step(n: int, title: str) -> None:
        typer.echo("")
        typer.echo(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        typer.echo(f"  Step {n}: {title}")
        typer.echo("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # 0. health check
    try:
        h = httpx.get(f"{base_url}/sisoul/health", timeout=3)
        if h.status_code != 200:
            typer.echo(f"ERROR: daemon not ready at {base_url}", err=True)
            raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"ERROR: daemon unreachable at {base_url}: {e}", err=True)
        typer.echo("  Start daemon: sisoul daemon start --background", err=True)
        raise typer.Exit(code=1)

    typer.echo("")
    typer.echo("  sisoul v2.0 智能体网络 · 一键 demo")
    typer.echo(f"  daemon @ {base_url}")

    # Step 1: Alice writes a case
    step(1, "Alice writes a case (POST /v2/case)")
    time.sleep(delay)
    r = httpx.post(f"{base_url}/v2/case", json={
        "question": "[demo] how to fix Rust async tokio deadlock",
        "answer": "use unwrap_or_else + cancellation token + proper Drop",
        "did_author": "did:key:z6MkDemoAlice",
        "tags": ["demo", "rust", "async"],
    }, timeout=5)
    alice_case_id = r.json()["id"]
    typer.echo(f"  ✓ case written: {alice_case_id}")

    # Step 2: Bob searches
    step(2, "Bob searches (GET /v2/case/search/?q=rust+async)")
    time.sleep(delay)
    s = httpx.get(f"{base_url}/v2/case/search/?q=rust+async&top_k=5", timeout=5)
    hits = s.json()["cases"]
    typer.echo(f"  ✓ {len(hits)} case(s) hit · alice's case in results: {any(c['id'] == alice_case_id for c in hits)}")

    # Step 3: Bob attests provenance
    step(3, "Bob attests provenance (POST /v2/provenance/attest)")
    time.sleep(delay)
    a = httpx.post(f"{base_url}/v2/provenance/attest", json={
        "response_id": "demo-resp-bob-1",
        "query": "rust async tokio",
        "answer": "[Bob's answer citing Alice's case]",
        "did_answerer": "did:key:z6MkDemoBob",
        "cited_cases": [{"source_id": alice_case_id, "did_author": "did:key:z6MkDemoAlice"}],
        "network": "mock",
    }, timeout=5)
    typer.echo(f"  ✓ attestation UID: {a.json()['attestation_uid']}")
    typer.echo(f"    citations: {a.json()['citation_count']}, micropay: {a.json()['total_micropay_sis']} SIS")

    # Step 4: Update Alice rep (Bob cited her)
    step(4, "Update Alice rep on rust (POST /v2/reputation/update)")
    time.sleep(delay)
    u = httpx.post(f"{base_url}/v2/reputation/update", json={
        "did": "did:key:z6MkDemoAlice", "topic": "rust", "score_delta": 0.15,
    }, timeout=5)
    typer.echo(f"  ✓ Alice new rust score: {u.json()['new_score']:.2f}")

    # Step 5: Routing top-K next rust question
    step(5, "Route next rust question (POST /v2/reputation/top-k)")
    time.sleep(delay)
    t = httpx.post(f"{base_url}/v2/reputation/top-k", json={
        "query": "another rust q",
        "topic": "rust",
        "candidates": [
            "did:key:z6MkDemoAlice", "did:key:z6MkDemoBob", "did:key:z6MkRandomPerson",
        ],
        "top_k": 2,
    }, timeout=5)
    typer.echo(f"  ✓ picked top {len(t.json()['picked'])} agents:")
    for p in t.json()["picked"]:
        typer.echo(f"    - {p[:30]}…")

    # Step 6: Multi-agent debate
    step(6, "Multi-agent debate (POST /v2/debate/run)")
    time.sleep(delay)
    d = httpx.post(f"{base_url}/v2/debate/run", json={
        "query": "PostgreSQL + pgbouncer + sqlx prepared statement issue?",
        "agents": [
            {"did": "did:key:z6MkDBExpert", "petname": "Bob (DBA)", "topic_reputation": 0.92},
            {"did": "did:key:z6MkRustGuru", "petname": "Charlie (Rust)", "topic_reputation": 0.85},
            {"did": "did:key:z6MkSRE", "petname": "Dave (SRE)", "topic_reputation": 0.78},
        ],
        "n_rounds": 3,
    }, timeout=15)
    data = d.json()
    typer.echo(f"  ✓ debate complete: {data['n_rounds']} rounds, confidence {data['final_confidence']:.2f}")
    for ag in data["agents"]:
        marker = " (synthesizer)" if ag.get("is_synthesizer") else ""
        typer.echo(f"    - {ag.get('petname') or ag['did'][:16]}{marker}")
    typer.echo(f"  Final answer: {data['final_answer'][:80]}…")

    # Step 7: Distill lesson
    step(7, "Distill lesson from 2 cases (POST /v2/lesson/distill)")
    time.sleep(delay)
    r2 = httpx.post(f"{base_url}/v2/case", json={
        "question": "[demo] another async case", "answer": "second", "did_author": "did:key:z6MkDemoBob",
    }, timeout=5)
    l = httpx.post(f"{base_url}/v2/lesson/distill", json={
        "did_owner": "did:key:z6MkDemoBob",
        "source_case_ids": [alice_case_id, r2.json()["id"]],
        "topic": "rust",
    }, timeout=5)
    typer.echo(f"  ✓ lesson distilled: {l.json()['id']}")
    typer.echo(f"    title: {l.json()['title']}")

    # Step 8: Growth snapshot
    step(8, "Record growth snapshot (POST /v2/growth/write)")
    time.sleep(delay)
    httpx.post(f"{base_url}/v2/growth/write", json={
        "date": "2026-06-04", "cases_added": 2, "chats_sent": 1, "skills_used": 0,
    }, timeout=5)
    trend = httpx.get(f"{base_url}/v2/growth/last?n=7", timeout=5)
    typer.echo(f"  ✓ total cases this week: {trend.json()['total_cases']}")

    # Done
    typer.echo("")
    typer.echo("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    typer.echo("  Demo complete — v2.0 智能体网络 8 steps 全链路 ✓")
    typer.echo("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    typer.echo("")
    typer.echo("  Try these next:")
    typer.echo("    sisoul stats         # see your local case/skill/lesson counts")
    typer.echo("    sisoul case list     # list all cases")
    typer.echo("    sisoul debate '<q>'  # try multi-agent debate on your question")
    typer.echo(f"    open {base_url}/docs    # FastAPI Swagger UI for /v2/* endpoints")
    typer.echo("    open https://sisoul.github.io/sisoul-pwa/   # PWA dashboard")
    typer.echo("")
