"""Cursor adapter: <project>/.cursorrules.

quirk: .cursorrules 不是 yaml / json, 是 plain text (Cursor 把整个文件喂给 LLM
作为 system prompt). 我们用 HTML 注释做 marker (Cursor 把 HTML 注释当 plain text
传给 LLM 也无害, LLM 会忽略). 一行一条规则风格.

TODO Phase 1 W11+: Cursor 新版支持 `.cursor/rules/*.mdc`, 之后加 detect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from sisoul.sync.base import Goal, Preference, ToolSyncAdapter


class CursorAdapter(ToolSyncAdapter):
    tool_name = "cursor"
    is_project_level = True

    def entry_file_path(self) -> Path:
        return self._resolve_project_path(".cursorrules")

    def render(
        self,
        preferences: Iterable[Preference],
        goals: Iterable[Goal],
    ) -> str:
        prefs = list(preferences)
        gs = list(goals)
        lines: list[str] = [
            "# sisoul vault rules (auto-managed; do not edit between markers)",
        ]

        if prefs:
            lines.append("")
            lines.append("## Preferences")
            for p in prefs:
                # 一行一条 rule, title 当锚
                body = p.body.strip().replace("\n", " ")
                lines.append(f"- {p.title}: {body}")

        if gs:
            lines.append("")
            lines.append("## Long-term goals")
            for g in gs:
                progress = f" ({g.progress})" if g.progress else ""
                lines.append(f"- [{g.id}] {g.title}{progress}")

        if not prefs and not gs:
            lines.append("")
            lines.append("- (no preferences yet; use `sisoul remember`)")

        return "\n".join(lines).rstrip() + "\n"
