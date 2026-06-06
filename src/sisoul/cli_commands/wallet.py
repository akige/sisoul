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


@wallet_app.command("inbound")
def cmd_inbound(
    address: str = typer.Option(None, "--address", "-a",
        help="TRC20 receive address (default: read from `sisoul wallet show`)"),
    expected: float = typer.Option(None, "--expected", "-e",
        help="filter to txs matching this USDT amount (±5%%)"),
    max_age_hours: float = typer.Option(24.0, "--max-age-hours",
        help="only show txs within this age window"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json", "-j"),
) -> None:
    """List recent inbound USDT-TRC20 transfers to your receive address (via TronGrid)."""
    import json
    from sisoul.wallet.chain_watcher import list_inbound_usdt, ChainWatcherError
    if address is None:
        addrs = WalletStore().get()
        address = addrs.usdt_trc20
        if not address:
            typer.echo(
                "ERROR: no --address passed and no USDT-TRC20 address in vault. "
                "Run `sisoul wallet set-usdt-trc20 T...` first, or pass --address.",
                err=True,
            )
            raise typer.Exit(code=1)
    try:
        txs = list_inbound_usdt(
            address,
            limit=limit,
            max_age_seconds=max_age_hours * 3600.0,
            expected_value_usdt=expected,
        )
    except ChainWatcherError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(code=2)
    if json_output:
        typer.echo(json.dumps([{
            "tx_id": t.tx_id, "from": t.from_addr, "to": t.to_addr,
            "value_usdt": t.value_usdt, "age_seconds": t.age_seconds,
            "tronscan": t.tronscan_url,
        } for t in txs], indent=2))
        return
    if not txs:
        msg = "No matching inbound USDT-TRC20 transfers found"
        if expected:
            msg += f" near {expected:.4f} USDT"
        msg += f" in the last {max_age_hours:.1f}h."
        typer.echo(msg)
        return
    typer.echo(f"Inbound USDT-TRC20 transfers to {address}:")
    typer.echo(f"{'when':<16}{'value':>10}  {'from':<25}{'tx':<20}")
    typer.echo("-" * 80)
    for t in txs:
        age_min = t.age_seconds / 60.0
        from_short = (t.from_addr[:22] + "...") if len(t.from_addr) > 25 else t.from_addr
        when = f"{age_min:.1f}m ago" if age_min < 60 else f"{age_min/60:.1f}h ago"
        typer.echo(f"{when:<16}{t.value_usdt:>10.4f}  {from_short:<25}{t.tx_id[:16]}...")
    typer.echo(f"\nView on tronscan: https://tronscan.org/#/address/{address}")
