"""sisoul p2p 命令组 (Phase 3 W31-W36 · 波 4 dev-A).

§28 §1.1 模块 9 / §28 §2.1 P2P endpoints.

6 子命令:
- start [--port N] [--vault-dir]   启动本地 P2P node
- stop                              停 P2P node
- status                            列当前 transport / peer_id / multiaddr / 同步统计
- sync-now [--peer PEER_ID]        强制 sync vault 给某 peer (默认所有 peer)
- list-peers                        已知 peer 列表
- add-peer <multiaddr>              手动加 peer (mDNS/DHT 失败时用)

由 ``cli.py`` 主入口通过 ``app.add_typer(p2p_app, name='p2p')`` 整合 (主集成做).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer

from sisoul.p2p import (
    AIORTC_AVAILABLE,
    LIBP2P_AVAILABLE,
    get_node,
    list_peers as _list_peers_api,
    set_node,
    start_node,
    stop_node,
    sync_with_peer,
)
from sisoul.p2p.node import SisoulP2PNode
from sisoul.vault import DEFAULT_VAULT_DIR

p2p_app = typer.Typer(
    name="p2p",
    help="跨设备 P2P sync (Phase 3 W31-W36, libp2p / WebRTC fallback / 加密).",
    no_args_is_help=True,
)


def _run(coro):
    """跑 coroutine (CLI 同步入口适配)."""
    return asyncio.run(coro)


# ── start ────────────────────────────────────────────────────────────────────


@p2p_app.command("start")
def cli_p2p_start(
    port: int = typer.Option(0, "--port", "-p", help="bind port (0 = OS 分配; inmem/webrtc fallback 忽略)"),
    vault_dir: Optional[str] = typer.Option(None, "--vault-dir", help="vault 路径 (默认 ~/.sisoul/)"),
    transport: Optional[str] = typer.Option(
        None,
        "--transport",
        help="强制 transport: libp2p / webrtc / inmem (默认自动选)",
    ),
) -> None:
    """启动 P2P node (后台 in-process; CLI 退出即停).

    生产: 应被 daemon 内部启动 (daemon 进程长期持有). 本 CLI 命令主要用于一次性 sync /
    测试 / 与 daemon route ``POST /sisoul/p2p/start`` 等价.
    """
    vault_path = Path(vault_dir).expanduser() if vault_dir else DEFAULT_VAULT_DIR
    if not vault_path.exists():
        typer.echo(f"❌ vault 不存在: {vault_path}. 先 ``sisoul init``.", err=True)
        raise typer.Exit(code=1)

    async def _do():
        node = await start_node(vault_dir=vault_path, port=port, transport_prefer=transport)
        st = node.status()
        typer.echo(f"✅ P2P node started")
        typer.echo(f"  transport:   {st.transport}")
        typer.echo(f"  peer_id:     {st.peer_id}")
        typer.echo(f"  multiaddr:   {st.multiaddr}")
        typer.echo(f"  libp2p_avail:{st.libp2p_available}  aiortc_avail:{st.aiortc_available}")
        typer.echo(f"  peers known: {len(st.peers)}")

    _run(_do())


# ── stop ─────────────────────────────────────────────────────────────────────


@p2p_app.command("stop")
def cli_p2p_stop() -> None:
    """停 P2P node."""

    async def _do():
        node = get_node()
        if node is None:
            typer.echo("ℹ️ P2P node 未 running")
            return
        await stop_node()
        typer.echo("✅ P2P node stopped")

    _run(_do())


# ── status ───────────────────────────────────────────────────────────────────


@p2p_app.command("status")
def cli_p2p_status(
    output_json: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """列当前 P2P node 状态 + peer 列表 + sync stats."""
    node = get_node()
    if node is None:
        if output_json:
            typer.echo(json.dumps({"running": False, "libp2p_available": LIBP2P_AVAILABLE,
                                   "aiortc_available": AIORTC_AVAILABLE}))
        else:
            typer.echo("P2P node: not running")
            typer.echo(f"  libp2p_avail: {LIBP2P_AVAILABLE}")
            typer.echo(f"  aiortc_avail: {AIORTC_AVAILABLE}")
        return
    st = node.status()
    if output_json:
        typer.echo(json.dumps({
            "running": st.running,
            "transport": st.transport,
            "peer_id": st.peer_id,
            "multiaddr": st.multiaddr,
            "port": st.port,
            "libp2p_available": st.libp2p_available,
            "aiortc_available": st.aiortc_available,
            "peers": [{"peer_id": p.peer_id, "multiaddr": p.multiaddr, "transport": p.transport}
                      for p in st.peers],
            "stats": {
                "syncs_total": st.stats.syncs_total,
                "syncs_ok": st.stats.syncs_ok,
                "syncs_failed": st.stats.syncs_failed,
                "last_sync_ts": st.stats.last_sync_ts,
                "last_sync_peer": st.stats.last_sync_peer,
                "last_sync_pulled": st.stats.last_sync_pulled,
                "last_sync_pushed": st.stats.last_sync_pushed,
                "last_sync_conflicts": st.stats.last_sync_conflicts,
            },
        }))
    else:
        typer.echo(f"P2P node: running")
        typer.echo(f"  transport:    {st.transport}")
        typer.echo(f"  peer_id:      {st.peer_id}")
        typer.echo(f"  multiaddr:    {st.multiaddr}")
        typer.echo(f"  syncs:        {st.stats.syncs_ok}/{st.stats.syncs_total} ok, {st.stats.syncs_failed} fail")
        typer.echo(f"  peers known:  {len(st.peers)}")
        for p in st.peers:
            typer.echo(f"    - {p.peer_id}  ({p.transport})  {p.multiaddr}")


# ── sync-now ─────────────────────────────────────────────────────────────────


@p2p_app.command("sync-now")
def cli_p2p_sync_now(
    peer: Optional[str] = typer.Option(None, "--peer", help="指定 peer_id (默认所有已知 peer)"),
    timeout: float = typer.Option(5.0, "--timeout", help="单 peer sync 超时 (秒)"),
) -> None:
    """强制 sync vault 给某 peer (或所有 peer)."""
    node = get_node()
    if node is None:
        typer.echo("❌ P2P node 未 running, 先 ``sisoul p2p start``", err=True)
        raise typer.Exit(code=1)

    async def _do():
        if peer:
            targets = [peer]
        else:
            targets = [p.peer_id for p in node.list_peers()]
        if not targets:
            typer.echo("⚠️ 无 known peer; 用 ``sisoul p2p add-peer <multiaddr>`` 先加")
            return
        for pid in targets:
            res = await sync_with_peer(pid, timeout=timeout)
            ok = "✅" if res["ok"] else "❌"
            typer.echo(
                f"{ok} {pid[:16]}...: pulled={res['pulled']} pushed={res['pushed']} "
                f"conflicts={res['conflicts']} err={res.get('error')}"
            )

    _run(_do())


# ── list-peers ───────────────────────────────────────────────────────────────


@p2p_app.command("list-peers")
def cli_p2p_list_peers(
    output_json: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """列已知 peer."""
    peers = _list_peers_api()
    if output_json:
        typer.echo(json.dumps([
            {"peer_id": p.peer_id, "multiaddr": p.multiaddr, "transport": p.transport,
             "last_seen_ts": p.last_seen_ts}
            for p in peers
        ]))
        return
    if not peers:
        typer.echo("(no peers known)")
        return
    for p in peers:
        typer.echo(f"- {p.peer_id}  [{p.transport}]  {p.multiaddr}")


# ── add-peer ─────────────────────────────────────────────────────────────────


@p2p_app.command("add-peer")
def cli_p2p_add_peer(
    multiaddr: str = typer.Argument(..., help="peer multiaddr, 例 inmem://abc123 / webrtc://xxx:9876"),
    peer_id: Optional[str] = typer.Option(None, "--peer-id", help="显式 peer_id (默认从 multiaddr 解)"),
    transport: str = typer.Option("manual", "--transport", help="标记 transport 类型"),
) -> None:
    """手动加 peer (mDNS/DHT 失败时用)."""
    node = get_node()
    if node is None:
        typer.echo("❌ P2P node 未 running, 先 ``sisoul p2p start``", err=True)
        raise typer.Exit(code=1)
    try:
        peer = node.add_peer(multiaddr, peer_id=peer_id, transport=transport)
    except ValueError as e:
        typer.echo(f"❌ add-peer 失败: {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"✅ peer added: {peer.peer_id} {peer.multiaddr}")


__all__ = [
    "cli_p2p_add_peer",
    "cli_p2p_list_peers",
    "cli_p2p_start",
    "cli_p2p_status",
    "cli_p2p_stop",
    "cli_p2p_sync_now",
    "p2p_app",
]
