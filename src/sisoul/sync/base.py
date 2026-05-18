"""ToolSyncAdapter abstract base class.

5 工具适配器全部继承本类, 重写 entry_file_path / render / 可选 apply.
Adapter 不持有 vault, 只接收 preferences + goals 渲染.
"""

from __future__ import annotations

import difflib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sisoul.sync.managed_section import (
    ManagedSectionError,
    MarkerPair,
    insert_or_replace,
)


@dataclass(frozen=True)
class Preference:
    """单条偏好 (vault preferences/ 下一段)."""

    title: str
    body: str  # markdown / plain text body


@dataclass(frozen=True)
class Goal:
    """单条长期目标 (vault goals/ 下一段)."""

    id: str
    title: str
    progress: str = ""  # 进度描述, 可空


@dataclass(frozen=True)
class SyncResult:
    """sync 单工具结果."""

    tool_name: str
    entry_path: Path
    success: bool
    first_sync: bool  # True = 新建 sisoul-managed 段, False = 增量替换
    written: bool  # dry-run = False, apply = True
    diff: str = ""  # unified diff, dry-run 用
    error: str = ""


class ToolSyncAdapter(ABC):
    """5 工具 sync adapter base."""

    #: 工具名 (claude_code / codex / cursor / aider / opencode)
    tool_name: str = ""

    #: True = 项目级文件 (.cursorrules / .aider.conf.yml / .opencode/config.md), 需 project_root
    #: False = 用户级文件 (~/.claude/CLAUDE.md / ~/.codex/AGENTS.md)
    is_project_level: bool = False

    #: 段标记 (default markdown HTML 注释; yaml adapter override)
    markers: MarkerPair = MarkerPair.default()

    def __init__(
        self,
        *,
        project_root: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self._project_root = project_root
        self._home = home or Path.home()

    # ---------- 子类必须实现 ----------

    @abstractmethod
    def entry_file_path(self) -> Path:
        """各工具入口文件路径 (绝对路径)."""

    @abstractmethod
    def render(
        self,
        preferences: Iterable[Preference],
        goals: Iterable[Goal],
    ) -> str:
        """渲染 sisoul-managed 段内容 (不含 marker 本身)."""

    # ---------- 公共 apply 流程 ----------

    def apply(
        self,
        managed_content: str,
        *,
        dry_run: bool = False,
        create_parent: bool = True,
    ) -> SyncResult:
        """把 managed_content 写到 entry 文件的 sisoul-managed 段."""
        entry_path = self.entry_file_path()

        try:
            if entry_path.exists():
                original = entry_path.read_text(encoding="utf-8")
            else:
                original = ""

            new_content = insert_or_replace(
                original, managed_content, markers=self.markers
            )
            first_sync = (self.markers.start not in original)

            diff = ""
            if dry_run:
                diff = _make_diff(
                    original, new_content, label=str(entry_path)
                )

            if not dry_run:
                if create_parent:
                    entry_path.parent.mkdir(parents=True, exist_ok=True)
                entry_path.write_text(new_content, encoding="utf-8")

            return SyncResult(
                tool_name=self.tool_name,
                entry_path=entry_path,
                success=True,
                first_sync=first_sync,
                written=not dry_run,
                diff=diff,
            )
        except ManagedSectionError as exc:
            return SyncResult(
                tool_name=self.tool_name,
                entry_path=entry_path,
                success=False,
                first_sync=False,
                written=False,
                error=f"managed-section corrupted: {exc}",
            )
        except OSError as exc:
            return SyncResult(
                tool_name=self.tool_name,
                entry_path=entry_path,
                success=False,
                first_sync=False,
                written=False,
                error=f"io error: {exc}",
            )

    # ---------- helpers ----------

    def _resolve_project_path(self, *parts: str) -> Path:
        """项目级文件路径解析. 没传 project_root → ValueError."""
        if self._project_root is None:
            raise ValueError(
                f"{self.tool_name} 是项目级工具, 必须传 project_root"
            )
        p = Path(self._project_root)
        for part in parts:
            p = p / part
        return p


def _make_diff(old: str, new: str, *, label: str) -> str:
    """unified diff (3 行上下文)."""
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"{label} (current)",
            tofile=f"{label} (after sync)",
            n=3,
        )
    )
