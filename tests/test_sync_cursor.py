"""CursorAdapter 测试 (项目级 .cursorrules; plain text 格式)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sisoul.sync.cursor import CursorAdapter
from sisoul.sync.base import Goal, Preference
from sisoul.sync.managed_section import END_MARKER, START_MARKER


def test_entry_path_in_project(tmp_path: Path) -> None:
    a = CursorAdapter(project_root=tmp_path)
    assert a.entry_file_path() == tmp_path / ".cursorrules"


def test_requires_project_root() -> None:
    a = CursorAdapter()
    with pytest.raises(ValueError):
        a.entry_file_path()


def test_render_one_line_per_rule(tmp_path: Path) -> None:
    """quirk: Cursor 习惯一行一条 rule. 多行偏好折成一行."""
    a = CursorAdapter(project_root=tmp_path)
    pref = Preference(
        title="Style",
        body="don't use semicolons\nprefer arrow functions",
    )
    out = a.render([pref], [])
    # body 内换行折成空格
    assert "don't use semicolons prefer arrow functions" in out


def test_apply_first_sync_plain_text(tmp_path: Path) -> None:
    a = CursorAdapter(project_root=tmp_path)
    managed = a.render(
        [Preference(title="T", body="B")],
        [Goal(id="g1", title="G", progress="")],
    )
    result = a.apply(managed)
    assert result.success
    assert result.first_sync
    text = (tmp_path / ".cursorrules").read_text()
    assert START_MARKER in text
    assert "T: B" in text
    assert "[g1] G" in text


def test_apply_increment_preserves_user_rules(tmp_path: Path) -> None:
    entry = tmp_path / ".cursorrules"
    entry.write_text(
        "- always use TypeScript strict mode\n"
        "- always run pytest before commit\n\n"
        f"{START_MARKER}\nOLD\n{END_MARKER}\n"
    )
    a = CursorAdapter(project_root=tmp_path)
    result = a.apply("NEW")
    assert result.success
    text = entry.read_text()
    assert "always use TypeScript strict mode" in text
    assert "always run pytest before commit" in text
    assert "OLD" not in text
    assert "NEW" in text
