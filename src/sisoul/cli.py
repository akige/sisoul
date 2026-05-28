"""sisoul CLI 主入口 · 波 2 ship (Phase 1 W3-W14 真实现).

11+ 命令 (按 §28 §1.1 模块 1 设计):

实现状态:
- ✅ init       (W3, dev-A)  — 引导建本地 vault + 长期目标
- ✅ login      (W5, dev-B)  — 接 LLM provider
- ✅ ask        (W5, dev-B)  — 轻量 LLM query (新加)
- ✅ sync       (W7-W10, dev-C) — 同步 vault 到 5 工具入口 (子 app, --tool/--dry-run/--apply)
- ✅ remember   (W11, dev-A) — 教偏好, 写 vault preferences
- ✅ status     (W3, dev-A)  — 查 vault + 长期目标 + daemon 状态
- ✅ goals      (W12, dev-A) — 长期目标管理 (子 app, list/add/progress)
- ✅ export     (W13, dev-D) — 一键 ZIP 导出
- ✅ restore    (W13, dev-D, ZIP 部分; BIP-39 seed Phase 2 W19+)
- ✅ daemon     (W2, 本会话) — 启动 HTTP API 后台 daemon
- ⏸️ verify     stub (Phase 3 W37-W40)
- ⏸️ friend     stub (Phase 4 W51-W53)
- ⏸️ borrow     stub (Phase 4 W66-W74)
- ⏸️ lend       stub (Phase 4 W59-W62)
"""

from __future__ import annotations

import typer

from sisoul import __version__, __phase__

app = typer.Typer(
    name="sisoul",
    help="Decentralized meta-layer protocol for AI workflow.",
    no_args_is_help=True,
    add_completion=False,
)


def _not_implemented(cmd: str) -> None:
    """Stub for unfinished Phase 2+ commands."""
    typer.echo(f"sisoul {cmd} — not implemented yet (phase: {__phase__})")
    typer.echo("详 obs §29 §3 Phase 1 周计划 (本命令将在 Week N 实现).")


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(False, "--version", "-V", help="显示版本号"),
) -> None:
    """sisoul 元层协议 CLI."""
    if version:
        typer.echo(f"sisoul {__version__} ({__phase__})")
        raise typer.Exit()


# ── init (W3, dev-A) ─────────────────────────────────────────────────────────
from sisoul.cli_commands.init import cli_init  # noqa: E402

app.command("init")(cli_init)


# ── login (W5, dev-B) ────────────────────────────────────────────────────────
from sisoul.cli_commands.login import cli_login  # noqa: E402

app.command("login")(cli_login)


# ── ask (W5, dev-B, 新加) ────────────────────────────────────────────────────
from sisoul.cli_commands.ask import cli_ask  # noqa: E402

app.command("ask")(cli_ask)


# ── sync (W7-W10, dev-C, Typer 子 app) ───────────────────────────────────────
from sisoul.cli_commands.sync import sync_app  # noqa: E402

app.add_typer(sync_app, name="sync")


# ── remember (W11, dev-A) ────────────────────────────────────────────────────
from sisoul.cli_commands.remember import cli_remember  # noqa: E402

app.command("remember")(cli_remember)


# ── status (W3, dev-A) ───────────────────────────────────────────────────────
from sisoul.cli_commands.status import cli_status  # noqa: E402

app.command("status")(cli_status)


# ── goals (W12, dev-A, Typer 子 app) ─────────────────────────────────────────
from sisoul.cli_commands.goals import goals_app  # noqa: E402

app.add_typer(goals_app, name="goals")


# ── did (W21-W22, 波 3 dev-B, Typer 子 app) ──────────────────────────────────
from sisoul.cli_commands.did import did_app  # noqa: E402

app.add_typer(did_app, name="did")


# ── p2p (W31-W36, 波 4 dev-A, Typer 子 app) ──────────────────────────────────
from sisoul.cli_commands.p2p import p2p_app  # noqa: E402

app.add_typer(p2p_app, name="p2p")


# ── peer (Wave A #16 · §F.4 STUN 池 + relay-mode) ────────────────────────────
from sisoul.cli_commands.peer import peer_app  # noqa: E402

app.add_typer(peer_app, name="peer")


# ── attest (W37-W40, 波 4 dev-B, Typer 子 app) ───────────────────────────────
from sisoul.cli_commands.attest import attest_app  # noqa: E402

app.add_typer(attest_app, name="attest")


# ── snapshot (W41-W43, 波 4 dev-C, Typer 子 app) ─────────────────────────────
from sisoul.cli_commands.snapshot import snapshot_app  # noqa: E402

app.add_typer(snapshot_app, name="snapshot")


# ── export (W13, dev-D) ──────────────────────────────────────────────────────
@app.command()
def export(
    output: str = typer.Option(None, "--output", "-o",
                               help="ZIP 输出路径 (默认: ~/sisoul-export-<YYYY-MM-DD-HHMM>.zip)"),
    vault_dir: str = typer.Option(None, "--vault-dir", help="vault 路径 (默认 ~/.sisoul/)"),
) -> None:
    """一键 ZIP 导出 vault (Phase 1 W13, dev-D)."""
    from pathlib import Path
    from sisoul.cli_commands.export import run_export
    run_export(
        output=Path(output) if output else None,
        vault_dir=Path(vault_dir) if vault_dir else None,
    )


# ── restore (W13 ZIP, dev-D; BIP-39 seed Phase 2 W19+) ───────────────────────
@app.command()
def restore(
    seed: str = typer.Argument(None, help="BIP-39 12 词 seed (Phase 2 W19-W20)"),
    from_zip: str = typer.Option(None, "--from-zip", help="ZIP 文件路径 (Phase 1 W13)"),
    vault_dir: str = typer.Option(None, "--vault-dir", help="还原目标路径 (默认 ~/.sisoul/)"),
    force: bool = typer.Option(False, "--force", help="vault 已存在时强制覆盖"),
) -> None:
    """从 ZIP 还原 vault 或 BIP-39 seed 跨机恢复."""
    from pathlib import Path
    if from_zip:
        from sisoul.cli_commands.restore import run_restore
        run_restore(
            zip_path=Path(from_zip),
            vault_dir=Path(vault_dir) if vault_dir else None,
            force=force,
        )
    elif seed:
        # 波 3 dev-A ship 的 BIP-39 seed restore (qa-D P0-1 修)
        from sisoul.cli_commands.restore import run_restore_from_seed
        run_restore_from_seed(
            seed=seed,
            vault_dir=Path(vault_dir) if vault_dir else None,
            force=force,
        )
    else:
        typer.echo("sisoul restore: 必须提供 <seed> 参数或 --from-zip <path>")
        raise typer.Exit(code=1)


# ── verify stub (Phase 3 W37-W40) ────────────────────────────────────────────
@app.command()
def verify() -> None:
    """检查 vault 完整性 + 链上 attestation (Phase 3 W37-W40)."""
    _not_implemented("verify")


# ── friend (W51-W53, 波 5 dev-A, Typer 子 app, 真实现替换 stub) ───────────────
from sisoul.cli_commands.friend import friend_app  # noqa: E402

app.add_typer(friend_app, name="friend")


# ── proxy (W54-W58, 波 5 dev-B, Typer 子 app) ────────────────────────────────
from sisoul.cli_commands.proxy import proxy_app  # noqa: E402

app.add_typer(proxy_app, name="proxy")


# ── perms (W59-W65, 波 5 dev-C, Typer 子 app) ────────────────────────────────
from sisoul.cli_commands.permissions import perms_app  # noqa: E402

app.add_typer(perms_app, name="perms")


# ── borrow (W66-W74, 波 5 dev-D, 真实现替换 stub) ────────────────────────────
from sisoul.cli_commands.borrow import borrow_app  # noqa: E402

app.add_typer(borrow_app, name="borrow")


# ── lend (W59-W62, 波 5 dev-D, 真实现替换 stub) ──────────────────────────────
from sisoul.cli_commands.lend import lend_app  # noqa: E402

app.add_typer(lend_app, name="lend")


# ── ledger (W66-W74, 波 5 dev-D, 互惠 ledger 查询) ───────────────────────────
from sisoul.cli_commands.ledger import ledger_app  # noqa: E402

app.add_typer(ledger_app, name="ledger")


# ── skill (W70-W74, 波 6 dev-A, AI 技能 packaging + IPFS + borrow lifecycle) ─
from sisoul.cli_commands.skill import skill_app  # noqa: E402

app.add_typer(skill_app, name="skill")


# ── dao (Phase 3 P3-4, SisoulGov + PIPRegistry on-chain governance) ──────────
from sisoul.cli_commands.dao import dao_app  # noqa: E402

app.add_typer(dao_app, name="dao")


# ── daemon (W2, 本会话) ──────────────────────────────────────────────────────
@app.command()
def daemon(
    host: str = typer.Option("127.0.0.1", "--host", help="bind host (默认 loopback)"),
    port: int = typer.Option(9876, "--port",
                             help="bind port (默认 9876, 详 __init__.py 端口选择理由)"),
) -> None:
    """启动 sisoul daemon (Phase 1 W2 ship)."""
    from sisoul.daemon import run_daemon

    typer.echo(f"sisoul daemon starting on http://{host}:{port} ...")
    typer.echo(f"  GET http://{host}:{port}/sisoul/health")
    run_daemon(host=host, port=port)





# ── notify (Wave B' P1-1, 推送 / 在线状态 · agent-B3) ────────────────────────
from sisoul.cli_commands.notify import notify_app  # noqa: E402

app.add_typer(notify_app, name="notify")

if __name__ == "__main__":
    app()
