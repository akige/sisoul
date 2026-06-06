"""`sisoul kudos` CLI: balance / history / grant / decay.

Kudos is a non-transferable reciprocity counter (see docs/INCENTIVE-DESIGN.md).
"""
from __future__ import annotations
import json
import time
from typing import Optional

import typer

from sisoul.friend.kudos import KudosStore, KudosInsufficient, KudosError

kudos_app = typer.Typer(help="Kudos: non-transferable reciprocity counter for borrow LLM (§4.10-compatible).")


@kudos_app.command("balance")
def cmd_balance(
    peer_did: Optional[str] = typer.Argument(None, help="filter by peer; default = list all"),
    json_output: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """Show kudos balance for a peer (or list all peers)."""
    with KudosStore() as ks:
        if peer_did:
            bal = ks.balance(peer_did)
            if json_output:
                typer.echo(json.dumps({"peer_did": peer_did, "balance": bal}))
            else:
                typer.echo(f"{peer_did}: {bal:.2f} kudos")
        else:
            all_b = ks.all_balances()
            if json_output:
                typer.echo(json.dumps(all_b, indent=2))
            else:
                if not all_b:
                    typer.echo("(no kudos balances yet — lend to a friend to earn)")
                    return
                typer.echo(f"{'peer_did':<60}{'balance':>10}")
                typer.echo("-" * 70)
                for did, bal in sorted(all_b.items(), key=lambda x: -x[1]):
                    short = did if len(did) <= 60 else did[:57] + "..."
                    typer.echo(f"{short:<60}{bal:>10.2f}")


@kudos_app.command("history")
def cmd_history(
    peer_did: Optional[str] = typer.Argument(None, help="filter by peer; default = all"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """Show recent kudos ledger entries."""
    with KudosStore() as ks:
        entries = ks.history(peer_did=peer_did, limit=limit)
        if json_output:
            typer.echo(json.dumps([{
                "id": e.id, "ts": e.ts, "peer_did": e.peer_did,
                "delta": e.delta, "reason": e.reason,
                "balance_after": e.balance_after,
            } for e in entries], indent=2))
            return
        if not entries:
            typer.echo("(no kudos history)")
            return
        typer.echo(f"{'when':<20}{'peer':<25}{'delta':>10}{'balance':>12}  reason")
        typer.echo("-" * 100)
        for e in entries:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.ts))
            short = e.peer_did[:22] + "..." if len(e.peer_did) > 25 else e.peer_did
            sign = "+" if e.delta >= 0 else ""
            typer.echo(f"{when:<20}{short:<25}{sign}{e.delta:>9.2f}{e.balance_after:>12.2f}  {e.reason}")


@kudos_app.command("grant")
def cmd_grant(
    peer_did: str = typer.Argument(..., help="peer did:key to grant kudos to"),
    amount: float = typer.Argument(..., help="amount to grant (positive = peer owes you more; negative = you owe them more)"),
    reason: str = typer.Option("manual grant", "--reason", "-r"),
) -> None:
    """Manually adjust a peer's kudos balance (test/seed/dispute).

    Note: this is a local adjustment only. It does NOT sync to the peer's
    daemon. In production this is for test seeding and disputed-correction;
    do not use it to fabricate reciprocity.
    """
    with KudosStore() as ks:
        new = ks.grant(peer_did, amount, reason)
        typer.echo(f"OK granted {amount:+.2f} kudos to {peer_did}, new balance: {new:.2f}")


@kudos_app.command("decay")
def cmd_decay(
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Apply 5%/month decay to all positive balances. Idempotent.

    Normally run by a daily LaunchAgent / systemd timer.
    """
    with KudosStore() as ks:
        if dry_run:
            balances = ks.all_balances()
            positive = {d: b for d, b in balances.items() if b > 0}
            typer.echo(f"Would decay {len(positive)} positive balances:")
            for d, b in positive.items():
                typer.echo(f"  {d}: {b:.2f}")
            return
        changes = ks.apply_decay()
        if not changes:
            typer.echo("No positive balances to decay (or no time has passed).")
            return
        for peer, (old, new, factor) in changes.items():
            typer.echo(f"  {peer[:30]}...  {old:>8.2f} → {new:>8.2f}  (×{factor:.4f})")
        typer.echo(f"OK decayed {len(changes)} balance(s)")
