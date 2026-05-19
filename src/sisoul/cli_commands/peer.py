"""sisoul peer 命令组 (Wave A #16 · §F.4 · 朋友 daemon TURN relay 退化).

§32 §F.4 #16 设计: 默认砍 Twilio NTS, 5% 双 NAT 失败的用户可让朋友的 daemon 充当
TURN relay (流量 libsodium box 加密, 朋友看不到内容).

3 子命令:
- ``relay-mode on|off``     切换本 daemon 是否充当 TURN/STUN relay 给朋友
- ``relay-mode status``     查当前 relay 状态 (on/off + 过去 24h relay 字节数)
- ``probe-stun [--json]``   探活 5 STUN 池, 报每个延迟 + reflexive IP (§F.4.2 CLI ``net probe``)

存储:
- ``<vault>/p2p/relay_state.json``: ``{"enabled": bool, "since_ts": float, "stats": {...}}``
- 真 relay 转发逻辑由 daemon p2p_transport 在 enabled=True 时挂 forward hook (Wave B 接入)

本命令仅做 toggle + status, 真 relay forward 在 transport / daemon 层 Wave B 接入
(本 Wave A 范围: STUN 池 + 砍 TURN + relay-mode toggle CLI).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

import typer

from sisoul.vault import DEFAULT_VAULT_DIR

peer_app = typer.Typer(
    name="peer",
    help=(
        "朋友 daemon 充当 STUN/TURN relay (Wave A #16 · §F.4). "
        "5% 双 NAT 失败兜底, 砍 Twilio NTS 中心化默认依赖."
    ),
    no_args_is_help=True,
)


def _relay_state_path(vault_dir: Optional[Path] = None) -> Path:
    v = vault_dir if vault_dir is not None else DEFAULT_VAULT_DIR
    return v / "p2p" / "relay_state.json"


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {
            "enabled": False,
            "since_ts": 0.0,
            "stats": {"bytes_relayed_24h": 0, "peers_served_24h": 0},
        }
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"enabled": False, "since_ts": 0.0, "stats": {}}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False))


# ── relay-mode ───────────────────────────────────────────────────────────────


@peer_app.command("relay-mode")
def cmd_relay_mode(
    action: str = typer.Argument(
        ..., help="on / off / status — 切换或查询本 daemon TURN relay 模式"
    ),
    vault_dir: Optional[Path] = typer.Option(
        None, "--vault-dir", help="vault 目录 (默认 ~/.sisoul/)"
    ),
    output_json: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """切换本 daemon 充当朋友 TURN relay (§F.4 5% NAT 失败兜底).

    on  → 写 relay_state.json enabled=True. 后续 daemon 启动 transport 时
          (Wave B 接入) 挂 forward hook, 看到 ``relay-to=<peer_did>`` 标记的
          ciphertext 转发给目标 peer. 朋友看不到内容 (libsodium box).

    off → 关 relay. 默认状态.

    status → 报当前 enabled + 持续多久 + 24h relay 字节数 (Wave B daemon 写).
    """
    path = _relay_state_path(vault_dir)
    state = _load_state(path)
    action_l = action.lower()

    if action_l == "on":
        if not state.get("enabled"):
            state["enabled"] = True
            state["since_ts"] = time.time()
            _save_state(path, state)
        msg = {"action": "on", "enabled": True, "since_ts": state["since_ts"]}
    elif action_l == "off":
        if state.get("enabled"):
            state["enabled"] = False
            state["since_ts"] = 0.0
            _save_state(path, state)
        msg = {"action": "off", "enabled": False}
    elif action_l == "status":
        msg = {
            "action": "status",
            "enabled": bool(state.get("enabled")),
            "since_ts": state.get("since_ts", 0.0),
            "stats": state.get("stats", {}),
            "state_path": str(path),
        }
    else:
        typer.echo(f"未知 action: {action} (用 on/off/status)", err=True)
        raise typer.Exit(code=1)

    if output_json:
        typer.echo(json.dumps(msg, ensure_ascii=False))
        return

    if action_l == "on":
        typer.echo(f"✅ relay-mode ON (since {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state['since_ts']))})")
        typer.echo("   本 daemon 现可为朋友的 sisoul 充当 TURN relay")
        typer.echo("   流量 libsodium box 加密, 你看不到内容 (§F.4.4)")
    elif action_l == "off":
        typer.echo("✅ relay-mode OFF")
    else:
        en = "✅ ON" if msg["enabled"] else "⛔ OFF"
        typer.echo(f"relay-mode: {en}")
        if msg["enabled"] and msg["since_ts"]:
            dur = time.time() - msg["since_ts"]
            typer.echo(f"  since: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(msg['since_ts']))} ({dur/3600:.1f}h ago)")
        stats = msg.get("stats", {})
        if stats:
            typer.echo(f"  24h bytes:  {stats.get('bytes_relayed_24h', 0)}")
            typer.echo(f"  24h peers:  {stats.get('peers_served_24h', 0)}")
        typer.echo(f"  state file: {msg['state_path']}")


# ── probe-stun ───────────────────────────────────────────────────────────────


@peer_app.command("probe-stun")
def cmd_probe_stun(
    timeout: float = typer.Option(5.0, "--timeout", help="每个 STUN 探活超时 (秒)"),
    output_json: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """探活 5 STUN 公共池, 报每个 reflexive IP + 延迟 (§F.4.2 ``net probe``).

    用 ``SISOUL_STUN_URLS`` env 覆盖默认 5 STUN 池.
    """
    from sisoul.p2p.stun_pool import load_stun_pool_from_env, probe_stun_pool

    urls = load_stun_pool_from_env()
    results = asyncio.run(probe_stun_pool(urls, timeout_sec=timeout))

    if output_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "url": r.url,
                        "alive": r.alive,
                        "latency_ms": r.latency_ms,
                        "reflexive_ip": r.reflexive_ip,
                        "reflexive_port": r.reflexive_port,
                        "error": r.error,
                    }
                    for r in results
                ],
                ensure_ascii=False,
            )
        )
        return

    alive_count = sum(1 for r in results if r.alive)
    typer.echo(f"STUN pool: {alive_count}/{len(results)} alive")
    for r in results:
        if r.alive:
            typer.echo(
                f"  ✅ {r.url}  {r.latency_ms:.0f}ms  reflexive={r.reflexive_ip}:{r.reflexive_port}"
            )
        else:
            typer.echo(f"  ❌ {r.url}  ({r.error})")


__all__ = [
    "cmd_probe_stun",
    "cmd_relay_mode",
    "peer_app",
]
