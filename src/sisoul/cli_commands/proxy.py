"""sisoul proxy 命令组 (Phase 4 W54-W58 · 波 5 dev-B).

§28 §3.2 加密 proxy CLI 入口. 3 子命令:

- ``sisoul proxy start [--listen-port N] [--bind-host H]`` — 起 Bob 端 proxy listener
  (本地 daemon 内已 include_router, 此命令仅辅助 systemd / launchd 单独跑 proxy daemon).
- ``sisoul proxy stop`` — 停 listener (本机已跑实例).
- ``sisoul proxy status`` — 列活动 forward session metadata (绝不含 prompt 内容).

⚠️ 安全约束:
- 默认 bind 127.0.0.1 (loopback). 公网暴露需 Tailscale + ACL 显式授权 + WireGuard 二层.
- listen-port 默认 9877 (跟 daemon 主 9876 错开避免冲突).
"""

from __future__ import annotations

import json
from typing import Optional

import typer

proxy_app = typer.Typer(
    name="proxy",
    help="加密 proxy daemon (Phase 4 W54-W58, libsodium box E2E, prompt 仅 Bob 内存可见).",
    no_args_is_help=True,
)


_DEFAULT_LISTEN_PORT = 9877
_DEFAULT_BIND_HOST = "127.0.0.1"


@proxy_app.command("start")
def cli_proxy_start(
    listen_port: int = typer.Option(
        _DEFAULT_LISTEN_PORT, "--listen-port", "-p",
        help="proxy listener bind port (默认 9877, 跟主 daemon 9876 错开).",
    ),
    bind_host: str = typer.Option(
        _DEFAULT_BIND_HOST, "--bind-host",
        help="bind 地址. 默认 loopback. 公网需 Tailscale ACL 显式授权.",
    ),
    friend_index: int = typer.Option(
        0, "--friend-index",
        help="本端 friend session keypair 派生 index (跟 friend DB 编号一致).",
    ),
) -> None:
    """启动 Bob 端 proxy listener (内部用 set_global_proxy 注册全局).

    注: 主 daemon (sisoul daemon) 已 include proxy_router, 通常无需单独跑.
    本命令保留给 systemd / launchd 独立进程隔离场景.
    """
    from pathlib import Path

    from sisoul.friend.encrypted_proxy import (
        EncryptedProxy,
        derive_friend_session_keypair,
        get_global_proxy,
        set_global_proxy,
    )

    if get_global_proxy() is not None:
        typer.echo("ℹ️ 已有全局 proxy 注册 (在 daemon 进程内). 先 sisoul proxy stop 再启.")
        raise typer.Exit(code=1)

    # 读 seed (Phase 4 复用 identity.seed.load_mnemonic_from_file)
    try:
        from sisoul.identity.seed import (
            DEFAULT_SEED_FILE,
            load_mnemonic_from_file,
            mnemonic_to_master_key,
        )
    except ImportError as e:
        typer.echo(f"❌ identity 模块缺失: {e}", err=True)
        raise typer.Exit(code=2)

    if not DEFAULT_SEED_FILE.exists():
        typer.echo(
            f"❌ 未找到 BIP-39 seed ({DEFAULT_SEED_FILE}). "
            "先 sisoul init 或 sisoul restore <seed>.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        mnemonic = load_mnemonic_from_file()
        master = mnemonic_to_master_key(mnemonic)
    except Exception as e:  # noqa: BLE001
        typer.echo(f"❌ 读 seed 失败: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(code=1)

    priv, pub = derive_friend_session_keypair(master, friend_index=friend_index)

    # 读自己 DID (波 3 dev-B did 模块)
    self_did = "unknown.sisoul.eth"
    try:
        from sisoul.identity.did import (  # type: ignore[import-not-found]
            load_did_state,
        )
        st = load_did_state()
        self_did = getattr(st, "did_string", None) or getattr(st, "handle", self_did)
    except Exception:  # noqa: BLE001
        pass

    proxy = EncryptedProxy(
        self_priv=priv,
        self_pub=pub,
        self_did=self_did,
    )
    set_global_proxy(proxy)

    typer.echo("✅ proxy 注册完成")
    typer.echo(f"  bind:        http://{bind_host}:{listen_port}/sisoul/proxy/*")
    typer.echo(f"  self_did:    {self_did}")
    typer.echo(f"  pubkey(hex): {pub.encode().hex()}")
    typer.echo(f"  注: 真 HTTP 监听由 daemon 进程持有; 此命令仅注册 proxy 实例.")
    typer.echo(f"      公网暴露请走 Tailscale + ACL, 严禁裸 0.0.0.0.")


@proxy_app.command("stop")
def cli_proxy_stop() -> None:
    """停 proxy listener (清全局 proxy 实例)."""
    from sisoul.friend.encrypted_proxy import get_global_proxy, set_global_proxy

    if get_global_proxy() is None:
        typer.echo("ℹ️ proxy 未启动, no-op")
        return
    set_global_proxy(None)
    typer.echo("✅ proxy stopped (全局实例已清, daemon 进程不受影响)")


@proxy_app.command("status")
def cli_proxy_status(
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
) -> None:
    """列活动 forward session metadata (绝不含 prompt 内容)."""
    from sisoul.friend.encrypted_proxy import get_global_proxy

    proxy = get_global_proxy()
    if proxy is None:
        if json_output:
            typer.echo(json.dumps({"running": False, "sessions": []}))
        else:
            typer.echo("ℹ️ proxy 未启动 (sisoul proxy start 启动)")
        return

    sessions = [s.to_safe_dict() for s in proxy.list_sessions()]

    if json_output:
        typer.echo(json.dumps({
            "running": True,
            "self_did": proxy.self_did,
            "pubkey_hex": proxy.self_pub.encode().hex(),
            "session_count": len(sessions),
            "sessions": sessions,
        }, indent=2))
        return

    typer.echo(f"✅ proxy running (self_did={proxy.self_did})")
    typer.echo(f"  pubkey(hex): {proxy.self_pub.encode().hex()}")
    typer.echo(f"  active sessions: {len(sessions)}")
    for s in sessions:
        typer.echo(
            f"  - {s['session_id']}: borrower={s['borrower_did']} "
            f"model={s['target_model']} status={s['status']} "
            f"tokens(p/r)={s['prompt_token_count']}/{s['response_token_count']}"
        )


__all__ = ["proxy_app"]
