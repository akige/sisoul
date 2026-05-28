"""sisoul notify CLI (Wave B' P1-1 · agent-B3).

子命令:
    sisoul notify list [--limit 50] [--unread] [--kind ...]
        查最近 N 条 notification (本机 SQLite, 不需 daemon 在跑).
    sisoul notify mark-read <notify_id>
        标已读.
    sisoul notify peer-status [<did>]
        查朋友 online/offline (本机 heartbeat 跟踪).
    sisoul notify watch
        实时跟随 (轮询 SQLite 模拟 tail -f; PWA 端走 WebSocket).

主集成: ``cli.py`` 加 ``app.add_typer(notify_app, name="notify")``.
"""

from __future__ import annotations

import json
import time
from typing import Optional

import typer

from sisoul.p2p.push import (
    HeartbeatTracker,
    NotificationStore,
    get_peer_status,
    list_recent_notifications,
)

notify_app = typer.Typer(
    name="notify",
    help="推送 / 在线状态 (Wave B' P1-1, Waku store-and-forward).",
    no_args_is_help=True,
)


@notify_app.command("list")
def cli_notify_list(
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=500),
    unread: bool = typer.Option(False, "--unread", help="只列未读"),
    kind: Optional[str] = typer.Option(
        None, "--kind", help="过滤 kind: borrow_request/borrow_approved/..."
    ),
    target_did: Optional[str] = typer.Option(
        None, "--target", help="过滤 target_did (一般 = self)"
    ),
    as_json: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """查最近 N 条 notification."""
    notifs = list_recent_notifications(
        limit=limit,
        target_did=target_did,
        kind=kind,  # type: ignore[arg-type]
        unread_only=unread,
    )
    if as_json:
        typer.echo(json.dumps([n.to_dict() for n in notifs], indent=2, ensure_ascii=False))
        return
    if not notifs:
        typer.echo("(no notifications)")
        return
    for n in notifs:
        marker = "●" if not n.read else "○"
        age = time.time() - n.ts
        age_str = (
            f"{int(age)}s ago"
            if age < 60
            else f"{int(age/60)}m ago"
            if age < 3600
            else f"{int(age/3600)}h ago"
        )
        src = n.source_did[:24] + ("…" if len(n.source_did) > 24 else "")
        typer.echo(
            f"{marker} [{n.kind:18s}] {src:26s} {age_str:10s} via={','.join(n.delivered_via)}"
        )
        if n.payload:
            short = {k: v for k, v in n.payload.items() if k not in ("source_did", "kind", "ts")}
            if short:
                typer.echo(f"    payload: {json.dumps(short, ensure_ascii=False)[:120]}")


@notify_app.command("mark-read")
def cli_notify_mark_read(
    notify_id: str = typer.Argument(..., help="notify_id (从 list 拿)"),
) -> None:
    store = NotificationStore()
    ok = store.mark_read(notify_id)
    if ok:
        typer.echo(f"marked read: {notify_id}")
    else:
        typer.echo(f"not found: {notify_id}", err=True)
        raise typer.Exit(code=1)


@notify_app.command("peer-status")
def cli_notify_peer_status(
    did: Optional[str] = typer.Argument(None, help="单个 did; 不传列全部"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """查朋友 online/offline (基于心跳, 超 5min 标 offline)."""
    if did:
        st = get_peer_status(did)
        if as_json:
            typer.echo(json.dumps(st.to_dict(), indent=2, ensure_ascii=False))
            return
        age_str = (
            f"{int(st.last_seen_age_sec)}s ago"
            if st.last_seen_age_sec is not None and st.last_seen_age_sec < 3600
            else (
                f"{int(st.last_seen_age_sec/3600)}h ago"
                if st.last_seen_age_sec
                else "never"
            )
        )
        typer.echo(f"{st.did}: {st.state} (last seen: {age_str})")
        return
    # 列全部
    tracker = HeartbeatTracker()
    peers = tracker.list_all()
    if as_json:
        typer.echo(
            json.dumps([p.to_dict() for p in peers], indent=2, ensure_ascii=False)
        )
        return
    if not peers:
        typer.echo("(no peers tracked)")
        return
    for p in peers:
        age_str = (
            f"{int(p.last_seen_age_sec)}s"
            if p.last_seen_age_sec is not None and p.last_seen_age_sec < 60
            else f"{int(p.last_seen_age_sec/60)}m"
            if p.last_seen_age_sec is not None and p.last_seen_age_sec < 3600
            else f"{int(p.last_seen_age_sec/3600)}h"
            if p.last_seen_age_sec
            else "?"
        )
        typer.echo(f"  {p.state:8s} {p.did[:40]:40s} last={age_str}")


@notify_app.command("watch")
def cli_notify_watch(
    interval: float = typer.Option(2.0, "--interval", "-i", help="轮询秒数"),
    target_did: Optional[str] = typer.Option(None, "--target"),
) -> None:
    """实时跟随 (SQLite 轮询 tail -f). PWA 用 WebSocket /sisoul/notify/stream."""
    typer.echo(f"watching notifications (interval={interval}s) Ctrl-C 退出")
    seen: set[str] = set()
    try:
        # 初始填 seen 防止首次冒出全部
        for n in list_recent_notifications(limit=200, target_did=target_did):
            seen.add(n.notify_id)
        while True:
            for n in list_recent_notifications(limit=50, target_did=target_did):
                if n.notify_id in seen:
                    continue
                seen.add(n.notify_id)
                typer.echo(
                    f"[{time.strftime('%H:%M:%S')}] {n.kind} from {n.source_did[:24]}: "
                    f"{json.dumps(n.payload, ensure_ascii=False)[:100]}"
                )
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("\nstopped.")


__all__ = ["notify_app"]
