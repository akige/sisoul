"""OpenCode adapter: <project>/.opencode/config.md.

quirk: OpenCode 把 .opencode/config.md 当 system instructions 喂给 agent
(类比 .cursorrules, 但放子目录). markdown 格式, 用 HTML 注释 marker.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from sisoul.sync.base import Goal, Preference, ToolSyncAdapter


class OpenCodeAdapter(ToolSyncAdapter):
    tool_name = "opencode"
    is_project_level = True

    def entry_file_path(self) -> Path:
        return self._resolve_project_path(".opencode", "config.md")

    def render(
        self,
        preferences: Iterable[Preference],
        goals: Iterable[Goal],
    ) -> str:
        prefs = list(preferences)
        gs = list(goals)
        lines: list[str] = [
            "# sisoul vault (OpenCode auto-managed)",
            "",
            "> Synced by sisoul daemon. User edits outside markers preserved.",
            "",
        ]

        lines.append("## Preferences")
        lines.append("")
        if prefs:
            for p in prefs:
                lines.append(f"- **{p.title}**: {p.body.strip()}")
        else:
            lines.append("- (empty)")
        lines.append("")

        lines.append("## Long-term goals")
        lines.append("")
        if gs:
            for g in gs:
                progress = f" — {g.progress}" if g.progress else ""
                lines.append(f"- `{g.id}` **{g.title}**{progress}")
        else:
            lines.append("- (empty)")
        lines.append("")

        return "\n".join(lines).rstrip() + "\n"
