"""sisoul lend · CLI 子 app (Phase 4 W66-W76 · 波 5 dev-D).

替换波 1 cli.py 里 stub 的 ``@app.command() def lend(action, request_id)``.

子命令 (复用波 1 stub 的 action 风格):
  sisoul lend list                          列 pending lend requests
  sisoul lend approve <request_id>          批准
  sisoul lend deny <request_id> [--reason]  拒绝
  sisoul lend history [--status X]          历史

接入: 父集成在 cli.py 用
    from sisoul.cli_commands.lend import lend_app
    app.add_typer(lend_app, name="lend")
"""

from __future__ import annotations

import json
from typing import Optional

import typer

from sisoul.friend.lend import (
    LendStore,
    RequestNotFoundError,
    RequestStateError,
    approve_lend,
    deny_lend,
    list_pending_requests,
)

lend_app = typer.Typer(
    name="lend",
    help="批准/拒绝/查看朋友发来的 borrow request (Phase 4 W66-W76, §28 §3.5).",
    no_args_is_help=True,
)


def _print_req(r: dict) -> None:
    typer.echo(f"- {r['id']}  status={r['status']}  mode={r.get('mode', '')}")
    typer.echo(
        f"    borrower={r['borrower_did']} → lender={r['lender_did']}  "
        f"resource={r['resource_type']} amount={r['amount']} model={r['model']}"
    )
    if r.get("denied_reason"):
        typer.echo(f"    denied_reason: {r['denied_reason']}")


@lend_app.command("list")
def cmd_list(json_out: bool = typer.Option(False, "--json")) -> None:
    """列 pending lend requests (待 Bob 批准)."""
    reqs = list_pending_requests()
    data = [r.to_dict() for r in reqs]
    if json_out:
        typer.echo(json.dumps({"count": len(data), "pending": data},
                              ensure_ascii=False, indent=2))
        return
    if not data:
        typer.echo("no pending lend requests")
        return
    for d in data:
        _print_req(d)


@lend_app.command("approve")
def cmd_approve(
    request_id: str = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """批准某条 lend request."""
    try:
        req = approve_lend(request_id)
    except RequestNotFoundError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)
    except RequestStateError as e:
        typer.echo(f"❌ 状态错误: {e}", err=True)
        raise typer.Exit(code=2)
    if json_out:
        typer.echo(json.dumps(req.to_dict(), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"approved: {req.id}")
        _print_req(req.to_dict())


@lend_app.command("deny")
def cmd_deny(
    request_id: str = typer.Argument(...),
    reason: Optional[str] = typer.Option(None, "--reason", "-r"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """拒绝某条 lend request."""
    try:
        req = deny_lend(request_id, reason=reason)
    except RequestNotFoundError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)
    except RequestStateError as e:
        typer.echo(f"❌ 状态错误: {e}", err=True)
        raise typer.Exit(code=2)
    if json_out:
        typer.echo(json.dumps(req.to_dict(), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"denied: {req.id}  reason={req.denied_reason or '-'}")


@lend_app.command("history")
def cmd_history(
    status: Optional[str] = typer.Option(
        None, "--status", "-s",
        help="过滤: pending / approved / denied / expired / completed",
    ),
    limit: int = typer.Option(50, "--limit", "-n"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """列历史 lend requests."""
    store = LendStore()
    try:
        reqs = store.list_all(limit=limit, status=status)
    finally:
        store.close()
    data = [r.to_dict() for r in reqs]
    if json_out:
        typer.echo(json.dumps({"count": len(data), "requests": data},
                              ensure_ascii=False, indent=2))
        return
    if not data:
        typer.echo("no lend requests in history")
        return
    for d in data:
        _print_req(d)


# ── v1.1 auto-approve (micropay USDT chain-watcher) ────────────────────────

auto_app = typer.Typer(
    name="auto-approve",
    help="自动批准 micropay borrow request (USDT 到账后自动 approve).",
    no_args_is_help=True,
)
lend_app.add_typer(auto_app, name="auto-approve")


@auto_app.command("enable")
def cmd_auto_enable() -> None:
    """开启 lend auto-approve 后台 daemon (下次启动生效).

    daemon 起来后每 30s 扫一次 pending micropay request, 查 TronGrid
    确认 USDT 到账后自动 approve_lend + 在 GossipSub 上发 ACK 给 borrower.
    """
    from sisoul.friend.lend_auto_approve import set_enabled
    set_enabled(True)
    typer.echo("OK lend auto-approve ENABLED.")
    typer.echo("  restart `sisoul daemon` for the change to take effect.")
    typer.echo("  watcher polls TronGrid every 30s; matched tx → approve_lend.")


@auto_app.command("disable")
def cmd_auto_disable() -> None:
    """关闭 lend auto-approve (后续 micropay request 改为手工 approve)."""
    from sisoul.friend.lend_auto_approve import set_enabled
    set_enabled(False)
    typer.echo("OK lend auto-approve DISABLED.")
    typer.echo("  restart `sisoul daemon` for the change to take effect.")


@auto_app.command("status")
def cmd_auto_status(
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """显示 auto-approve 当前开关状态 + (若运行中) 上次轮询时间."""
    from sisoul.friend.lend_auto_approve import is_enabled, _vault_config_path
    enabled = is_enabled()
    p = _vault_config_path()
    payload = {
        "enabled": enabled,
        "config_path": str(p),
        "config_exists": p.exists(),
    }
    if json_out:
        typer.echo(json.dumps(payload, indent=2))
        return
    typer.echo(f"auto-approve: {'ENABLED' if enabled else 'DISABLED'}")
    typer.echo(f"  config:    {p}")
    if not p.exists():
        typer.echo("  (config absent — defaults to DISABLED)")
