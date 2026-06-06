"""sisoul borrow · CLI 子 app (Phase 4 W66-W76 · 波 5 dev-D).

替换波 1 cli.py 里 stub 的 ``@app.command() def borrow(...)``.

子命令:
  sisoul borrow run <friend> <resource> <amount> [--model M] [--prompt P] [--force-mode X]
      → 完整 borrow 流程 (同步; per-request 模式等 timeout)
  sisoul borrow proxy <friend> --model M [--base-url URL]
      → 起长寿命 proxy session (ANTHROPIC_BASE_URL 用)
  sisoul borrow proxy-list
  sisoul borrow proxy-stop <session_id>

接入: 父集成在 cli.py 用
    from sisoul.cli_commands.borrow import borrow_app
    app.add_typer(borrow_app, name="borrow")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from sisoul.friend.borrow import (
    borrow_resource,
    list_proxy_sessions,
    start_proxy_session,
    stop_proxy_session,
)

borrow_app = typer.Typer(
    name="borrow",
    help="借朋友 LLM quota / AI 技能 (Phase 4 W66-W76, §28 §3.5).",
    no_args_is_help=True,
)


def _proxy_env_var(provider: str) -> str:
    p = provider.lower()
    if p == "anthropic":
        return "ANTHROPIC_BASE_URL"
    if p == "openai":
        return "OPENAI_BASE_URL"
    return f"{p.upper()}_BASE_URL"


def _print_borrow_session(s: Any) -> None:  # type: ignore[no-untyped-def]
    typer.echo(f"borrow session: {s.session_id}")
    typer.echo(f"  status      : {s.status}")
    typer.echo(f"  mode        : {s.mode}")
    typer.echo(f"  borrower    : {s.borrower_did}")
    typer.echo(f"  lender      : {s.lender_did}")
    typer.echo(f"  resource    : {s.resource_type} amount={s.amount} model={s.model}")
    inc_mode = getattr(s, "incentive_mode", "gift")
    typer.echo(f"  incentive   : {inc_mode}")
    if inc_mode == "kudos":
        typer.echo(f"    kudos cost   : {getattr(s, 'kudos_cost', 0):.2f}")
        if getattr(s, "kudos_balance_after", None) is not None:
            typer.echo(f"    new balance  : {s.kudos_balance_after:.2f}")
    elif inc_mode == "micropay":
        receipt = getattr(s, "incentive_receipt", {}) or {}
        typer.echo(f"    USDT cost    : {receipt.get('usdt_amount', 0):.4f}  ({receipt.get('network', 'TRC20')})")
        typer.echo(f"    payout addr  : {receipt.get('payout_address', '')}")
        if receipt.get("tronscan"):
            typer.echo(f"    verify on    : {receipt['tronscan']}")
        if receipt.get("instruction"):
            typer.echo(f"    instruction  : {receipt['instruction']}")
    if s.lend_request_id:
        typer.echo(f"  lend req    : {s.lend_request_id}")
    if s.proxy_method:
        typer.echo(f"  proxy via   : {s.proxy_method}")
    if s.proxy_text:
        prev = s.proxy_text if len(s.proxy_text) < 200 else s.proxy_text[:200] + "..."
        typer.echo(f"  proxy text  : {prev}")
    if s.tokens_used:
        typer.echo(f"  tokens used : {s.tokens_used}")
    if s.ledger_entry_id:
        typer.echo(f"  ledger entry: {s.ledger_entry_id}")
    if getattr(s, "note", None):
        typer.echo(f"  note        : {s.note}")
    if s.error:
        typer.echo(f"  error       : {s.error}")


# ── 子命令 (显式) ───────────────────────────────────────────────────────────


@borrow_app.command("run")
def cmd_run(
    friend_did: str = typer.Argument(...),
    resource: str = typer.Argument(...),
    amount: int = typer.Argument(...),
    model: str = typer.Option("claude-opus-4-7", "--model", "-m"),
    prompt: str = typer.Option("", "--prompt", "-p"),
    force_mode: Optional[str] = typer.Option(None, "--force-mode"),
    emergency: bool = typer.Option(False, "--emergency"),
    timeout: float = typer.Option(30.0, "--timeout"),
    no_onchain: bool = typer.Option(False, "--no-onchain"),
    json_out: bool = typer.Option(False, "--json"),
    dry_run: bool = typer.Option(False, "--dry-run",
        help="show incentive quote (kudos cost / USDT amount + payout address) without sending the request"),
) -> None:
    """完整 borrow 流程 (一次性, 走 lend store + proxy + ledger)."""
    sess = borrow_resource(
        borrower_did="me",
        lender_did=friend_did,
        resource_type=resource if resource in (
            "llm_quota", "ai_skill", "compute"
        ) else "ai_skill",
        amount=amount,
        model=model if resource == "llm_quota" else resource,
        prompt=prompt,
        force_mode=force_mode,  # type: ignore[arg-type]
        emergency_flag=emergency,
        per_request_timeout_sec=timeout,
        dry_run=dry_run,
        enqueue_onchain=not no_onchain,
    )
    if json_out:
        typer.echo(json.dumps(sess.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_borrow_session(sess)


@borrow_app.command("proxy")
def cmd_proxy(
    friend_did: str = typer.Argument(...),
    provider: str = typer.Option(
        "anthropic", "--provider",
        help="anthropic / openai (决定 env var hint)",
    ),
    model: str = typer.Option("claude-opus-4-7", "--model", "-m"),
    base_url: str = typer.Option("http://127.0.0.1:9876", "--base-url"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """起 proxy session: ANTHROPIC_BASE_URL=<endpoint> claude 透明走朋友 quota."""
    sess = start_proxy_session(
        borrower_did="me", lender_did=friend_did, model=model, base_url=base_url
    )
    payload = {
        "session": sess.to_dict(),
        "env_hint": f"{_proxy_env_var(provider)}={sess.endpoint}",
    }
    if json_out:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"proxy session: {sess.session_id}")
        typer.echo(f"  endpoint  : {sess.endpoint}")
        typer.echo(f"  env hint  : {payload['env_hint']}")
        typer.echo("用法 e.g.:")
        typer.echo(f"  {payload['env_hint']} claude")


@borrow_app.command("proxy-list")
def cmd_proxy_list(json_out: bool = typer.Option(False, "--json")) -> None:
    """列所有 active proxy session."""
    sessions = [s.to_dict() for s in list_proxy_sessions()]
    if json_out:
        typer.echo(json.dumps({"count": len(sessions), "sessions": sessions},
                              ensure_ascii=False, indent=2))
    else:
        if not sessions:
            typer.echo("no active proxy session")
            return
        for s in sessions:
            typer.echo(
                f"- {s['session_id']}  status={s['status']}  "
                f"lender={s['lender_did']}  model={s['model']}  endpoint={s['endpoint']}"
            )


@borrow_app.command("proxy-stop")
def cmd_proxy_stop(
    session_id: str = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """停 proxy session."""
    sess = stop_proxy_session(session_id)
    if sess is None:
        typer.echo(f"session {session_id} 不存在", err=True)
        raise typer.Exit(code=1)
    if json_out:
        typer.echo(json.dumps(sess.to_dict(), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"stopped: {sess.session_id} (status={sess.status})")


# 为 typing 补 Any (避免 typed 顶部漏 import; _print 用 Any 简化)
from typing import Any  # noqa: E402
