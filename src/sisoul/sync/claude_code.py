"""Claude Code adapter: ~/.claude/CLAUDE.md.

格式: markdown, sisoul-managed 段放在文件末尾, 风格类比用户既有 CLAUDE.md
(短促 + bullet + 段内子节). 用 HTML 注释做 marker (markdown renderer 透明).

项目级 .claude/CLAUDE.md 暂不处理 (TODO Phase 1 W11+).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from sisoul.sync.base import Goal, Preference, ToolSyncAdapter


class ClaudeCodeAdapter(ToolSyncAdapter):
    tool_name = "claude_code"
    is_project_level = False

    def entry_file_path(self) -> Path:
        return self._home / ".claude" / "CLAUDE.md"

    def render(
        self,
        preferences: Iterable[Preference],
        goals: Iterable[Goal],
    ) -> str:
        prefs = list(preferences)
        gs = list(goals)
        lines: list[str] = [
            "## sisoul vault 偏好 (auto-managed)",
            "",
            "> 由 sisoul daemon 同步. 用户手写段在 marker 外, 不会被覆盖.",
            "",
        ]

        if prefs:
            lines.append("### 偏好")
            lines.append("")
            for p in prefs:
                lines.append(f"- **{p.title}**: {p.body.strip()}")
            lines.append("")
        else:
            lines.append("### 偏好")
            lines.append("")
            lines.append("- (空, 用 `sisoul remember <text>` 加偏好)")
            lines.append("")

        if gs:
            lines.append("### 长期目标")
            lines.append("")
            for g in gs:
                progress = f" — {g.progress}" if g.progress else ""
                lines.append(f"- `{g.id}` **{g.title}**{progress}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"
