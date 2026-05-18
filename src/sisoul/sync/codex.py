"""Codex CLI adapter: ~/.codex/AGENTS.md.

格式 / 风格跟 Claude Code 同 markdown, 但顶级标题用 AGENTS 习惯
(对照用户 ~/.codex/AGENTS.md 既有风格).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from sisoul.sync.base import Goal, Preference, ToolSyncAdapter


class CodexAdapter(ToolSyncAdapter):
    tool_name = "codex"
    is_project_level = False

    def entry_file_path(self) -> Path:
        return self._home / ".codex" / "AGENTS.md"

    def render(
        self,
        preferences: Iterable[Preference],
        goals: Iterable[Goal],
    ) -> str:
        prefs = list(preferences)
        gs = list(goals)
        lines: list[str] = [
            "## sisoul vault (auto-managed for Codex CLI)",
            "",
            "> sisoul daemon 同步. 不要手改本段, marker 外随便写.",
            "",
        ]

        lines.append("### Preferences")
        lines.append("")
        if prefs:
            for p in prefs:
                lines.append(f"- **{p.title}**: {p.body.strip()}")
        else:
            lines.append("- (empty)")
        lines.append("")

        lines.append("### Long-term goals")
        lines.append("")
        if gs:
            for g in gs:
                progress = f" — {g.progress}" if g.progress else ""
                lines.append(f"- `{g.id}` **{g.title}**{progress}")
        else:
            lines.append("- (empty)")
        lines.append("")

        return "\n".join(lines).rstrip() + "\n"
