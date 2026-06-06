"""`sisoul username` — pick a Telegram-style handle so peers can `chat send @name`.

Workstream B (v1.0-stable): the handle->did:key mapping is an EAS attestation on
Optimism (per user decision 2026-06-06: EAS mainnet). EAS is permissionless
trustless infra — sisoul runs no directory server. The signing key is derived from
your BIP-39 seed (path m/44'/60'/0'/0/0); you fund that address with a little OP gas
and pay for your own one-time registration. First-claim-wins per name.

`register` is dry-run by default (shows the plan + the address to fund). Add
`--on-chain` to actually broadcast.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import typer

from sisoul.cli_commands.chat import _resolve_local_did
from sisoul.onchain.username_eas import (
    EVM_DERIVATION_PATH,
    MainnetBlockedError,
    UsernameEASError,
    discover as eas_discover,
    load_evm_account,
    register_username,
    resolve_username as eas_resolve_username,
)

username_app = typer.Typer(help="Register / resolve a username via EAS (Optimism) — Telegram-style handle.")

DEFAULT_NETWORK = os.environ.get("SISOUL_USERNAME_NETWORK", "optimism-mainnet")


def _username_file() -> Path:
    vault = Path(os.environ.get("SISOUL_VAULT", str(Path.home() / ".sisoul"))).expanduser()
    return vault / "username.json"


@username_app.command("eas-address")
def cmd_eas_address(
    json_output: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """Show the EVM address derived from your BIP-39 seed (fund it with OP gas)."""
    try:
        acct = load_evm_account()
    except OSError as e:  # FileNotFoundError / PermissionError (loose seed perms) / ...
        typer.echo(f"ERROR: cannot read seed ({e}). Run `sisoul init` (seed must be chmod 600).", err=True)
        raise typer.Exit(code=1)
    if json_output:
        typer.echo(json.dumps(acct.public()))
        return
    typer.echo(f"EVM address (derived from your BIP-39 seed): {acct.address}")
    typer.echo(f"  derivation path: {EVM_DERIVATION_PATH}")
    typer.echo("  Fund this with ~$1 of Optimism ETH to pay your own gas for `username register`.")
    typer.echo("  sisoul never holds this key — it is re-derived from your seed on demand.")


@username_app.command("register")
def cmd_register(
    name: str = typer.Argument(..., help="username (2-32 chars, [a-zA-Z0-9_-])"),
    bio: str = typer.Option("", "--bio", "-b", help="(reserved for v1.1 profile attestation)"),
    network: str = typer.Option(DEFAULT_NETWORK, "--network", help="optimism-mainnet | optimism-sepolia"),
    on_chain: bool = typer.Option(False, "--on-chain", help="actually broadcast (default: dry-run preview)"),
) -> None:
    """Claim a username as an EAS attestation (first-claim-wins).

    Default is a dry-run that shows the plan + the address to fund. Re-run with
    --on-chain (after funding) to broadcast. Mainnet also needs EAS_ALLOW_MAINNET=1.
    """
    import re

    if not re.match(r"^[a-zA-Z0-9_\-]{2,32}$", name):
        typer.echo(f"ERROR: username must match [a-zA-Z0-9_-]{{2,32}}: {name!r}", err=True)
        raise typer.Exit(code=1)

    try:
        did_key = _resolve_local_did()
    except Exception as e:  # noqa: BLE001
        typer.echo(f"ERROR: cannot resolve your did:key (run `sisoul init`?): {e}", err=True)
        raise typer.Exit(code=1)

    allow_mainnet = "mainnet" in network
    try:
        res = register_username(
            name, did_key, network=network, dry_run=not on_chain, allow_mainnet=allow_mainnet,
        )
    except MainnetBlockedError:
        typer.echo(
            "ERROR: mainnet registration is double-gated. To broadcast on mainnet:\n"
            "  export EAS_ALLOW_MAINNET=1\n"
            f"  sisoul username register {name} --network optimism-mainnet --on-chain",
            err=True,
        )
        raise typer.Exit(code=2)
    except (UsernameEASError, OSError) as e:  # incl. missing / loose-perm seed
        typer.echo(f"ERROR: register failed: {e}", err=True)
        raise typer.Exit(code=2)

    if res["method"] == "dry-run":
        typer.echo(f"[dry-run] would register @{name} -> {did_key}")
        typer.echo(f"  network:    {res['network']} (chain_id {res['chain_id']})")
        typer.echo(f"  EAS:        {res['eas_contract']}")
        typer.echo(f"  schema_uid: {res['schema_uid']}")
        typer.echo(f"  pay from:   {res['evm_address']}  ({EVM_DERIVATION_PATH})")
        typer.echo("")
        typer.echo("  To broadcast: fund that address with a little OP ETH, then:")
        gate = "EAS_ALLOW_MAINNET=1 " if res["is_mainnet"] else ""
        typer.echo(f"    {gate}sisoul username register {name} --network {res['network']} --on-chain")
        return

    # live
    f = _username_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(
        {"username": name, "did": did_key, "network": res["network"],
         "tx_hash": res["tx_hash"], "schema_uid": res["schema_uid"]}, indent=2))
    typer.echo(f"OK @{name} registered on {res['network']} -> {did_key}")
    typer.echo(f"  tx:    {res['tx_hash']}  (block {res.get('block')})")
    typer.echo(f"  gas:   {res.get('gas_used')}")


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
    if payload.get("network"):
        typer.echo(f"  network: {payload['network']}")
    if payload.get("tx_hash"):
        typer.echo(f"  tx: {payload['tx_hash']}")


@username_app.command("resolve")
def cmd_resolve(
    name: str = typer.Argument(..., help="username to look up"),
    network: str = typer.Option(DEFAULT_NETWORK, "--network", help="optimism-mainnet | optimism-sepolia"),
) -> None:
    """Resolve a username to a did:key via an EAS indexer (first-claim-wins)."""
    try:
        did = eas_resolve_username(name, network=network)
    except UsernameEASError as e:
        typer.echo(f"ERROR: EAS lookup failed ({network}): {e}", err=True)
        raise typer.Exit(code=2)
    if did is None:
        typer.echo(f"NO MATCH: @{name} is not registered on {network}.")
        raise typer.Exit(code=1)
    typer.echo(f"@{name} -> {did}")


@username_app.command("discover")
def cmd_discover(
    limit: int = typer.Option(50, "--limit", "-n", help="max recent claims to list"),
    network: str = typer.Option(DEFAULT_NETWORK, "--network", help="optimism-mainnet | optimism-sepolia"),
    json_output: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """List recent username claims on EAS (username + did + when)."""
    try:
        rows = eas_discover(network=network, limit=limit)
    except UsernameEASError as e:
        typer.echo(f"ERROR: EAS discover failed ({network}): {e}", err=True)
        raise typer.Exit(code=2)
    if json_output:
        typer.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        typer.echo(f"(no username claims found on {network})")
        return
    for r in rows:
        typer.echo(f"@{r['username']:20} {r['did_key']}  (issued {r.get('issued_at')})")
