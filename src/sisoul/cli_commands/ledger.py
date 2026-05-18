"""sisoul ledger · CLI 子 app (Phase 4 W66-W76 · 波 5 dev-D).

子命令:
  sisoul ledger show <friend_did> [--self-did X] [--threshold T] [--limit N]
  sisoul ledger imbalance --self-did X [--threshold T]
  sisoul ledger stats
  sisoul ledger friends [--self-did X]
  sisoul ledger record <friend_did> <resource> <amount> --self-did X --model M --direction borrow|lend

接入: 父集成在 cli.py 用
    from sisoul.cli_commands.ledger import ledger_app
    app.add_typer(ledger_app, name="ledger")
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

import typer

from sisoul.friend.ledger import (
    DEFAULT_IMBALANCE_THRESHOLD,
    ReciprocityLedger,
)

ledger_app = typer.Typer(
    name="ledger",
    help="互惠 ledger 查看 + 不平衡告警 (Phase 4 W66-W76, §28 §3.4).",
    no_args_is_help=True,
)


def _fmt_ts(ts: Optional[int]) -> str:
    if not ts:
        return "-"
    try:
        return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return str(ts)


@ledger_app.command("show")
def cmd_show(
    friend_did: str = typer.Argument(...),
    self_did: str = typer.Option(..., "--self-did", "-s", help="本机 self DID"),
    threshold: float = typer.Option(DEFAULT_IMBALANCE_THRESHOLD, "--threshold", "-t"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """显示跟某 friend 的 ledger 摘要 + 最近 N 条 entries."""
    led = ReciprocityLedger(self_did=self_did)
    try:
        bal = led.query_balance(friend_did, threshold=threshold)
        entries = led.list_entries(
            friend_did=friend_did, self_did=self_did, limit=limit
        )
    finally:
        led.close()

    if json_out:
        typer.echo(
            json.dumps(
                {
                    "balance": bal.to_dict(),
                    "entries": [e.to_dict() for e in entries],
                },
                ensure_ascii=False, indent=2,
            )
        )
        return

    typer.echo(f"{self_did} ↔ {friend_did}  (互惠 ledger, threshold={threshold})")
    typer.echo(
        f"  借入 (me=borrower): {bal.borrowed_total}  "
        f"by_resource={bal.borrowed_from_friend}"
    )
    typer.echo(
        f"  借出 (me=lender)  : {bal.lent_total}  "
        f"by_resource={bal.lent_to_friend}"
    )
    typer.echo(
        f"  比率: ratio={bal.ratio:.3f}  inv={bal.ratio_inverted:.3f}  "
        f"direction={bal.direction_imbalance}"
    )
    if bal.imbalance_warning:
        typer.echo(
            f"  ⚠️ 不平衡告警: {bal.direction_imbalance}  (超过 threshold={threshold})"
        )
    typer.echo(f"  最近活动: {_fmt_ts(bal.last_activity_ts)}  entries={bal.entry_count}")
    if entries:
        typer.echo(f"\n  最近 {len(entries)} 条 entries:")
        for e in entries:
            mark = "←" if e.borrower_did == self_did else "→"
            typer.echo(
                f"    {_fmt_ts(e.ts)}  {mark}  {e.direction:6s}  "
                f"{e.resource_type:9s}  amount={e.amount}  model={e.model_or_skill_id}  "
                f"chain={e.onchain_status}"
            )


@ledger_app.command("imbalance")
def cmd_imbalance(
    self_did: str = typer.Option(..., "--self-did", "-s"),
    threshold: float = typer.Option(DEFAULT_IMBALANCE_THRESHOLD, "--threshold", "-t"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """列所有 friend 的不平衡 warning."""
    led = ReciprocityLedger(self_did=self_did)
    try:
        warnings = led.list_imbalance_warnings(threshold=threshold)
    finally:
        led.close()
    data = [w.to_dict() for w in warnings]
    if json_out:
        typer.echo(json.dumps(
            {"self_did": self_did, "threshold": threshold, "count": len(data),
             "warnings": data},
            ensure_ascii=False, indent=2,
        ))
        return
    if not warnings:
        typer.echo(f"无 imbalance warning (threshold={threshold})")
        return
    typer.echo(f"⚠️ 检测到 {len(warnings)} 条 imbalance warning:")
    for w in warnings:
        typer.echo(
            f"  {w.friend_did}  direction={w.direction}  ratio={w.ratio:.3f}  "
            f"borrowed={w.borrowed_total}  lent={w.lent_total}"
        )


@ledger_app.command("stats")
def cmd_stats(json_out: bool = typer.Option(False, "--json")) -> None:
    """全局 ledger stats (按 onchain status 分桶)."""
    led = ReciprocityLedger()
    try:
        s = led.stats()
    finally:
        led.close()
    if json_out:
        typer.echo(json.dumps(s, ensure_ascii=False, indent=2))
        return
    typer.echo("ledger stats:")
    for k, v in s.items():
        typer.echo(f"  {k:12s}: {v}")


@ledger_app.command("friends")
def cmd_friends(
    self_did: Optional[str] = typer.Option(None, "--self-did", "-s"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """列所有 ledger 涉及的对端 DID."""
    led = ReciprocityLedger(self_did=self_did)
    try:
        fs = led.list_friends()
    finally:
        led.close()
    if json_out:
        typer.echo(json.dumps({"count": len(fs), "friends": fs},
                              ensure_ascii=False, indent=2))
        return
    if not fs:
        typer.echo("ledger 无 entries")
        return
    for f in fs:
        typer.echo(f"  {f}")


@ledger_app.command("record")
def cmd_record(
    friend_did: str = typer.Argument(...),
    resource: str = typer.Argument(..., help="llm_quota / ai_skill / compute"),
    amount: int = typer.Argument(...),
    self_did: str = typer.Option(..., "--self-did", "-s"),
    model: str = typer.Option("claude-opus-4-7", "--model", "-m"),
    direction: str = typer.Option("borrow", "--direction", "-d",
                                  help="borrow (me 借入) / lend (me 借出)"),
    no_onchain: bool = typer.Option(False, "--no-onchain"),
    note: Optional[str] = typer.Option(None, "--note"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """手动写一条 ledger entry (test / 离线补录)."""
    if direction not in ("borrow", "lend"):
        typer.echo("--direction 必须 borrow / lend", err=True)
        raise typer.Exit(code=1)
    if resource not in ("llm_quota", "ai_skill", "compute"):
        typer.echo("resource 必须 llm_quota / ai_skill / compute", err=True)
        raise typer.Exit(code=1)
    if direction == "borrow":
        borrower, lender = self_did, friend_did
    else:
        borrower, lender = friend_did, self_did
    led = ReciprocityLedger(self_did=self_did)
    try:
        entry = led.record_usage(
            borrower_did=borrower,
            lender_did=lender,
            resource_type=resource,  # type: ignore[arg-type]
            amount=amount,
            model_or_skill_id=model,
            direction=direction,  # type: ignore[arg-type]
            note=note,
            enqueue_onchain=not no_onchain,
            actor_did=self_did,
        )
    finally:
        led.close()
    if json_out:
        typer.echo(json.dumps(entry.to_dict(), ensure_ascii=False, indent=2))
    else:
        typer.echo(
            f"recorded: {entry.entry_id}  {direction}  {resource} amount={amount}  "
            f"chain={entry.onchain_status}"
        )
