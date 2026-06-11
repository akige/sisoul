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


# ── M2 market: 挂牌出借 (写 vault/market_offer.json, daemon 周期广播) ───────

market_app = typer.Typer(
    name="market",
    help="挂牌出借 LLM 额度到去中心化市场 (daemon 周期 GossipSub 广播 offer).",
    no_args_is_help=True,
)
lend_app.add_typer(market_app, name="market")


def _market_offer_path():
    import os
    from pathlib import Path
    vault = Path(os.environ.get("SISOUL_VAULT", str(Path.home() / ".sisoul"))).expanduser()
    return vault / "market_offer.json"


@market_app.command("join")
def cmd_market_join(
    models: str = typer.Option(..., "--models", "-m", help="逗号分隔, e.g. claude-sonnet-4-6,gpt-4o"),
    price: float = typer.Option(0.0, "--price", "-p", help="USDT / 1k tokens (0=gift 免费)"),
    mode: str = typer.Option("strong-tie-auto", "--mode", help="strong-tie-auto / per-request"),
    daily_cap: int = typer.Option(0, "--daily-cap", help="每日 token 上限 (0=不限)"),
    note: str = typer.Option("", "--note"),
) -> None:
    """挂牌: 把本节点的出借 offer 写入 vault, daemon 启动后周期广播到市场。"""
    if mode not in ("strong-tie-auto", "per-request"):
        typer.echo("mode 必须 strong-tie-auto / per-request"); raise typer.Exit(1)
    offer = {
        "models": [m.strip() for m in models.split(",") if m.strip()],
        "price_usdt_per_1k": float(price),
        "mode": mode,
        "daily_cap_tokens": int(daily_cap),
        "note": note,
    }
    p = _market_offer_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(offer, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"  挂牌已写入: {p}")
    typer.echo(f"  models={offer['models']} price={price} USDT/1k mode={mode}")
    typer.echo("  daemon 在跑的话重启生效: pkill -f 'sisoul daemon'; sisoul daemon &")
    if price > 0:
        typer.echo("  ⚠️ 收费出借: 用无转售限制的 provider key (自有 OpenAI/Anthropic), "
                   "勿用 Copilot (ToS 禁转售)")


@market_app.command("leave")
def cmd_market_leave() -> None:
    """下牌: 删除 offer, daemon 停止广播 (已广播的 offer TTL 后自动过期)。"""
    p = _market_offer_path()
    if p.exists():
        p.unlink()
        typer.echo(f"  已下牌: {p} 删除; daemon 重启后停止广播")
    else:
        typer.echo("  当前未挂牌")


@market_app.command("status")
def cmd_market_status() -> None:
    """看本节点当前挂牌状态。"""
    p = _market_offer_path()
    if not p.exists():
        typer.echo("  未挂牌 (sisoul lend market join 挂牌)"); return
    typer.echo(p.read_text(encoding="utf-8"))


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


# ── lend watch (实时通知, 2026-06-11) ───────────────────────────────────────


def _parse_sse_event(block: str) -> tuple[str, dict]:
    """解析一个 SSE 事件块 (``event: X`` + ``data: {...}``) → (event_type, data_dict)。

    纯函数, 不碰网络, 便于单测。无 event 行默认 'message'; data 非 json 返 {}。
    """
    event_type = "message"
    data_raw = ""
    for line in block.splitlines():
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_raw += line[len("data:"):].strip()
    try:
        data = json.loads(data_raw) if data_raw else {}
    except (ValueError, TypeError):
        data = {}
    return event_type, data


def _format_lend_request_event(data: dict) -> str:
    """把一条 lend.request 事件格式化成 lender 可读的一行通知。"""
    emer = "  🚨EMERGENCY" if data.get("emergency_flag") else ""
    return (
        f"📥 有人来借{emer}: req={str(data.get('lend_request_id', '?'))[:12]} "
        f"from={str(data.get('borrower_did', '?'))[:24]} "
        f"{data.get('resource_type', '?')} amount={data.get('amount', '?')} "
        f"model={data.get('model', '?')} mode={data.get('mode', '?')}"
    )


@lend_app.command("watch")
def cmd_watch(
    base_url: str = typer.Option("http://127.0.0.1:9876", "--base-url"),
    once: bool = typer.Option(False, "--once", help="收到第一条事件即退出 (脚本/测试用)"),
) -> None:
    """实时盯 borrow 请求 (连 daemon SSE, Ctrl-C 退出)。

    PWA 一直有实时弹窗; 本命令给纯 CLI lender 同样的实时通知 —
    有人 borrow 立刻打印, 不用反复 `sisoul lend list` 轮询。
    """
    import httpx

    url = base_url.rstrip("/") + "/sisoul/notify/stream"
    typer.echo(f"watching {url} … (Ctrl-C 退出; approve 用 `sisoul lend approve <req>`)")
    try:
        with httpx.stream("GET", url, timeout=None) as resp:
            if resp.status_code != 200:
                typer.echo(f"连不上 daemon (HTTP {resp.status_code}) — 先 `sisoul daemon`?", err=True)
                raise typer.Exit(code=1)
            block = ""
            for line in resp.iter_lines():
                if line:
                    block += line + "\n"
                    continue
                if block.strip():
                    etype, data = _parse_sse_event(block)
                    block = ""
                    if etype == "lend.request":
                        typer.echo(_format_lend_request_event(data))
                        if once:
                            return
                    elif etype in ("lend.update", "borrow.update"):
                        typer.echo(f"· {etype}: {json.dumps(data, ensure_ascii=False)}")
                    # heartbeat 静默
    except KeyboardInterrupt:
        typer.echo("\nstopped.")
    except httpx.HTTPError as e:
        typer.echo(f"SSE 连接错误: {type(e).__name__}: {e} — daemon 在跑吗?", err=True)
        raise typer.Exit(code=1)
