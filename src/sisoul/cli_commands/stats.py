"""sisoul stats — show local + friend stats."""
from __future__ import annotations
import json
import os
from pathlib import Path

import typer


def cli_stats(
    json_output: bool = typer.Option(False, "--json", "-j", help="JSON 输出"),
    vault_dir: Path = typer.Option(None, "--vault", help="vault dir (default ~/.sisoul/)"),
) -> None:
    """显示本机 case / skill / friend / lessons 数 + v2/v3 状态."""
    vault = vault_dir or Path(os.environ.get("SISOUL_VAULT", "~/.sisoul")).expanduser()
    skills_dir = Path(os.environ.get("SISOUL_SKILLS_DIR", "~/.sisoul/skills")).expanduser()

    stats = {
        "vault": str(vault),
        "vault_exists": vault.exists(),
        "cases_count": 0,
        "skills_count": 0,
        "friends_count": 0,
        "petnames_count": 0,
        "lessons_count": 0,
        "growth_snapshots": 0,
    }

    if vault.exists():
        cases_dir = vault / "cases"
        if cases_dir.exists():
            stats["cases_count"] = len(list(cases_dir.glob("*.json")))
        lessons_dir = vault / "lessons"
        if lessons_dir.exists():
            stats["lessons_count"] = len(list(lessons_dir.glob("*.json")))
        growth_dir = vault / "growth"
        if growth_dir.exists():
            stats["growth_snapshots"] = len(list(growth_dir.glob("*.json")))

        petnames_file = vault / "petnames.json"
        if petnames_file.exists():
            try:
                stats["petnames_count"] = len(json.loads(petnames_file.read_text()))
            except Exception:
                pass

        friends_dir = vault / "friends"
        if friends_dir.exists():
            stats["friends_count"] = len(list(friends_dir.glob("*.json")))

    if skills_dir.exists():
        stats["skills_count"] = len(
            [d for d in skills_dir.iterdir() if d.is_dir() and (d / "manifest.json").exists()]
        )

    if json_output:
        typer.echo(json.dumps(stats, indent=2, ensure_ascii=False))
        return

    typer.echo("")
    typer.echo("  sisoul stats")
    typer.echo("  ────────────────────────────────")
    typer.echo(f"  vault       : {stats['vault']}")
    if not stats["vault_exists"]:
        typer.echo(f"  ⚠️  vault not initialized. run: sisoul init")
        return
    typer.echo(f"  cases       : {stats['cases_count']}")
    typer.echo(f"  skills      : {stats['skills_count']}")
    typer.echo(f"  friends     : {stats['friends_count']}")
    typer.echo(f"  petnames    : {stats['petnames_count']}")
    typer.echo(f"  lessons     : {stats['lessons_count']}")
    typer.echo(f"  growth days : {stats['growth_snapshots']}")
    typer.echo("")
