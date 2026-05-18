"""sisoul sync 模块 (Phase 1 W7-W10).

§28 §1.1 模块 4: 跨工具 sync 5 工具
(Claude Code / Codex CLI / Cursor / Aider / OpenCode).

提供:
- sync_all(...)         — 同步全部 5 工具
- sync_one_tool(...)    — 同步单工具
- 5 个 adapter 类       — 5 工具入口文件格式各不同
- managed_section       — sisoul-managed 段标记 + 增量替换 (保留用户手写)
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from sisoul.sync.aider import AiderAdapter
from sisoul.sync.base import (
    Goal,
    Preference,
    SyncResult,
    ToolSyncAdapter,
)
from sisoul.sync.claude_code import ClaudeCodeAdapter
from sisoul.sync.codex import CodexAdapter
from sisoul.sync.cursor import CursorAdapter
from sisoul.sync.managed_section import (
    END_MARKER,
    START_MARKER,
    YAML_END_MARKER,
    YAML_START_MARKER,
    ManagedSectionError,
    MarkerPair,
    extract_managed_section,
    insert_or_replace,
)
from sisoul.sync.opencode import OpenCodeAdapter

#: 全部 5 工具 adapter 类 (注册表)
ALL_ADAPTERS: dict[str, type[ToolSyncAdapter]] = {
    "claude_code": ClaudeCodeAdapter,
    "codex": CodexAdapter,
    "cursor": CursorAdapter,
    "aider": AiderAdapter,
    "opencode": OpenCodeAdapter,
}

#: 用户级 vs 项目级 分组
USER_LEVEL_TOOLS = ("claude_code", "codex")
PROJECT_LEVEL_TOOLS = ("cursor", "aider", "opencode")


def get_adapter(
    tool_name: str,
    *,
    project_root: Path | None = None,
    home: Path | None = None,
) -> ToolSyncAdapter:
    """按名取 adapter 实例.

    tool_name 不在 ALL_ADAPTERS → KeyError.
    项目级工具没传 project_root → adapter.apply 时 ValueError.
    """
    cls = ALL_ADAPTERS[tool_name]
    return cls(project_root=project_root, home=home)


def sync_one_tool(
    tool_name: str,
    preferences: Iterable[Preference],
    goals: Iterable[Goal],
    *,
    project_root: Path | None = None,
    home: Path | None = None,
    dry_run: bool = False,
) -> SyncResult:
    """同步单工具."""
    adapter = get_adapter(tool_name, project_root=project_root, home=home)
    managed = adapter.render(preferences, goals)
    return adapter.apply(managed, dry_run=dry_run)


def sync_all(
    preferences: Iterable[Preference],
    goals: Iterable[Goal],
    *,
    project_root: Path | None = None,
    home: Path | None = None,
    dry_run: bool = False,
    only: Iterable[str] | None = None,
    skip: Iterable[str] | None = None,
) -> list[SyncResult]:
    """同步全部 5 工具 (或子集).

    only / skip 控制范围. 项目级工具 + 没传 project_root → 跳过 (返回失败 result).
    """
    prefs = list(preferences)
    gs = list(goals)
    results: list[SyncResult] = []
    only_set = set(only) if only else set(ALL_ADAPTERS.keys())
    skip_set = set(skip) if skip else set()

    for name in ALL_ADAPTERS.keys():
        if name not in only_set or name in skip_set:
            continue

        cls = ALL_ADAPTERS[name]
        # 项目级 + 没 project_root → 标记失败但不抛
        if cls.is_project_level and project_root is None:
            results.append(
                SyncResult(
                    tool_name=name,
                    entry_path=Path("(unresolved)"),
                    success=False,
                    first_sync=False,
                    written=False,
                    error="项目级工具需 --project-root",
                )
            )
            continue

        adapter = cls(project_root=project_root, home=home)
        managed = adapter.render(prefs, gs)
        results.append(adapter.apply(managed, dry_run=dry_run))

    return results


__all__ = [
    "ALL_ADAPTERS",
    "AiderAdapter",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "CursorAdapter",
    "END_MARKER",
    "Goal",
    "ManagedSectionError",
    "MarkerPair",
    "OpenCodeAdapter",
    "PROJECT_LEVEL_TOOLS",
    "Preference",
    "START_MARKER",
    "SyncResult",
    "ToolSyncAdapter",
    "USER_LEVEL_TOOLS",
    "YAML_END_MARKER",
    "YAML_START_MARKER",
    "extract_managed_section",
    "get_adapter",
    "insert_or_replace",
    "sync_all",
    "sync_one_tool",
]
