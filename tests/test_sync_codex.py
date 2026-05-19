"""CodexAdapter 测试 (~/.codex/AGENTS.md)."""

from __future__ import annotations

from pathlib import Path

from sisoul.sync.codex import CodexAdapter
from sisoul.sync.base import Goal, Preference
from sisoul.sync.managed_section import END_MARKER, START_MARKER


def test_entry_path(tmp_path: Path) -> None:
    a = CodexAdapter(home=tmp_path)
    assert a.entry_file_path() == tmp_path / ".codex" / "AGENTS.md"


def test_render_format(tmp_path: Path) -> None:
    a = CodexAdapter(home=tmp_path)
    out = a.render(
        [Preference(title="P", body="B")],
        [Goal(id="g1", title="T", progress="10%")],
    )
    assert "P" in out and "B" in out
    assert "g1" in out and "T" in out and "10%" in out
    assert "Codex CLI" in out


def test_apply_first_sync(tmp_path: Path) -> None:
    a = CodexAdapter(home=tmp_path)
    managed = a.render([Preference(title="X", body="Y")], [])
    result = a.apply(managed)
    assert result.success
    assert result.first_sync
    text = (tmp_path / ".codex" / "AGENTS.md").read_text()
    assert START_MARKER in text and END_MARKER in text


def test_apply_increment_preserves(tmp_path: Path) -> None:
    entry = tmp_path / ".codex" / "AGENTS.md"
    entry.parent.mkdir(parents=True)
    entry.write_text(
        "# my AGENTS.md\n\n"
        f"{START_MARKER}\nOLD\n{END_MARKER}\n\n"
        "## user trailing section\n"
    )
    a = CodexAdapter(home=tmp_path)
    result = a.apply("NEW")
    assert result.success
    assert not result.first_sync
    text = entry.read_text()
    assert "OLD" not in text
    assert "NEW" in text
    assert "user trailing section" in text


def test_dry_run_emits_diff_no_write(tmp_path: Path) -> None:
    a = CodexAdapter(home=tmp_path)
    result = a.apply(a.render([], []), dry_run=True)
    assert result.success
    assert result.diff
    assert not (tmp_path / ".codex" / "AGENTS.md").exists()
