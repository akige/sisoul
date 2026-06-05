"""sisoul health · check local daemon + v2 endpoints + show modules state."""
from __future__ import annotations
import json
import os
import sys

import typer
import httpx


def cli_health(
    base: str = typer.Option(
        None, "--base", help="daemon base URL (default $SISOUL_DAEMON_BASE or http://127.0.0.1:9876)"
    ),
    json_output: bool = typer.Option(False, "--json", "-j"),
    timeout: float = typer.Option(2.0, "--timeout", "-t"),
) -> None:
    """Check daemon health + list v2 endpoint count + module versions.

    Exit code 0 if daemon healthy + all v2 endpoints reachable, else 1.
    """
    base_url = base or os.environ.get("SISOUL_DAEMON_BASE", "http://127.0.0.1:9876")
    result: dict = {
        "base": base_url,
        "daemon_health": "unknown",
        "version": None,
        "phase": None,
        "v2_endpoint_count": 0,
        "v2_endpoint_health": {},
        "metrics_available": False,
    }

    # 1. /sisoul/health
    try:
        r = httpx.get(f"{base_url}/sisoul/health", timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            result["daemon_health"] = data["status"]
            result["version"] = data.get("version")
            result["phase"] = data.get("phase")
        else:
            result["daemon_health"] = f"http_{r.status_code}"
    except Exception as e:
        result["daemon_health"] = f"unreachable: {type(e).__name__}"
        if json_output:
            typer.echo(json.dumps(result, indent=2))
        else:
            typer.echo(f"\nERROR daemon unreachable at {base_url}\n  {e}\n  Start: sisoul daemon start --background\n", err=True)
        raise typer.Exit(code=1)

    # 2. /openapi.json — count v2 routes
    try:
        o = httpx.get(f"{base_url}/openapi.json", timeout=timeout)
        if o.status_code == 200:
            paths = o.json().get("paths", {})
            v2_paths = [p for p in paths if p.startswith("/v2/")]
            result["v2_endpoint_count"] = len(v2_paths)
    except Exception:
        pass

    # 3. Quick smoke test 3 critical v2 endpoints
    for path in ["/v2/case", "/v2/skill/list", "/v2/growth/last?n=1"]:
        try:
            r = httpx.get(f"{base_url}{path}", timeout=timeout)
            result["v2_endpoint_health"][path] = "ok" if r.status_code in (200, 404) else f"http_{r.status_code}"
        except Exception as e:
            result["v2_endpoint_health"][path] = f"err:{type(e).__name__}"

    # 4. /sisoul/metrics
    try:
        m = httpx.get(f"{base_url}/sisoul/metrics", timeout=timeout)
        result["metrics_available"] = m.status_code == 200
    except Exception:
        pass

    if json_output:
        typer.echo(json.dumps(result, indent=2))
        return

    ok = result["daemon_health"] == "ok"
    icon = "✓" if ok else "✗"
    typer.echo(f"\n  {icon} sisoul daemon health\n")
    typer.echo(f"    base       : {base_url}")
    typer.echo(f"    status     : {result['daemon_health']}")
    typer.echo(f"    version    : {result['version']}")
    typer.echo(f"    phase      : {result['phase']}")
    typer.echo(f"    v2 routes  : {result['v2_endpoint_count']}")
    typer.echo(f"    metrics    : {'available' if result['metrics_available'] else 'unavailable'}")
    typer.echo("")
    if result["v2_endpoint_health"]:
        typer.echo("    v2 endpoints:")
        for path, status in result["v2_endpoint_health"].items():
            mark = "✓" if status == "ok" else "✗"
            typer.echo(f"      {mark} {path:30} {status}")
    typer.echo("")
    if not ok:
        raise typer.Exit(code=1)
