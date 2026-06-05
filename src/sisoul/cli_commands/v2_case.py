"""sisoul case CLI · 直接调 daemon /v2/case/* endpoints.

提供 list / search / show / add 4 子命令.
"""
from __future__ import annotations
import json
import os
from typing import Optional

import typer
import httpx


case_app = typer.Typer(name="case", help="case retrieval / write (v2.0 智能体网络).")


def _daemon_base() -> str:
    return os.environ.get("SISOUL_DAEMON_BASE", "http://127.0.0.1:9876")


def _check_daemon(base: str) -> None:
    try:
        r = httpx.get(f"{base}/sisoul/health", timeout=2.0)
        if r.status_code != 200:
            raise RuntimeError(f"daemon health {r.status_code}")
    except Exception as e:
        typer.echo(f"ERROR: daemon not reachable at {base} ({e})", err=True)
        typer.echo("  Start daemon: sisoul daemon start --background", err=True)
        raise typer.Exit(code=1)


@case_app.command("list")
def cmd_list(
    limit: int = typer.Option(20, "--limit", "-n", help="max cases to show"),
    json_output: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """List all cases stored in local vault."""
    base = _daemon_base()
    _check_daemon(base)
    r = httpx.get(f"{base}/v2/case?limit={limit}", timeout=5)
    if r.status_code != 200:
        typer.echo(f"ERROR: {r.status_code} {r.text}", err=True)
        raise typer.Exit(code=1)
    data = r.json()
    if json_output:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    typer.echo(f"\n  {data['count']} case(s)\n")
    for c in data["cases"]:
        typer.echo(f"  {c['id']}")
        typer.echo(f"    Q: {c['question'][:80]}")
        typer.echo(f"    by {c['did_author'][:24]}… @ {c['created_at']}")
        typer.echo("")


@case_app.command("search")
def cmd_search(
    query: str = typer.Argument(..., help="search query"),
    top_k: int = typer.Option(5, "--top-k", "-k"),
    json_output: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """Search cases via TF-IDF (foundation) or ChromaDB embed (v2.0 ship)."""
    base = _daemon_base()
    _check_daemon(base)
    from urllib.parse import quote
    r = httpx.get(f"{base}/v2/case/search/?q={quote(query)}&top_k={top_k}", timeout=5)
    if r.status_code != 200:
        typer.echo(f"ERROR: {r.status_code} {r.text}", err=True)
        raise typer.Exit(code=1)
    data = r.json()
    if json_output:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    typer.echo(f"\n  query: {data['query']}")
    typer.echo(f"  hits : {len(data['cases'])} ({'✓' if data['is_hit'] else '∅'})\n")
    for c in data["cases"]:
        typer.echo(f"  {c['id']}")
        typer.echo(f"    Q: {c['question'][:80]}")
        typer.echo("")


@case_app.command("show")
def cmd_show(
    case_id: str = typer.Argument(...),
    json_output: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """Show full case detail by ID."""
    base = _daemon_base()
    _check_daemon(base)
    r = httpx.get(f"{base}/v2/case/{case_id}", timeout=5)
    if r.status_code == 404:
        typer.echo(f"ERROR: case not found: {case_id}", err=True)
        raise typer.Exit(code=1)
    if r.status_code != 200:
        typer.echo(f"ERROR: {r.status_code} {r.text}", err=True)
        raise typer.Exit(code=1)
    c = r.json()
    if json_output:
        typer.echo(json.dumps(c, ensure_ascii=False, indent=2))
        return
    typer.echo(f"\n  Case: {c['id']}")
    typer.echo(f"  Author: {c['did_author']}")
    typer.echo(f"  Created: {c['created_at']}")
    typer.echo(f"  Tags: {', '.join(c.get('tags', []))}")
    typer.echo(f"\n  Q: {c['question']}\n")
    typer.echo(f"  A: {c['answer']}\n")
    if c.get("sources"):
        typer.echo(f"  Sources: {len(c['sources'])}")
        for s in c["sources"]:
            typer.echo(f"    - {s}")
    if c.get("eas_attestation_uid"):
        typer.echo(f"\n  EAS UID: {c['eas_attestation_uid']}")


@case_app.command("add")
def cmd_add(
    question: str = typer.Option(..., "--question", "-q"),
    answer: str = typer.Option(..., "--answer", "-a"),
    did_author: str = typer.Option(..., "--did", "-d"),
    tags: str = typer.Option("", "--tags", help="comma-separated"),
) -> None:
    """Add a case to local vault."""
    base = _daemon_base()
    _check_daemon(base)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    r = httpx.post(f"{base}/v2/case", json={
        "question": question, "answer": answer,
        "did_author": did_author, "tags": tag_list,
    }, timeout=5)
    if r.status_code != 200:
        typer.echo(f"ERROR: {r.status_code} {r.text}", err=True)
        raise typer.Exit(code=1)
    data = r.json()
    typer.echo(f"OK case added: {data['id']}")
    typer.echo(f"  path: {data['path']}")
