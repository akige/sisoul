"""sisoul net — 内嵌 kubo / GossipSub P2P 状态 (去中心化传输层).

``sisoul net status`` 是 Gate-2 验收命令:
显示本机是否跑了内嵌 kubo 节点 + swarm peer 数。

- 在 mac/wsl/win 上: 探测本机 kubo API (127.0.0.1:5001), 显示 peer_id + swarm peer 数。
- 在 aws-*/cloud 主机上: 报 "P2P disabled on this host" (用户红线 §10.3), 不算错误。

与 ``sisoul p2p status`` 区分: ``p2p`` 是 vault-sync 的 SisoulP2PNode;
``net`` 是 chat/borrow GossipSub 底座的 kubo IPFSKuboNode。
"""

from __future__ import annotations

import asyncio
import json
import os

import typer

from sisoul.p2p.host_policy import ALLOW_CLOUD_P2P_ENV, cloud_refusal_reason

net_app = typer.Typer(
    name="net",
    help="内嵌 kubo / GossipSub P2P 状态 (去中心化传输层; Gate-2 验收).",
    no_args_is_help=True,
)

DEFAULT_KUBO_API = "http://127.0.0.1:5001"


@net_app.command("status")
def cli_net_status(
    output_json: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """显示内嵌 kubo / GossipSub 传输状态 + swarm peer 数."""
    reason = cloud_refusal_reason()
    if reason is not None:
        payload = {
            "p2p_allowed": False,
            "reason": reason,
            "running": False,
            "peers": 0,
        }
        if output_json:
            typer.echo(json.dumps(payload))
            return
        typer.echo("P2P (kubo/GossipSub): DISABLED on this host")
        typer.echo(f"  reason:   {reason}")
        typer.echo("  policy:   只允许在 mac/wsl/win 跑内嵌 kubo (用户红线 §10.3)")
        typer.echo(f"  override: export {ALLOW_CLOUD_P2P_ENV}=1 (不建议)")
        return

    api = os.environ.get("SISOUL_IPFS_API_URL", DEFAULT_KUBO_API)
    from sisoul.p2p.ipfs_kubo import IPFSKuboNode, IPFSNotStarted

    async def _probe():
        # external-daemon 模式: 跨进程探测本机 kubo API (daemon 起的或用户手动起的都行).
        node = IPFSKuboNode(mode="external-daemon", external_daemon_url=api)
        try:
            await node.start()  # 校验端口在 listen + 拿 peer_id
        except IPFSNotStarted:
            return None
        return await node.status()

    st = asyncio.run(_probe())

    if st is None or not st.running:
        payload = {"p2p_allowed": True, "running": False, "peers": 0, "api_url": api}
        if output_json:
            typer.echo(json.dumps(payload))
            return
        typer.echo("P2P (kubo/GossipSub): not running")
        typer.echo(f"  api probed: {api} (无响应)")
        typer.echo(
            "  启动: 本机跑 `sisoul daemon` (自动起内嵌 kubo); "
            "未装 kubo 先 brew install ipfs / apt install kubo"
        )
        return

    payload = {
        "p2p_allowed": True,
        "running": True,
        "peer_id": st.peer_id,
        "peers": st.peers,
        "agent_version": st.agent_version,
        "api_url": api,
    }
    if output_json:
        typer.echo(json.dumps(payload))
        return
    typer.echo("P2P (kubo/GossipSub): running")
    typer.echo(f"  peer_id:     {st.peer_id}")
    typer.echo(f"  swarm peers: {st.peers}")
    typer.echo(f"  agent:       {st.agent_version}")
    typer.echo(f"  api:         {api}")


__all__ = ["net_app", "cli_net_status"]
