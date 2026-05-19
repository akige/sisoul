"""sisoul attest 命令组 (Phase 3 W37-W40, 波 4 dev-B).

5 子命令 (Typer subapp `attest_app`):
- queue        列当前 pending queue
- flush        强制 batch 上链 (跳过 10 条阈值)
- history      链上 attestation 历史 (EAS GraphQL)
- verify <uid> 验证某 attestation 是否真在链上 (本地 + 链上)
- config       查/改 batch_size / network / RPC URL

由 cli.py 主入口通过 ``app.add_typer(attest_app, name='attest')`` 整合.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from sisoul.onchain.eas import (
    AttestConfig,
    AttestQueue,
    EASError,
    NetworkNotSupportedError,
    QueueEmptyError,
    AttestationNotFoundError,
    SHORT_TO_NETWORK,
    list_history_local,
    list_history_onchain,
    load_config,
    resolve_chain,
    save_config,
    upload_batch,
    verify_attestation_local,
    verify_attestation_onchain,
)


def _apply_chain_override(cfg: AttestConfig, chain: Optional[str]) -> AttestConfig:
    """P3-5: --chain optimism|arbitrum|base|zksync 覆盖 config.network + rpc_url + schema_uid 留默认."""
    if not chain:
        return cfg
    cc = resolve_chain(chain)
    cfg.network = cc.name  # type: ignore[assignment]
    cfg.rpc_url = cc.rpc_url
    return cfg

attest_app = typer.Typer(
    name="attest",
    help="EAS attestation queue (Optimism Sepolia testnet, batched). Phase 3 W37-W40.",
    no_args_is_help=True,
)


def _resolve_db_path(queue_db: Optional[Path]) -> Optional[Path]:
    return queue_db if queue_db else None


# ── attest queue ─────────────────────────────────────────────────────────────


@attest_app.command("queue")
def cmd_queue(
    queue_db: Path = typer.Option(
        None, "--queue-db", help="SQLite queue 路径 (默认 ~/.sisoul/attest_queue.db)"
    ),
    status: str = typer.Option(
        "pending", "--status", "-s",
        help="过滤状态: pending / batched / confirmed / failed / all",
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="最多列 N 条"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """列当前 attestation queue (pending 默认)."""
    db = _resolve_db_path(queue_db)
    with AttestQueue(db_path=db) as q:
        if status == "all":
            items = q.all_items(status=None, limit=limit)
        else:
            items = q.all_items(status=status, limit=limit)
        stats = q.stats()

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "stats": stats,
                    "items": [it.to_dict() for it in items],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    typer.echo(
        f"queue stats: pending={stats['pending']} batched={stats['batched']} "
        f"confirmed={stats['confirmed']} failed={stats['failed']} batches={stats['batches']}"
    )
    if not items:
        typer.echo(f"(无 {status} 项)")
        return
    typer.echo(f"| queue_id | action | target | tool | queued_at | status |")
    typer.echo("|---|---|---|---|---|---|")
    for it in items:
        tgt = it.target if len(it.target) <= 40 else it.target[:37] + "..."
        typer.echo(
            f"| {it.queue_id[:8]} | {it.action_type} | {tgt} | "
            f"{it.tool_name} | {it.queued_at} | {it.status} |"
        )


# ── attest flush ─────────────────────────────────────────────────────────────


@attest_app.command("flush")
def cmd_flush(
    queue_db: Path = typer.Option(
        None, "--queue-db", help="SQLite queue 路径 (默认 ~/.sisoul/attest_queue.db)"
    ),
    config_path: Path = typer.Option(
        None, "--config", "-c", help="config 路径 (默认 ~/.sisoul/attest_config.json)"
    ),
    force: bool = typer.Option(
        False, "--force", help="不限 batch_size, 把所有 pending 全打一笔 (注意 gas 估算)"
    ),
    max_items: int = typer.Option(
        None, "--max-items", help="本次最多 batch N 条 (覆盖 config.batch_size)"
    ),
    chain: Optional[str] = typer.Option(
        None, "--chain",
        help="P3-5 跨链: optimism / arbitrum / base / zksync (覆盖 config.network)"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """强制 batch 上链 (跳过 10 条阈值)."""
    try:
        cfg = load_config(config_path)
    except EASError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=2)

    try:
        cfg = _apply_chain_override(cfg, chain)
    except NetworkNotSupportedError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=3)

    db = _resolve_db_path(queue_db)
    try:
        with AttestQueue(db_path=db) as q:
            result = upload_batch(q, cfg, force=force, max_items=max_items)
    except QueueEmptyError as e:
        typer.echo(f"⚠️  {e}", err=True)
        raise typer.Exit(code=1)
    except NetworkNotSupportedError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=3)
    except EASError as e:
        typer.echo(f"❌ batch 失败: {e}", err=True)
        raise typer.Exit(code=2)

    if json_output:
        typer.echo(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return

    typer.echo(f"✅ batch 已上链 ({result.method}):")
    typer.echo(f"   batch_uid: {result.batch_uid}")
    typer.echo(f"   tx_hash:   {result.tx_hash}")
    typer.echo(f"   network:   {result.network}")
    typer.echo(f"   schema:    {result.schema_uid[:20]}...")
    typer.echo(f"   count:     {result.count} attestations")
    typer.echo(
        f"   gas est:   {result.gas_used_estimate} units, "
        f"{result.gas_cost_wei_estimate} wei"
    )
    typer.echo(f"   confirmed: {result.confirmed_at}")
    if result.method == "mock":
        typer.echo("   ⚠️  mock 模式: 未真上链 (config.network=mock 或 RPC 不通)")
    elif result.method == "live-readonly":
        typer.echo("   ⚠️  live-readonly: RPC 已校验, 未发 tx (无 private_key_path)")


# ── attest history ───────────────────────────────────────────────────────────


@attest_app.command("history")
def cmd_history(
    queue_db: Path = typer.Option(None, "--queue-db", help="SQLite queue 路径"),
    config_path: Path = typer.Option(None, "--config", "-c", help="config 路径"),
    source: str = typer.Option(
        "local", "--source", "-s", help="local / onchain (查 EAS GraphQL)"
    ),
    attester: str = typer.Option(
        None, "--attester", help="onchain 过滤 attester 地址 (0x...)"
    ),
    chain: Optional[str] = typer.Option(
        None, "--chain",
        help="P3-5 跨链: optimism / arbitrum / base / zksync (覆盖 config.network)"
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="最多列 N 条"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """链上 / 本地 attestation 历史."""
    try:
        cfg = load_config(config_path)
    except EASError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=2)

    try:
        cfg = _apply_chain_override(cfg, chain)
    except NetworkNotSupportedError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=3)

    if source == "local":
        db = _resolve_db_path(queue_db)
        with AttestQueue(db_path=db) as q:
            batches = list_history_local(q, limit=limit)
        if json_output:
            typer.echo(
                json.dumps([b.to_dict() for b in batches], ensure_ascii=False, indent=2)
            )
            return
        if not batches:
            typer.echo("(本地无 batch 历史)")
            return
        typer.echo(f"| batch_uid | tx_hash | network | count | method | confirmed |")
        typer.echo("|---|---|---|---|---|---|")
        for b in batches:
            typer.echo(
                f"| {b.batch_uid[:8]} | {b.tx_hash[:14]}... | {b.network} | "
                f"{b.count} | {b.method} | {b.confirmed_at} |"
            )
        return

    if source == "onchain":
        try:
            atts = list_history_onchain(
                attester=attester, network=cfg.network, limit=limit
            )
        except NetworkNotSupportedError as e:
            typer.echo(f"❌ {e}", err=True)
            raise typer.Exit(code=3)
        except EASError as e:
            typer.echo(f"❌ onchain 历史失败: {e}", err=True)
            raise typer.Exit(code=2)

        if json_output:
            typer.echo(json.dumps(atts, ensure_ascii=False, indent=2))
            return
        if not atts:
            typer.echo("(链上无 attestation, 或 network=mock)")
            return
        typer.echo(f"| id | attester | recipient | schemaId | time |")
        typer.echo("|---|---|---|---|---|")
        for a in atts:
            typer.echo(
                f"| {a.get('id','')[:14]}... | {a.get('attester','')[:14]}... | "
                f"{a.get('recipient','')[:14]}... | "
                f"{(a.get('schemaId') or '')[:14]}... | {a.get('time','')} |"
            )
        return

    typer.echo(f"❌ source 必须是 local / onchain (拿到 '{source}')", err=True)
    raise typer.Exit(code=1)


# ── attest verify ────────────────────────────────────────────────────────────


@attest_app.command("verify")
def cmd_verify(
    uid: str = typer.Argument(..., help="attestation UID (0x...)"),
    queue_db: Path = typer.Option(None, "--queue-db", help="SQLite queue 路径"),
    config_path: Path = typer.Option(None, "--config", "-c", help="config 路径"),
    onchain: bool = typer.Option(
        False, "--onchain", help="同时查链上 EAS GraphQL (默认只本地校验)"
    ),
    chain: Optional[str] = typer.Option(
        None, "--chain",
        help="P3-5 跨链: optimism / arbitrum / base / zksync (覆盖 config.network)"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """验证某 attestation 是否真在链上 (本地 recompute + 可选 onchain)."""
    try:
        cfg = load_config(config_path)
    except EASError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=2)

    try:
        cfg = _apply_chain_override(cfg, chain)
    except NetworkNotSupportedError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=3)

    db = _resolve_db_path(queue_db)
    with AttestQueue(db_path=db) as q:
        try:
            local = verify_attestation_local(q, uid)
        except AttestationNotFoundError as e:
            local = {"valid": False, "method": "local-recompute", "reason": str(e)}

    onchain_result = None
    if onchain:
        try:
            onchain_result = verify_attestation_onchain(
                uid, network=cfg.network, rpc_url=cfg.rpc_url
            )
        except NetworkNotSupportedError as e:
            typer.echo(f"❌ {e}", err=True)
            raise typer.Exit(code=3)
        except EASError as e:
            onchain_result = {"valid": False, "method": "onchain-graphql", "reason": str(e)}

    out = {"uid": uid, "local": local, "onchain": onchain_result}
    if json_output:
        typer.echo(json.dumps(out, ensure_ascii=False, indent=2))
        return

    # 友好输出
    typer.echo(f"attestation uid: {uid}")
    typer.echo(f"--- local ---")
    typer.echo(f"  valid:  {local.get('valid')}")
    typer.echo(f"  method: {local.get('method')}")
    if local.get("reason"):
        typer.echo(f"  reason: {local.get('reason')}")
    if local.get("attestation"):
        att = local["attestation"]
        typer.echo(f"  attestation: actor={att.get('actor_did')} action={att.get('action_type')} "
                   f"target={att.get('target')}")
    if onchain_result:
        typer.echo(f"--- onchain ---")
        typer.echo(f"  valid:  {onchain_result.get('valid')}")
        typer.echo(f"  method: {onchain_result.get('method')}")
        if onchain_result.get("reason"):
            typer.echo(f"  reason: {onchain_result.get('reason')}")

    if not local.get("valid") and not (onchain_result and onchain_result.get("valid")):
        raise typer.Exit(code=1)


# ── attest config ────────────────────────────────────────────────────────────


@attest_app.command("config")
def cmd_config(
    config_path: Path = typer.Option(
        None, "--config", "-c", help="config 路径 (默认 ~/.sisoul/attest_config.json)"
    ),
    show: bool = typer.Option(False, "--show", help="显示当前 config"),
    set_network: str = typer.Option(
        None, "--set-network", help="optimism-sepolia / optimism-mainnet (禁) / mock"
    ),
    set_rpc: str = typer.Option(None, "--set-rpc", help="自定义 RPC URL"),
    set_batch_size: int = typer.Option(None, "--set-batch-size", help="batch 阈值 (默认 10)"),
    set_batch_timeout: int = typer.Option(
        None, "--set-batch-timeout", help="batch 超时秒 (默认 3600)"
    ),
    set_schema_uid: str = typer.Option(None, "--set-schema-uid", help="EAS schema UID"),
    set_attester_did: str = typer.Option(
        None, "--set-attester-did", help="attester DID (默认本地 registry 第一条)"
    ),
    set_private_key_path: str = typer.Option(
        None, "--set-private-key-path",
        help="hex private key 文件路径 (启用 live-tx, 本 wave 不实测)"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """查/改 attest config."""
    try:
        cfg = load_config(config_path)
    except EASError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=2)

    changed = False
    if set_network is not None:
        if set_network not in ("optimism-sepolia", "optimism-mainnet", "mock"):
            typer.echo(
                f"❌ network 必须是 optimism-sepolia / optimism-mainnet / mock", err=True
            )
            raise typer.Exit(code=1)
        cfg.network = set_network  # type: ignore[assignment]
        changed = True
    if set_rpc is not None:
        cfg.rpc_url = set_rpc
        changed = True
    if set_batch_size is not None:
        if set_batch_size <= 0:
            typer.echo("❌ batch_size 必须 > 0", err=True)
            raise typer.Exit(code=1)
        cfg.batch_size = set_batch_size
        changed = True
    if set_batch_timeout is not None:
        if set_batch_timeout < 0:
            typer.echo("❌ batch_timeout_sec 必须 >= 0", err=True)
            raise typer.Exit(code=1)
        cfg.batch_timeout_sec = set_batch_timeout
        changed = True
    if set_schema_uid is not None:
        cfg.schema_uid = set_schema_uid
        changed = True
    if set_attester_did is not None:
        cfg.attester_did = set_attester_did
        changed = True
    if set_private_key_path is not None:
        cfg.private_key_path = set_private_key_path
        changed = True

    if changed:
        saved = save_config(cfg, config_path)
        typer.echo(f"✅ config 已保存: {saved}")

    if show or not changed:
        if json_output:
            typer.echo(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2))
        else:
            typer.echo("当前 attest config:")
            for k, v in cfg.to_dict().items():
                # 不打印 private_key 本体, 只显示路径
                typer.echo(f"  {k}: {v}")


__all__ = ["attest_app"]
