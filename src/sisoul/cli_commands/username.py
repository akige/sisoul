"""`sisoul username` — pick a Telegram-style handle so peers can `chat send <name>` instead of typing a 60-char did:key."""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Optional

import typer

from sisoul.cli_commands.chat import _build_manager
from sisoul.prekey_directory import (
    publish_my_prekey,
    resolve_username as resolve_username_remote,
    PrekeyDirectoryError,
)

username_app = typer.Typer(help="Register / show your username in the prekey directory (Telegram-style handle).")


def _username_file() -> Path:
    vault = Path(os.environ.get("SISOUL_VAULT", str(Path.home() / ".sisoul"))).expanduser()
    return vault / "username.json"


@username_app.command("register")
def cmd_register(
    name: str = typer.Argument(..., help="username (2-32 chars, [a-zA-Z0-9_-])"),
    bio: str = typer.Option("", "--bio", "-b", help="200 char public bio (optional)"),
    memory: bool = typer.Option(False, "--memory"),
) -> None:
    """Reserve a username + publish it to the prekey directory.

    Last-writer-wins for now (alpha v1.0). Anyone can squat your username if
    you don't keep your bundle refreshed — `sisoul chat rotate-prekey` refreshes
    daily-ish, which is what you want anyway."""
    import re
    if not re.match(r"^[a-zA-Z0-9_\-]{2,32}$", name):
        typer.echo(
            f"ERROR: username must match [a-zA-Z0-9_-]{{2,32}}: {name!r}",
            err=True,
        )
        raise typer.Exit(code=1)
    mgr = _build_manager(memory)
    bundle = mgr.rotate_prekey()
    try:
        result = publish_my_prekey(
            mgr.local_did, bundle.to_dict(), username=name, bio=bio,
        )
    except PrekeyDirectoryError as e:
        typer.echo(f"ERROR: prekey directory PUT failed: {e}", err=True)
        raise typer.Exit(code=2)
    # Cache local mapping
    f = _username_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"username": name, "did": mgr.local_did, "bio": bio}, indent=2))
    typer.echo(f"OK username @{name} registered for did {mgr.local_did}")
    if bio:
        typer.echo(f"  bio: {bio}")
    typer.echo(f"  remote: {result}")


@username_app.command("show")
def cmd_show(
    json_output: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """Show your locally-saved username."""
    f = _username_file()
    if not f.exists():
        typer.echo("(no username registered; run `sisoul username register <name>`)")
        return
    payload = json.loads(f.read_text())
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(f"@{payload['username']}")
    typer.echo(f"  did: {payload['did']}")
    if payload.get("bio"):
        typer.echo(f"  bio: {payload['bio']}")


@username_app.command("resolve")
def cmd_resolve(
    name: str = typer.Argument(..., help="username to look up"),
) -> None:
    """Resolve a username to a did:key via the prekey directory."""
    try:
        did = resolve_username_remote(name)
    except PrekeyDirectoryError as e:
        typer.echo(f"ERROR: directory lookup failed: {e}", err=True)
        raise typer.Exit(code=2)
    if did is None:
        typer.echo(f"NO MATCH: @{name} is not registered.")
        raise typer.Exit(code=1)
    typer.echo(f"@{name} → {did}")
