"""sisoul goals 命令组 (Phase 1 W12).

子命令:
- list           列长期目标 + 进度
- add "title"    加新目标 (auto-assign id)
- progress <id> <delta>  手动加进度 (Phase 1 简化, W12+ 用 LLM 自动算)

Phase 2+ TODO: LLM 自动评估 task → goal alignment.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer

from sisoul.vault import (
    DEFAULT_VAULT_DIR,
    VaultPaths,
    dump_frontmatter,
    list_files,
    load_frontmatter,
    read_file,
    write_file,
)

goals_app = typer.Typer(name="goals", help="长期目标管理 (Phase 1 W12).", no_args_is_help=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _next_goal_id(paths: VaultPaths) -> str:
    """扫 goals/ 找最大 id 编号 +1."""
    max_n = 0
    for fp in list_files(paths.goals_dir, "goal-*.md"):
        try:
            n = int(fp.stem.split("-")[1])
            max_n = max(max_n, n)
        except (ValueError, IndexError):
            continue
    return f"goal-{max_n + 1:03d}"


def list_goals(vault_dir: Path | None = None) -> list[dict]:
    """读 goals/*.md 返回 list. 不存在 → []."""
    root = Path(vault_dir) if vault_dir is not None else DEFAULT_VAULT_DIR
    paths = VaultPaths(root=root)
    out = []
    for fp in list_files(paths.goals_dir, "*.md"):
        try:
            meta, body = load_frontmatter(read_file(fp))
            out.append(
                {
                    "id": meta.get("id", fp.stem),
                    "title": meta.get("title", "(no title)"),
                    "progress": int(meta.get("progress", 0)),
                    "status": meta.get("status", "active"),
                    "path": fp,
                    "body": body,
                    "_meta": meta,
                }
            )
        except (OSError, ValueError):
            continue
    return out


def add_goal(title: str, vault_dir: Path | None = None) -> Path:
    """加新目标. 返回新文件路径."""
    title = title.strip()
    if not title:
        raise ValueError("title 不能为空")
    root = Path(vault_dir) if vault_dir is not None else DEFAULT_VAULT_DIR
    paths = VaultPaths(root=root)
    paths.ensure_dirs()
    gid = _next_goal_id(paths)
    meta = {
        "id": gid,
        "title": title,
        "created_at": _now_iso(),
        "progress": 0,
        "status": "active",
    }
    body = f"# {title}\n\n_长期目标. 进度 0/100._\n"
    fp = paths.goals_dir / f"{gid}.md"
    write_file(fp, dump_frontmatter(meta, body))
    return fp


def update_progress(
    goal_id: str, delta: int, vault_dir: Path | None = None
) -> tuple[int, int]:
    """progress += delta (clamp 0..100). 返回 (old, new)."""
    root = Path(vault_dir) if vault_dir is not None else DEFAULT_VAULT_DIR
    paths = VaultPaths(root=root)
    fp = paths.goals_dir / f"{goal_id}.md"
    if not fp.exists():
        raise FileNotFoundError(f"goal not found: {goal_id} ({fp})")
    meta, body = load_frontmatter(read_file(fp))
    old = int(meta.get("progress", 0))
    new = max(0, min(100, old + delta))
    meta["progress"] = new
    meta["updated_at"] = _now_iso()
    if new >= 100 and meta.get("status") == "active":
        meta["status"] = "completed"
    write_file(fp, dump_frontmatter(meta, body))
    return old, new


@goals_app.command("list")
def cmd_list(
    vault_dir: Path = typer.Option(
        None, "--vault-dir", help="vault 路径 (默认 ~/.sisoul/)"
    ),
) -> None:
    """列长期目标 + 进度."""
    goals = list_goals(vault_dir=vault_dir)
    if not goals:
        typer.echo("(无长期目标. 运行 `sisoul init` 或 `sisoul goals add '...'`)")
        return
    typer.echo("| id | title | progress | status |")
    typer.echo("|---|---|---|---|")
    for g in goals:
        typer.echo(f"| {g['id']} | {g['title']} | {g['progress']}/100 | {g['status']} |")


@goals_app.command("add")
def cmd_add(
    title: str = typer.Argument(..., help="目标标题"),
    vault_dir: Path = typer.Option(
        None, "--vault-dir", help="vault 路径 (默认 ~/.sisoul/)"
    ),
) -> None:
    """加新长期目标."""
    try:
        fp = add_goal(title, vault_dir=vault_dir)
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"✅ 新目标已加: {fp}")


@goals_app.command("progress")
def cmd_progress(
    goal_id: str = typer.Argument(..., help="goal id, 例 goal-001"),
    delta: int = typer.Argument(..., help="进度增量 (正/负, 自动 clamp 0..100)"),
    vault_dir: Path = typer.Option(
        None, "--vault-dir", help="vault 路径 (默认 ~/.sisoul/)"
    ),
) -> None:
    """手动加进度 (Phase 1 简化, W12+ 用 LLM 自动算)."""
    try:
        old, new = update_progress(goal_id, delta, vault_dir=vault_dir)
    except FileNotFoundError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"✅ {goal_id} progress: {old} → {new}")
