"""sisoul status 命令 (Phase 1 W3).

输出 markdown 表:
- Vault path / size / 文件数
- 长期目标 N active + 各进度
- Preferences N 条
- Daemon running/stopped (尝试 GET 127.0.0.1:9876/sisoul/health, 1s timeout)
- Last sync: N/A (Phase 1 W7+ 加)
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from sisoul import DAEMON_BASE_URL
from sisoul.vault import (
    DEFAULT_VAULT_DIR,
    VaultPaths,
    list_files,
    load_frontmatter,
    read_file,
    vault_size,
)


def _check_daemon(base_url: str = DAEMON_BASE_URL, timeout: float = 1.0) -> tuple[bool, str]:
    """尝试调 GET /sisoul/health. 返回 (alive, status_str)."""
    try:
        import httpx
    except ImportError:
        return False, "httpx not installed"
    try:
        r = httpx.get(f"{base_url}/sisoul/health", timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            return True, data.get("status", "ok")
        return False, f"http {r.status_code}"
    except Exception as e:  # noqa: BLE001 - daemon 不在线一律视作 stopped
        return False, type(e).__name__


def _read_dna(paths: VaultPaths) -> dict | None:
    if not paths.dna.exists():
        return None
    try:
        return json.loads(read_file(paths.dna))
    except (json.JSONDecodeError, OSError):
        return None


def _collect_goals(paths: VaultPaths) -> list[dict]:
    """读 goals/*.md, 返回 [{id, title, progress, status}, ...]."""
    out = []
    for fp in list_files(paths.goals_dir, "*.md"):
        try:
            meta, _ = load_frontmatter(read_file(fp))
            out.append(
                {
                    "id": meta.get("id", fp.stem),
                    "title": meta.get("title", "(no title)"),
                    "progress": meta.get("progress", 0),
                    "status": meta.get("status", "active"),
                }
            )
        except OSError:
            continue
    return out


def _count_preferences(paths: VaultPaths) -> int:
    """preferences/<date>.md 内每个 entry (--- 分隔的 frontmatter block) 算 1 条.

    简化 Phase 1 W3: 每个文件 = 1 条 (Phase 1 W11 起一个文件多 entry, 再算 sub-entry)."""
    files = list_files(paths.preferences_dir, "*.md")
    total = 0
    for f in files:
        try:
            text = read_file(f)
            # 简单算 frontmatter block 数 (--- 出现次数 / 2)
            n_blocks = text.count("\n---\n") // 2 + (1 if text.startswith("---\n") else 0)
            total += max(n_blocks, 1)
        except OSError:
            continue
    return total


def render_status(vault_dir: Path | None = None) -> str:
    """生成 status markdown 表. 主逻辑可被单元测试调."""
    root = Path(vault_dir) if vault_dir is not None else DEFAULT_VAULT_DIR
    paths = VaultPaths(root=root)

    exists = root.exists()
    size = vault_size(root)
    dna = _read_dna(paths)
    goals = _collect_goals(paths)
    n_pref = _count_preferences(paths)
    daemon_alive, daemon_status = _check_daemon()

    lines: list[str] = []
    lines.append("# sisoul status")
    lines.append("")
    lines.append("| field | value |")
    lines.append("|---|---|")
    lines.append(f"| vault path | `{root}` |")
    lines.append(f"| vault exists | {'✅' if exists else '❌ (run `sisoul init`)'} |")
    lines.append(f"| vault size | {size} bytes |")
    if dna:
        lines.append(f"| dna.sisoul_version | {dna.get('sisoul_version', '?')} |")
        lines.append(f"| dna.created_at | {dna.get('vault_created_at', '?')} |")
        lines.append(f"| dna.master_key_hash | {dna.get('master_key_hash', '?')} |")
    lines.append(f"| goals | {len(goals)} active |")
    lines.append(f"| preferences | {n_pref} 条 |")
    lines.append(
        f"| daemon | {'running' if daemon_alive else 'stopped'} ({daemon_status}) |"
    )
    lines.append("| last sync | N/A (Phase 1 W7+) |")
    lines.append("")
    if goals:
        lines.append("## 长期目标")
        lines.append("")
        lines.append("| id | title | progress | status |")
        lines.append("|---|---|---|---|")
        for g in goals:
            lines.append(
                f"| {g['id']} | {g['title']} | {g['progress']}/100 | {g['status']} |"
            )
        lines.append("")
    return "\n".join(lines)


def cli_status(
    vault_dir: Path = typer.Option(
        None, "--vault-dir", help="vault 路径 (默认 ~/.sisoul/)"
    ),
) -> None:
    """查 vault / 长期目标 / daemon 状态."""
    typer.echo(render_status(vault_dir=vault_dir))
