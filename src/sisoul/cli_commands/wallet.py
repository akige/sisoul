"""`sisoul wallet` CLI: show / set receive addresses for incentive_mode=micropay.

This is RECEIVE-ONLY. sisoul does not custody, sign, or watch the chain.
Use Trust / TronLink / SafePal etc. as your wallet.
"""
from __future__ import annotations
import json
from typing import Optional

import typer

from sisoul.wallet import WalletStore, WalletError

wallet_app = typer.Typer(help="Local receive-address store for USDT-TRC20 micropay (no custody).")


@wallet_app.command("show")
def cmd_show(
    json_output: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """Show all configured receive addresses."""
    addrs = WalletStore().get()
    d = addrs.to_dict()
    if json_output:
        typer.echo(json.dumps(d, indent=2))
        return
    if not any(d.values()):
        typer.echo("(no receive addresses set)")
        typer.echo("Set USDT-TRC20: sisoul wallet set-usdt-trc20 T...")
        return
    typer.echo("Receive addresses (local, you control):")
    if d["usdt_trc20"]:
        typer.echo(f"  USDT (TRC20): {d['usdt_trc20']}")
        typer.echo(f"               tronscan: https://tronscan.org/#/address/{d['usdt_trc20']}")
    if d["usdt_erc20"]:
        typer.echo(f"  USDT (ERC20): {d['usdt_erc20']}")
    if d["btc_taproot"]:
        typer.echo(f"  BTC (Taproot): {d['btc_taproot']}")


@wallet_app.command("set-usdt-trc20")
def cmd_set_trc20(
    address: str = typer.Argument(..., help="TRC20 T-address (34 chars starting with T)"),
) -> None:
    """Set USDT-TRC20 receive address. Validated for shape, not chain liveness."""
    try:
        WalletStore().set_usdt_trc20(address)
    except WalletError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"OK USDT-TRC20 receive address set: {address}")
    typer.echo(f"Use this in a per-friend perm with incentive_mode=micropay.")


@wallet_app.command("set-usdt-erc20")
def cmd_set_erc20(
    address: str = typer.Argument(..., help="ERC20 0x-address"),
) -> None:
    """Set USDT-ERC20 receive address."""
    try:
        WalletStore().set_usdt_erc20(address)
    except WalletError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"OK USDT-ERC20 receive address set: {address}")


@wallet_app.command("clear")
def cmd_clear() -> None:
    """Erase all stored receive addresses."""
    WalletStore().clear()
    typer.echo("OK all receive addresses cleared")
