"""sisoul friend-discover · combined mDNS scan + petname-aware friend lookup."""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Optional

import typer


def cli_friend_discover(
    timeout: float = typer.Option(5.0, "--timeout", "-t", help="mDNS scan timeout (sec)"),
    json_output: bool = typer.Option(False, "--json", "-j"),
    save_petname: bool = typer.Option(
        False, "--save-petname",
        help="prompt to save petname for each discovered friend"
    ),
) -> None:
    """Scan LAN for sisoul peers via mDNS + lookup petnames + show friend status."""
    try:
        from sisoul.friend.mdns import ZEROCONF_AVAILABLE, scan
    except ImportError:
        typer.echo("ERROR: mDNS module not available", err=True)
        raise typer.Exit(code=1)

    if not ZEROCONF_AVAILABLE:
        typer.echo("ERROR: zeroconf package not installed (pip install zeroconf)", err=True)
        raise typer.Exit(code=1)

    # Load own did to filter self
    vault = Path(os.environ.get("SISOUL_VAULT", "~/.sisoul")).expanduser()
    own_did = None
    did_file = vault / "did_key.json"
    if did_file.exists():
        try:
            own_did = json.loads(did_file.read_text()).get("did")
        except Exception:
            pass

    # Load petnames
    petnames: dict[str, str] = {}
    pet_file = vault / "petnames.json"
    if pet_file.exists():
        try:
            petnames = json.loads(pet_file.read_text())
        except Exception:
            pass

    typer.echo(f"\n  Scanning LAN for sisoul peers... ({timeout}s)\n")
    peers = scan(timeout=timeout, own_did_key=own_did)

    if not peers:
        typer.echo("  (no sisoul peers found on LAN)\n")
        if json_output:
            typer.echo(json.dumps([]))
        return

    # Enrich with petname status
    enriched = []
    for p in peers:
        did = p.get("did_key", "")
        enriched.append({
            **p,
            "petname_saved": petnames.get(did),
            "is_friend": did in petnames,
        })

    if json_output:
        typer.echo(json.dumps(enriched, ensure_ascii=False, indent=2))
        return

    typer.echo(f"  Found {len(enriched)} peer(s):\n")
    for p in enriched:
        marker = "★" if p["is_friend"] else " "
        nick = p["petname_saved"] or p.get("petname_hint", "?")
        did_short = (p["did_key"][:16] + "…" + p["did_key"][-4:]) if p["did_key"] else "?"
        typer.echo(f"  {marker} {nick:20} {did_short}  @ {p.get('hostname', '?')}")
    typer.echo("")
    typer.echo("  ★ = already in your petnames (added friend)")
    typer.echo("    no star = LAN peer, not yet added")
    typer.echo("")

    if save_petname:
        from sisoul.friend.petname import PetnameStore
        store = PetnameStore().load()
        for p in enriched:
            if p["is_friend"]:
                continue
            did = p["did_key"]
            hint = p.get("petname_hint", "")
            answer = typer.prompt(f"  Save petname for {did[:16]}… (hint: '{hint}') [empty to skip]", default="")
            if answer:
                store.set(did, answer)
                typer.echo(f"    → saved: {did[:16]}… = {answer}")
