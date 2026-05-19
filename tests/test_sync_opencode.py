"""OpenCodeAdapter 测试 (项目级 .opencode/config.md)."""

from __future__ import annotations

from pathlib import Path

from sisoul.sync.opencode import OpenCodeAdapter
from sisoul.sync.base import Goal, Preference
from sisoul.sync.managed_section import END_MARKER, START_MARKER


def test_entry_path_under_subdir(tmp_path: Path) -> None:
    a = OpenCodeAdapter(project_root=tmp_path)
    assert a.entry_file_path() == tmp_path / ".opencode" / "config.md"


def test_render(tmp_path: Path) -> None:
    a = OpenCodeAdapter(project_root=tmp_path)
    out = a.render(
        [Preference(title="P", body="B")],
        [Goal(id="g1", title="G", progress="50%")],
    )
    assert "P" in out and "B" in out
    assert "g1" in out and "G" in out and "50%" in out


def test_apply_creates_subdir(tmp_path: Path) -> None:
    a = OpenCodeAdapter(project_root=tmp_path)
    result = a.apply(a.render([], []))
    assert result.success
    entry = tmp_path / ".opencode" / "config.md"
    assert entry.exists()
    text = entry.read_text()
    assert START_MARKER in text and END_MARKER in text


def test_apply_increment(tmp_path: Path) -> None:
    entry = tmp_path / ".opencode" / "config.md"
    entry.parent.mkdir(parents=True)
    entry.write_text(
        "# my opencode config\n\n"
        "## user section\n\n"
        f"{START_MARKER}\nOLD\n{END_MARKER}\n"
    )
    a = OpenCodeAdapter(project_root=tmp_path)
    result = a.apply("NEW MANAGED")
    assert result.success
    text = entry.read_text()
    assert "user section" in text
    assert "OLD" not in text
    assert "NEW MANAGED" in text
