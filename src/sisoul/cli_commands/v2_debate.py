"""sisoul debate CLI · Multi-Agent Debate via daemon /v2/debate/run."""
from __future__ import annotations
import json
import os
from typing import Optional

import typer
import httpx


def cli_debate(
    query: str = typer.Argument(..., help="difficult question"),
    rounds: int = typer.Option(3, "--rounds", "-r", help="2-5 rounds"),
    agents: str = typer.Option(
        "did:key:z6MkA:Alice:0.6,did:key:z6MkB:Bob:0.85,did:key:z6MkC:Carol:0.72",
        "--agents", help="comma list: did:petname:rep, default 3 agents",
    ),
    json_output: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """Run multi-agent debate. Foundation: 3-round mock. v3.0 ship: 真 LLM + GossipSub."""
    base = os.environ.get("SISOUL_DAEMON_BASE", "http://127.0.0.1:9876")
    try:
        h = httpx.get(f"{base}/sisoul/health", timeout=2.0)
        if h.status_code != 200:
            raise RuntimeError(f"daemon {h.status_code}")
    except Exception as e:
        typer.echo(f"ERROR: daemon not reachable at {base} ({e})", err=True)
        raise typer.Exit(code=1)

    agent_specs = []
    for entry in agents.split(","):
        parts = entry.strip().split(":")
        if len(parts) < 3:
            typer.echo(f"ERROR: bad agent spec: {entry} (need did:petname:rep)", err=True)
            raise typer.Exit(code=1)
        rep = float(parts[-1])
        petname = parts[-2]
        did = ":".join(parts[:-2])
        agent_specs.append({"did": did, "petname": petname, "topic_reputation": rep})

    r = httpx.post(f"{base}/v2/debate/run", json={
        "query": query, "agents": agent_specs, "n_rounds": rounds,
    }, timeout=30)
    if r.status_code != 200:
        typer.echo(f"ERROR: {r.status_code} {r.text}", err=True)
        raise typer.Exit(code=1)
    data = r.json()
    if json_output:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    typer.echo(f"\n  Question: {data['query']}\n")
    typer.echo(f"  Agents: {len(data['agents'])} · Rounds: {data['n_rounds']} · Confidence: {data['final_confidence']:.2f}")
    typer.echo("")
    for a in data["agents"]:
        marker = " (synthesizer)" if a.get("is_synthesizer") else ""
        typer.echo(f"    - {a.get('petname') or a['did'][:16]}{marker}")
    typer.echo("\n  Synthesized answer:")
    typer.echo(f"  {data['final_answer']}\n")
