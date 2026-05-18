"""sisoul snapshot · CLI 子 app (Phase 3 W41-W43 dev-C).

5 命令:
- sisoul snapshot now [--upload arweave|ipfs|both]
- sisoul snapshot list
- sisoul snapshot restore <tx_id_or_cid_or_hash>
- sisoul snapshot schedule [--monthly|weekly|never]
- sisoul snapshot config

接入: cli.py `app.add_typer(snapshot_app, name="snapshot")` (主集成做, 不动 cli.py).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import typer

from sisoul.onchain.arweave import (
    DEFAULT_HISTORY_PATH,
    ArweaveSnapshot,
    SnapshotHistory,
    schedule_monthly_snapshot,
)
from sisoul.vault.storage import DEFAULT_VAULT_DIR

snapshot_app = typer.Typer(
    name="snapshot",
    help="加密 vault snapshot → IPFS pin (即时) + Arweave 上链 (异步, 永久)",
    no_args_is_help=True,
)


# ── 配置文件 (Pinata JWT / Arweave wallet / IPFS daemon URL) ──────────────
CONFIG_PATH = Path.home() / ".sisoul" / "snapshot_config.json"


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    # 含 secret, 收紧权限
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def _build_client(
    vault_dir: Optional[Path] = None,
    network: str = "testnet",
) -> ArweaveSnapshot:
    """从 config + env 构造 ArweaveSnapshot."""
    cfg = _load_config()
    pinata = os.environ.get("PINATA_JWT") or cfg.get("pinata_jwt")
    wallet = os.environ.get("ARWEAVE_WALLET") or cfg.get("arweave_wallet")
    return ArweaveSnapshot(
        pinata_jwt=pinata,
        arweave_wallet_path=Path(wallet) if wallet else None,
        network=network,  # type: ignore[arg-type]
    )


# ── 1. snapshot now ──────────────────────────────────────────────────────


@snapshot_app.command("now")
def cmd_now(
    upload: str = typer.Option(
        "both", "--upload",
        help="上传策略: arweave / ipfs / both / none",
    ),
    vault_dir: Optional[Path] = typer.Option(
        None, "--vault-dir",
        help=f"vault 路径 (默认 {DEFAULT_VAULT_DIR})",
    ),
    network: str = typer.Option(
        "testnet", "--network",
        help="testnet (默认 · 安全) / mainnet (需 ARWEAVE_ALLOW_MAINNET=1) / mock",
    ),
) -> None:
    """立即生成一次加密 snapshot 并上传."""
    if upload not in ("arweave", "ipfs", "both", "none"):
        typer.echo(f"❌ --upload 必须 ∈ arweave/ipfs/both/none, 实际: {upload}", err=True)
        raise typer.Exit(2)

    root = Path(vault_dir) if vault_dir else DEFAULT_VAULT_DIR
    if not root.exists():
        typer.echo(f"❌ vault 不存在: {root}", err=True)
        typer.echo("  先跑 sisoul init", err=True)
        raise typer.Exit(1)

    client = _build_client(root, network=network)
    typer.echo(f"📸 snapshot vault {root} · network={client.network} · upload={upload}")
    record = client.snapshot_now(root, upload=upload)  # type: ignore[arg-type]
    typer.echo(f"  size: {record.size_bytes:,} bytes")
    typer.echo(f"  sha256: {record.sha256}")
    typer.echo(f"  key_fingerprint: {record.vault_master_key_fingerprint}")
    if record.ipfs_cid:
        typer.echo(f"  ipfs_cid: {record.ipfs_cid}")
    if record.arweave_tx_id:
        typer.echo(f"  arweave_tx_id: {record.arweave_tx_id}")
    if record.status == "failed":
        typer.echo(f"❌ 部分失败: {record.error}", err=True)
        raise typer.Exit(1)
    typer.echo(f"✅ 写入 history: {client.history.path}")


# ── 2. snapshot list ─────────────────────────────────────────────────────


@snapshot_app.command("list")
def cmd_list(
    history_path: Optional[Path] = typer.Option(
        None, "--history",
        help=f"history 路径 (默认 {DEFAULT_HISTORY_PATH})",
    ),
    fmt: str = typer.Option("table", "--format", help="table / json"),
) -> None:
    """历史 snapshot 列表."""
    h = SnapshotHistory(history_path)
    records = h.load()
    if not records:
        typer.echo("(无 snapshot 历史; 跑 `sisoul snapshot now` 创建第一个)")
        return

    if fmt == "json":
        typer.echo(json.dumps([asdict(r) for r in records], indent=2, ensure_ascii=False))
        return

    # table
    typer.echo(f"{'timestamp':27} {'size':>10} {'status':8} {'ipfs':12} {'arweave':12} sha256[:12]")
    typer.echo("-" * 100)
    for r in records:
        size_human = _human_size(r.size_bytes)
        ipfs = (r.ipfs_cid or "-")[:12]
        ar = (r.arweave_tx_id or "-")[:12]
        typer.echo(
            f"{r.timestamp[:26]:27} {size_human:>10} {r.status:8} {ipfs:12} {ar:12} {r.sha256[:12]}"
        )


# ── 3. snapshot restore ──────────────────────────────────────────────────


@snapshot_app.command("restore")
def cmd_restore(
    tx_or_cid_or_hash: str = typer.Argument(
        ..., help="Arweave tx_id 或 IPFS CID 或 sha256 hash"
    ),
    target: Path = typer.Option(
        ..., "--target",
        help="还原目标 dir (必须空 / 不存在)",
    ),
    source: str = typer.Option(
        "auto", "--source",
        help="auto (默认 · 按前缀判) / arweave / ipfs",
    ),
    network: str = typer.Option(
        "testnet", "--network",
        help="testnet / mainnet / mock",
    ),
) -> None:
    """从 snapshot 还原 vault."""
    if source not in ("auto", "arweave", "ipfs"):
        typer.echo(f"❌ --source 必须 ∈ auto/arweave/ipfs", err=True)
        raise typer.Exit(2)

    client = _build_client(network=network)
    # 如果用户传的是 sha256 hash, 先去 history 查
    if len(tx_or_cid_or_hash) == 64 and all(c in "0123456789abcdef" for c in tx_or_cid_or_hash.lower()):
        rec = client.history.find(tx_or_cid_or_hash)
        if rec is None:
            typer.echo(f"❌ 在 history 没找到 sha256={tx_or_cid_or_hash}", err=True)
            raise typer.Exit(1)
        actual = rec.arweave_tx_id or rec.ipfs_cid
        if not actual:
            typer.echo(f"❌ history 这条没 ipfs/arweave 凭证: {rec}", err=True)
            raise typer.Exit(1)
        typer.echo(f"  hash → ipfs_cid/tx_id: {actual}")
        tx_or_cid_or_hash = actual

    typer.echo(f"🔄 restore from {source}={tx_or_cid_or_hash[:40]}... → {target}")
    try:
        out = client.restore_from_arweave(
            tx_or_cid_or_hash,
            target_vault_dir=target,
            source=source,  # type: ignore[arg-type]
        )
    except (FileExistsError, RuntimeError) as e:
        typer.echo(f"❌ restore 失败: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"✅ 还原完成: {out}")


# ── 4. snapshot schedule ────────────────────────────────────────────────


@snapshot_app.command("schedule")
def cmd_schedule(
    monthly: bool = typer.Option(False, "--monthly", help="月度 (Day=1 3:00)"),
    weekly: bool = typer.Option(False, "--weekly", help="周度 (Mon 3:00)"),
    daily: bool = typer.Option(False, "--daily", help="日度 (3:00)"),
    never: bool = typer.Option(False, "--never", help="不调度 (打印空 unit)"),
    upload: str = typer.Option("both", "--upload", help="arweave / ipfs / both"),
    install: bool = typer.Option(
        False, "--install",
        help="真写到 ~/Library/LaunchAgents/ 或 ~/.config/systemd/user/ "
             "(不自动 launchctl load, 用户手动确认)",
    ),
) -> None:
    """设周期 snapshot (生成 launchd plist / systemd timer)."""
    flags = [monthly, weekly, daily, never]
    if sum(flags) > 1:
        typer.echo("❌ --monthly/--weekly/--daily/--never 只能选一", err=True)
        raise typer.Exit(2)
    if sum(flags) == 0:
        cadence = "monthly"
    else:
        cadence = ["monthly", "weekly", "daily", "never"][flags.index(True)]

    if upload not in ("arweave", "ipfs", "both"):
        typer.echo(f"❌ --upload 必须 ∈ arweave/ipfs/both", err=True)
        raise typer.Exit(2)

    result = schedule_monthly_snapshot(
        cadence=cadence,  # type: ignore[arg-type]
        upload=upload,  # type: ignore[arg-type]
        install=install,
    )
    typer.echo(f"system: {result['system']}  cadence: {result['cadence']}  upload: {result['upload']}")
    typer.echo("───── unit text ─────")
    typer.echo(result["unit_text"])
    typer.echo("───── end ─────")
    if install:
        typer.echo(f"✅ 已写入: {result['install_path']}")
        if result["system"] == "darwin":
            typer.echo("  下一步: launchctl load ~/Library/LaunchAgents/io.sisoul.snapshot.*.plist")
        elif result["system"] == "linux":
            typer.echo("  下一步: systemctl --user daemon-reload && systemctl --user enable --now sisoul-snapshot.timer")


# ── 5. snapshot config ──────────────────────────────────────────────────


@snapshot_app.command("config")
def cmd_config(
    set_pinata_jwt: Optional[str] = typer.Option(None, "--set-pinata-jwt"),
    set_arweave_wallet: Optional[Path] = typer.Option(None, "--set-arweave-wallet"),
    set_ipfs_daemon: Optional[str] = typer.Option(None, "--set-ipfs-daemon"),
    show: bool = typer.Option(False, "--show", help="显示当前配置 (脱敏)"),
    clear: bool = typer.Option(False, "--clear", help="清空配置"),
) -> None:
    """查/改 Arweave / IPFS 配置."""
    cfg = _load_config()

    if clear:
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
        typer.echo("✅ 已清空配置")
        return

    changed = False
    if set_pinata_jwt is not None:
        cfg["pinata_jwt"] = set_pinata_jwt
        changed = True
    if set_arweave_wallet is not None:
        cfg["arweave_wallet"] = str(Path(set_arweave_wallet).expanduser().resolve())
        changed = True
    if set_ipfs_daemon is not None:
        cfg["ipfs_daemon_url"] = set_ipfs_daemon
        changed = True

    if changed:
        _save_config(cfg)
        typer.echo(f"✅ 已更新: {CONFIG_PATH}")

    # show (默认行为, 没传 set 参数也 show)
    if show or not changed:
        typer.echo(f"config path: {CONFIG_PATH}")
        if not cfg:
            typer.echo("(空 · 跑 `sisoul snapshot config --set-pinata-jwt <jwt>` 配置)")
        else:
            for k, v in cfg.items():
                # 脱敏 jwt: 只显示前 8 + 后 4
                if k == "pinata_jwt" and isinstance(v, str) and len(v) > 16:
                    typer.echo(f"  {k}: {v[:8]}...{v[-4:]}")
                else:
                    typer.echo(f"  {k}: {v}")
        # env 提示
        typer.echo("env overrides (优先):")
        typer.echo(f"  PINATA_JWT: {'set' if os.environ.get('PINATA_JWT') else 'unset'}")
        typer.echo(f"  ARWEAVE_WALLET: {os.environ.get('ARWEAVE_WALLET') or 'unset'}")
        typer.echo(f"  ARWEAVE_ALLOW_MAINNET: {os.environ.get('ARWEAVE_ALLOW_MAINNET') or 'unset (mainnet 拒绝)'}")


# ── helpers ─────────────────────────────────────────────────────────────


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n = n // 1024 if unit == "B" else n  # noqa: PLR1714
        if unit != "B":
            n = n
    return f"{n} B"
