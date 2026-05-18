"""sisoul sync · 把 vault 偏好同步到 5 工具入口文件 (Phase 1 W7-W10).

§28 §1.1 模块 4. CLI 入口:

    sisoul sync                          # 全部 5 工具 (项目级需 --project-root)
    sisoul sync --dry-run                # 不写, 显示 diff
    sisoul sync --tool claude_code       # 仅同步某工具
    sisoul sync --project-root /path     # 项目级工具用此根目录

注意: 本命令暂时**不依赖** vault.frontmatter 真实读取
(vault 读取由 dev-A 的 init/remember 命令落地). 本 v1 用 vault preferences/ +
goals/ 下的 markdown 文件 title + body 作为简单数据源, 没文件就用空 list.

Phase 2 整合: 接入 vault.frontmatter 后改本 _load_preferences/_load_goals.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from sisoul.sync import (
    ALL_ADAPTERS,
    Goal,
    Preference,
    SyncResult,
    sync_all,
    sync_one_tool,
)
from sisoul.vault.storage import DEFAULT_VAULT_DIR, VaultPaths, list_files


sync_app = typer.Typer(help="同步 vault 偏好到 5 工具入口 (Phase 1 W7-W10)")


def _load_preferences(vault_root: Path) -> list[Preference]:
    """vault preferences/*.md 读出 (Preference list).

    每文件 first heading (# X) 当 title, 其余当 body. 没 heading → filename 当 title.
    本 v1 简单版, Phase 2 改 frontmatter.
    """
    paths = list_files(VaultPaths(root=vault_root).preferences_dir, "*.md")
    out: list[Preference] = []
    for p in paths:
        text = p.read_text(encoding="utf-8")
        lines = text.splitlines()
        title = p.stem
        body_start = 0
        for i, line in enumerate(lines):
            if line.startswith("# "):
                title = line[2:].strip()
                body_start = i + 1
                break
        body = "\n".join(lines[body_start:]).strip() or "(no body)"
        out.append(Preference(title=title, body=body))
    return out


def _load_goals(vault_root: Path) -> list[Goal]:
    """vault goals/*.md 读出 (Goal list)."""
    paths = list_files(VaultPaths(root=vault_root).goals_dir, "*.md")
    out: list[Goal] = []
    for p in paths:
        text = p.read_text(encoding="utf-8")
        lines = text.splitlines()
        title = p.stem
        for line in lines:
            if line.startswith("# "):
                title = line[2:].strip()
                break
        out.append(Goal(id=p.stem, title=title, progress=""))
    return out


def _format_result(r: SyncResult) -> str:
    icon = "✅" if r.success else "❌"
    state = "[new]" if r.first_sync else "[update]"
    if not r.success:
        return f"{icon} {r.tool_name:13} → {r.entry_path}  ERROR: {r.error}"
    written = "wrote" if r.written else "dry-run"
    return f"{icon} {r.tool_name:13} {state:9} {written:7} → {r.entry_path}"


@sync_app.callback(invoke_without_command=True)
def sync(
    tool: Optional[str] = typer.Option(
        None, "--tool", "-t",
        help=f"仅同步某工具: {', '.join(ALL_ADAPTERS.keys())} (默认全部)",
    ),
    all_tools: bool = typer.Option(
        False, "--all",
        help="同步全部 5 工具 (跟省略 --tool 同效, 显式 flag 方便脚本)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="只显示 diff, 不真写",
    ),
    apply: bool = typer.Option(
        False, "--apply",
        help="真写 (默认行为, flag 仅为对称)",
    ),
    project_root: Optional[Path] = typer.Option(
        None, "--project-root", "-p",
        help="项目根目录 (cursor/aider/opencode 需要)",
    ),
    vault_root: Path = typer.Option(
        DEFAULT_VAULT_DIR, "--vault-root",
        help=f"vault 根目录 (默认 {DEFAULT_VAULT_DIR})",
    ),
    home: Optional[Path] = typer.Option(
        None, "--home",
        help="用户 HOME 覆盖 (测试用; 默认 Path.home())",
    ),
) -> None:
    """把 vault 偏好同步到 5 工具入口文件."""
    if tool is not None and tool not in ALL_ADAPTERS:
        typer.echo(
            f"未知 tool '{tool}'. 支持: {', '.join(ALL_ADAPTERS.keys())}",
            err=True,
        )
        raise typer.Exit(code=2)

    if dry_run and apply:
        typer.echo("不能同时 --dry-run + --apply", err=True)
        raise typer.Exit(code=2)

    if not Path(vault_root).exists():
        typer.echo(
            f"vault root {vault_root} 不存在, 用空 preferences/goals 渲染 "
            f"(先 `sisoul init` 建 vault)",
        )

    preferences = _load_preferences(Path(vault_root))
    goals = _load_goals(Path(vault_root))

    typer.echo(
        f"vault: {vault_root}  preferences={len(preferences)} goals={len(goals)}  "
        f"mode={'dry-run' if dry_run else 'apply'}"
    )

    if tool is not None:
        results = [
            sync_one_tool(
                tool,
                preferences,
                goals,
                project_root=project_root,
                home=home,
                dry_run=dry_run,
            )
        ]
    else:
        results = sync_all(
            preferences,
            goals,
            project_root=project_root,
            home=home,
            dry_run=dry_run,
        )

    typer.echo("")
    for r in results:
        typer.echo(_format_result(r))
        if dry_run and r.diff:
            typer.echo("--- diff ---")
            typer.echo(r.diff)
            typer.echo("--- end diff ---")

    failed = [r for r in results if not r.success]
    if failed:
        raise typer.Exit(code=1)
