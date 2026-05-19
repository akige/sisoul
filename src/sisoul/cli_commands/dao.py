"""sisoul dao 命令组 (Phase 3 P3-4 DAO governance).

子命令:
- propose <PIP-id> [--next-status review|finalcall|final|withdrawn]
- vote <proposal-id> <for|against|abstain>
- status <proposal-id>
- config  (查 / 改 RPC / contract 地址 / mode)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import typer

from sisoul.dao.governance import (
    DAOConfig,
    DAOError,
    GovernorClient,
    PROPOSAL_STATE_NAMES,
    ProposalNotFoundError,
    Web3NotInstalledError,
    load_dao_config,
    propose_pip_promotion,
    save_dao_config,
)

dao_app = typer.Typer(
    name="dao",
    help="sisoul DAO governance (SisoulGov + PIPRegistry on-chain). Phase 3 P3-4.",
    no_args_is_help=True,
)


_PIP_ID_RE = re.compile(r"^(?:PIP-)?(\d+)$", re.IGNORECASE)


def _parse_pip_id(raw: str) -> int:
    m = _PIP_ID_RE.match(raw.strip())
    if not m:
        raise typer.BadParameter(f"PIP id 格式: PIP-001 或 1 (拿到 '{raw}')")
    n = int(m.group(1))
    if n <= 0:
        raise typer.BadParameter(f"PIP id 必须 > 0 (拿到 {n})")
    return n


# ── dao propose ──────────────────────────────────────────────────────────────


@dao_app.command("propose")
def cmd_propose(
    pip_id_raw: str = typer.Argument(..., help="PIP id (e.g. PIP-003 或 3)"),
    next_status: str = typer.Option(
        "review",
        "--next-status",
        "-s",
        help="目标状态 review / finalcall / final / withdrawn",
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="DAO config 路径 (默认 ~/.sisoul/dao_config.json)"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """提案 PIP 升级状态 (e.g. Draft → Review)."""
    pip_id = _parse_pip_id(pip_id_raw)
    try:
        cfg = load_dao_config(config_path)
    except DAOError as e:
        typer.echo(f"❌ config: {e}", err=True)
        raise typer.Exit(code=2)

    client = GovernorClient(cfg)
    try:
        summary = propose_pip_promotion(pip_id, next_status, client)
    except Web3NotInstalledError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=4)
    except DAOError as e:
        typer.echo(f"❌ propose 失败: {e}", err=True)
        raise typer.Exit(code=2)

    if json_output:
        typer.echo(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return

    typer.echo(f"✅ proposal 已提交 (mode={cfg.mode}):")
    typer.echo(f"   proposal_id: {summary.proposal_id}")
    typer.echo(f"   state:       {summary.state_name}")
    typer.echo(f"   target:      PIP-{pip_id:03d} → {next_status}")
    typer.echo(f"   tx_hash:     {summary.tx_hash}")
    typer.echo(f"   desc_hash:   {summary.description_hash}")


# ── dao vote ─────────────────────────────────────────────────────────────────


@dao_app.command("vote")
def cmd_vote(
    proposal_id: int = typer.Argument(..., help="proposal id (uint256)"),
    support: str = typer.Argument(..., help="for / against / abstain"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="config 路径"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """投票."""
    try:
        cfg = load_dao_config(config_path)
    except DAOError as e:
        typer.echo(f"❌ config: {e}", err=True)
        raise typer.Exit(code=2)
    client = GovernorClient(cfg)
    try:
        tx = client.cast_vote(proposal_id, support)
    except ProposalNotFoundError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)
    except Web3NotInstalledError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=4)
    except DAOError as e:
        typer.echo(f"❌ vote 失败: {e}", err=True)
        raise typer.Exit(code=2)

    if json_output:
        typer.echo(json.dumps({"tx_hash": tx, "proposal_id": proposal_id, "support": support}))
        return
    typer.echo(f"✅ 投票完成 ({cfg.mode}):")
    typer.echo(f"   proposal_id: {proposal_id}")
    typer.echo(f"   support:     {support}")
    typer.echo(f"   tx_hash:     {tx}")


# ── dao status ───────────────────────────────────────────────────────────────


@dao_app.command("status")
def cmd_status(
    proposal_id: int = typer.Argument(..., help="proposal id"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="config 路径"),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """查 proposal 状态 + 票数."""
    try:
        cfg = load_dao_config(config_path)
    except DAOError as e:
        typer.echo(f"❌ config: {e}", err=True)
        raise typer.Exit(code=2)
    client = GovernorClient(cfg)
    try:
        summary = client.summary(proposal_id)
    except ProposalNotFoundError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)
    except Web3NotInstalledError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=4)
    except DAOError as e:
        typer.echo(f"❌ status 失败: {e}", err=True)
        raise typer.Exit(code=2)

    if json_output:
        typer.echo(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return
    typer.echo(f"proposal_id: {proposal_id}")
    typer.echo(f"  state:    {summary.state_name} ({summary.state})")
    typer.echo(f"  for:      {summary.votes_for}")
    typer.echo(f"  against:  {summary.votes_against}")
    typer.echo(f"  abstain:  {summary.votes_abstain}")
    typer.echo(f"  proposer: {summary.proposer}")
    if summary.tx_hash:
        typer.echo(f"  propose_tx: {summary.tx_hash}")


# ── dao config ───────────────────────────────────────────────────────────────


@dao_app.command("config")
def cmd_config(
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="config 路径"),
    show: bool = typer.Option(False, "--show", help="显示当前 config"),
    set_mode: Optional[str] = typer.Option(None, "--set-mode", help="mock / live"),
    set_rpc: Optional[str] = typer.Option(None, "--set-rpc", help="RPC URL"),
    set_chain_id: Optional[int] = typer.Option(None, "--set-chain-id", help="chain id"),
    set_governor: Optional[str] = typer.Option(None, "--set-governor", help="governor 地址"),
    set_token: Optional[str] = typer.Option(None, "--set-token", help="token 地址"),
    set_pip_registry: Optional[str] = typer.Option(
        None, "--set-pip-registry", help="PIPRegistry 地址"
    ),
    set_skill_registry: Optional[str] = typer.Option(
        None, "--set-skill-registry", help="SkillRegistry 地址"
    ),
    set_timelock: Optional[str] = typer.Option(None, "--set-timelock", help="Timelock 地址"),
    set_sender: Optional[str] = typer.Option(None, "--set-sender", help="sender 地址 (mock)"),
    set_private_key_path: Optional[str] = typer.Option(
        None, "--set-private-key-path", help="hex private key 文件路径"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 输出"),
) -> None:
    """查/改 DAO config."""
    try:
        cfg = load_dao_config(config_path)
    except DAOError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=2)

    changed = False
    if set_mode is not None:
        if set_mode not in ("mock", "live"):
            typer.echo("❌ mode 必须是 mock / live", err=True)
            raise typer.Exit(code=1)
        cfg.mode = set_mode  # type: ignore[assignment]
        changed = True
    if set_rpc is not None:
        cfg.rpc_url = set_rpc
        changed = True
    if set_chain_id is not None:
        cfg.chain_id = set_chain_id
        changed = True
    if set_governor is not None:
        cfg.governor_address = set_governor
        changed = True
    if set_token is not None:
        cfg.token_address = set_token
        changed = True
    if set_pip_registry is not None:
        cfg.pip_registry_address = set_pip_registry
        changed = True
    if set_skill_registry is not None:
        cfg.skill_registry_address = set_skill_registry
        changed = True
    if set_timelock is not None:
        cfg.timelock_address = set_timelock
        changed = True
    if set_sender is not None:
        cfg.sender_address = set_sender
        changed = True
    if set_private_key_path is not None:
        cfg.private_key_path = set_private_key_path
        changed = True

    if changed:
        saved = save_dao_config(cfg, config_path)
        typer.echo(f"✅ DAO config 保存: {saved}")

    if show or not changed:
        if json_output:
            typer.echo(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2))
        else:
            typer.echo("当前 DAO config:")
            for k, v in cfg.to_dict().items():
                typer.echo(f"  {k}: {v}")


__all__ = ["dao_app"]
