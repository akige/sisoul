"""sisoul remember 命令 (Phase 1 W11).

`sisoul remember "X"` 写 preferences/<YYYY-MM-DD>.md.
同日多偏好累积到一个文件 (append, 用 `---\\n` 分隔 frontmatter block).

每条 entry:
- frontmatter: timestamp / scope=default / verified=false
- body: 原文偏好
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer

from sisoul.vault import (
    DEFAULT_VAULT_DIR,
    VaultPaths,
    dump_frontmatter,
    read_file,
    write_file,
)


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_remember(
    text: str,
    *,
    scope: str = "default",
    vault_dir: Path | None = None,
) -> Path:
    """append 一条偏好. 返回写入文件路径.

    raises:
        ValueError: text 空.
    """
    text = text.strip()
    if not text:
        raise ValueError("偏好内容不能为空")
    root = Path(vault_dir) if vault_dir is not None else DEFAULT_VAULT_DIR
    paths = VaultPaths(root=root)
    paths.ensure_dirs()

    fp = paths.preferences_dir / f"{_today_str()}.md"
    meta = {
        "timestamp": _now_iso(),
        "scope": scope,
        "verified": False,
    }
    entry = dump_frontmatter(meta, text)

    if fp.exists():
        existing = read_file(fp)
        if not existing.endswith("\n"):
            existing += "\n"
        # 用空行分隔多 entry (frontmatter 自带 --- 头尾, 直接 concat 即可)
        new_content = existing + "\n" + entry
    else:
        new_content = entry
    write_file(fp, new_content)
    return fp


def cli_remember(
    text: str = typer.Argument(..., help="偏好内容, 例 '我用 Tailwind CSS'"),
    scope: str = typer.Option("default", "--scope", help="偏好 scope (default / project / global)"),
    vault_dir: Path = typer.Option(
        None, "--vault-dir", help="vault 路径 (默认 ~/.sisoul/)"
    ),
) -> None:
    """教偏好, 写进 vault."""
    try:
        fp = run_remember(text, scope=scope, vault_dir=vault_dir)
    except ValueError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"✅ 偏好已写入: {fp}")
