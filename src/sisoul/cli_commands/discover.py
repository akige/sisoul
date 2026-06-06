"""`sisoul friend discover` — Telegram/QQ-style: list active peers from the directory."""
from __future__ import annotations
import json
import time
from typing import Optional

import typer

from sisoul.prekey_directory import discover_peers, PrekeyDirectoryError


discover_app = typer.Typer(help="Find sisoul peers by username / bio / last-seen.")


@discover_app.command("discover")
def cmd_discover(
    filter_text: str = typer.Argument("", help="match against username or bio (substring, case-insensitive)"),
    limit: int = typer.Option(50, "--limit", "-n"),
    max_age_hours: float = typer.Option(168.0, "--max-age-hours",
        help="only show peers active within this window (default 7 days)"),
    json_output: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """List peers registered at the prekey directory.

    Like QQ "search online users" — you get a list of usernames + bios +
    last-seen age, all of whom you can immediately `sisoul friend add` and
    `sisoul chat send <username>`.
    """
    try:
        peers = discover_peers(filter_text, limit=limit, max_age_hours=max_age_hours)
    except PrekeyDirectoryError as e:
        typer.echo(f"ERROR: directory unreachable: {e}", err=True)
        raise typer.Exit(code=2)
    if json_output:
        typer.echo(json.dumps({"count": len(peers), "peers": peers}, indent=2))
        return
    if not peers:
        scope = f"matching {filter_text!r}" if filter_text else "online"
        typer.echo(f"(no peers {scope} in the last {max_age_hours:.0f}h)")
        return
    typer.echo(f"Discovered {len(peers)} peer(s):")
    typer.echo(f"{'username':<20}{'last-seen':<14}did")
    typer.echo("-" * 90)
    now = time.time()
    for p in peers:
        uname = ("@" + p["username"]) if p.get("username") else "(no name)"
        age = p.get("age_seconds", now - p.get("last_seen", now))
        when = f"{age/60:.0f}m ago" if age < 3600 else (
            f"{age/3600:.0f}h ago" if age < 86400 else f"{age/86400:.1f}d ago"
        )
        did_short = p["did"][:50] + "..." if len(p["did"]) > 50 else p["did"]
        typer.echo(f"{uname:<20}{when:<14}{did_short}")
        if p.get("bio"):
            typer.echo(f"  bio: {p['bio'][:80]}")
    typer.echo()
    typer.echo("Add any of them: sisoul friend add @<username>")
    typer.echo("Send a message:  sisoul chat send @<username> 'hi'")
