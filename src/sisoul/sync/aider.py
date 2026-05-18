"""Aider adapter: <project>/.aider.conf.yml.

quirk: aider 用 YAML config, 不像别工具用 markdown system prompt.
偏好往哪塞? aider 没原生 "rules" 字段, 我们用 `read` 字段塞一个 markdown
文件路径 (`.aider/sisoul-rules.md`), 或更简单 — 用 yaml 注释做 marker
(yaml parser 不报错), 在文件里加 `# sisoul-managed-start` ... 段.

本 v1 选择: 在 .aider.conf.yml 加 yaml-comment marker 段 (兼容 yaml parser),
但实际偏好以 yaml dict 形式写, 也保留人类可读. aider 不读不影响 (yaml 注释段).

TODO Phase 2: 加 .aider/sisoul-rules.md + `read:` 自动加列.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from sisoul.sync.base import Goal, Preference, ToolSyncAdapter
from sisoul.sync.managed_section import MarkerPair


class AiderAdapter(ToolSyncAdapter):
    tool_name = "aider"
    is_project_level = True
    markers = MarkerPair.yaml()  # yaml 注释 marker

    def entry_file_path(self) -> Path:
        return self._resolve_project_path(".aider.conf.yml")

    def render(
        self,
        preferences: Iterable[Preference],
        goals: Iterable[Goal],
    ) -> str:
        # 用 yaml 注释行 (#) 表示偏好, aider parser 看到 yaml 注释自动跳过.
        # 人类读得懂, 同时 sisoul 自己 round-trip 也方便.
        prefs = list(preferences)
        gs = list(goals)
        lines: list[str] = [
            "# sisoul vault — auto-managed; user yaml keys outside markers preserved",
        ]

        if prefs:
            lines.append("# preferences:")
            for p in prefs:
                body = p.body.strip().replace("\n", " ")
                lines.append(f"#   - {p.title}: {body}")

        if gs:
            lines.append("# long_term_goals:")
            for g in gs:
                progress = f" ({g.progress})" if g.progress else ""
                lines.append(f"#   - {g.id}: {g.title}{progress}")

        if not prefs and not gs:
            lines.append("# (empty — use `sisoul remember`)")

        return "\n".join(lines).rstrip() + "\n"
