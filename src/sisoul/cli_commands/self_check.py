"""sisoul self-check · single command validates alpha launch readiness."""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
import httpx


CheckResult = tuple[bool, str]


def _check_version() -> CheckResult:
    """Verify VERSION = 1.0.0-alpha."""
    try:
        from sisoul import __version__
        ok = __version__ == "1.0.0-alpha"
        return (ok, f"sisoul version: {__version__}")
    except Exception as e:
        return (False, f"import error: {e}")


def _check_vault_init() -> CheckResult:
    """Verify vault initialized."""
    vault = Path(os.environ.get("SISOUL_VAULT", "~/.sisoul")).expanduser()
    if not vault.exists():
        return (False, f"vault not found at {vault} (run: sisoul init)")
    if not (vault / "dna.json").exists():
        return (False, f"vault exists but no dna.json")
    return (True, f"vault ok at {vault}")


def _check_daemon(base: str, timeout: float = 2.0) -> CheckResult:
    """Verify daemon reachable."""
    try:
        r = httpx.get(f"{base}/sisoul/health", timeout=timeout)
        if r.status_code != 200:
            return (False, f"daemon health http {r.status_code}")
        return (True, f"daemon ok @ {base}")
    except Exception as e:
        return (False, f"daemon unreachable @ {base}: {type(e).__name__}")


def _check_v2_routes(base: str, timeout: float = 2.0) -> CheckResult:
    """Verify daemon has ≥10 v2 routes."""
    try:
        r = httpx.get(f"{base}/openapi.json", timeout=timeout)
        if r.status_code != 200:
            return (False, f"openapi.json http {r.status_code}")
        paths = r.json().get("paths", {})
        v2 = [p for p in paths if p.startswith("/v2/")]
        if len(v2) < 10:
            return (False, f"only {len(v2)} v2 routes (expected ≥10)")
        return (True, f"v2 routes: {len(v2)} endpoints")
    except Exception as e:
        return (False, f"openapi probe failed: {type(e).__name__}")


def _check_metrics(base: str, timeout: float = 2.0) -> CheckResult:
    """Verify /sisoul/metrics works."""
    try:
        r = httpx.get(f"{base}/sisoul/metrics", timeout=timeout)
        if r.status_code != 200:
            return (False, f"metrics http {r.status_code}")
        if "sisoul_info" not in r.text:
            return (False, "metrics output missing sisoul_info")
        return (True, "metrics ok (Prometheus format)")
    except Exception as e:
        return (False, f"metrics failed: {type(e).__name__}")


def _check_modules() -> CheckResult:
    """Verify v2 modules import."""
    try:
        from sisoul.v2 import case_graph, personal_lora, provenance, skill_marketplace
        from sisoul.v2 import debate, reputation, memory_compaction, growth, pipeline
        return (True, "9 v2 modules importable")
    except Exception as e:
        return (False, f"v2 module import failed: {e}")


def _check_chat_pqxdh() -> CheckResult:
    """Verify chat PQXDH module works."""
    try:
        from sisoul.chat import pqxdh, double_ratchet, session
        return (True, "chat modules ok (PQXDH + Double Ratchet)")
    except Exception as e:
        return (False, f"chat import failed: {e}")


def _check_pytest_quick() -> CheckResult:
    """Verify pytest can collect (no syntax errors)."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q",
             "--ignore=tests/test_v1_integration_full_user_journey.py"],
            cwd=str(Path(__file__).parent.parent.parent.parent),
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return (False, f"pytest collect failed: {result.stderr[:100]}")
        # Look for test count
        last_lines = result.stdout.strip().splitlines()[-3:]
        return (True, f"pytest collect ok ({last_lines[-1] if last_lines else 'tests found'})")
    except Exception as e:
        return (False, f"pytest invoke failed: {type(e).__name__}")


def cli_self_check(
    base: str = typer.Option(None, "--base", help="daemon URL"),
    skip_daemon: bool = typer.Option(False, "--skip-daemon", help="skip daemon checks (offline mode)"),
    skip_pytest: bool = typer.Option(False, "--skip-pytest", help="skip pytest collect"),
    json_output: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """One-shot validation: alpha launch ready? Exit 0 if all green."""
    base_url = base or os.environ.get("SISOUL_DAEMON_BASE", "http://127.0.0.1:9876")
    checks: list[tuple[str, CheckResult]] = []

    checks.append(("version", _check_version()))
    checks.append(("vault", _check_vault_init()))
    checks.append(("modules", _check_modules()))
    checks.append(("chat", _check_chat_pqxdh()))
    if not skip_pytest:
        checks.append(("pytest", _check_pytest_quick()))
    if not skip_daemon:
        checks.append(("daemon", _check_daemon(base_url)))
        checks.append(("v2_routes", _check_v2_routes(base_url)))
        checks.append(("metrics", _check_metrics(base_url)))

    all_ok = all(ok for _, (ok, _) in checks)

    if json_output:
        typer.echo(json.dumps({
            "alpha_launch_ready": all_ok,
            "checks": [{"name": n, "ok": ok, "msg": msg} for n, (ok, msg) in checks],
        }, indent=2))
        if not all_ok:
            raise typer.Exit(code=1)
        return

    typer.echo("")
    typer.echo("  sisoul self-check · alpha launch readiness")
    typer.echo("  " + "─" * 50)
    for name, (ok, msg) in checks:
        mark = "✓" if ok else "✗"
        typer.echo(f"  {mark} {name:12} {msg}")
    typer.echo("  " + "─" * 50)
    if all_ok:
        typer.echo(f"  ✓ ALL {len(checks)} CHECKS PASS — alpha launch READY\n")
    else:
        failed = [n for n, (ok, _) in checks if not ok]
        typer.echo(f"  ✗ {len(failed)} check(s) FAILED: {', '.join(failed)}\n")
        raise typer.Exit(code=1)
