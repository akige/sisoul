"""ToolSyncAdapter base 行为 + SyncResult 测试.

测试 apply 流程: 首次 / 增量 / corrupted / dry-run / io error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pytest

from sisoul.sync.base import (
    Goal,
    Preference,
    SyncResult,
    ToolSyncAdapter,
)
from sisoul.sync.managed_section import END_MARKER, START_MARKER


class DummyAdapter(ToolSyncAdapter):
    """测试用 adapter, entry 路径外部注入."""

    tool_name = "dummy"
    is_project_level = False

    def __init__(self, entry: Path, **kw) -> None:
        super().__init__(**kw)
        self._entry = entry

    def entry_file_path(self) -> Path:
        return self._entry

    def render(self, preferences, goals) -> str:
        prefs = list(preferences)
        gs = list(goals)
        return f"prefs={len(prefs)} goals={len(gs)}"


class DummyProjectAdapter(ToolSyncAdapter):
    tool_name = "dummy-proj"
    is_project_level = True

    def entry_file_path(self) -> Path:
        return self._resolve_project_path(".dummy.conf")

    def render(self, preferences, goals) -> str:
        return "managed"


def test_apply_first_sync_creates_file(tmp_path: Path) -> None:
    entry = tmp_path / "entry.md"
    adapter = DummyAdapter(entry=entry)
    result = adapter.apply(adapter.render([], []))
    assert result.success
    assert result.first_sync
    assert result.written
    assert entry.exists()
    content = entry.read_text()
    assert START_MARKER in content
    assert END_MARKER in content
    assert "prefs=0 goals=0" in content


def test_apply_increment_replaces_section(tmp_path: Path) -> None:
    entry = tmp_path / "entry.md"
    entry.write_text(
        "# user wrote\n\n"
        f"{START_MARKER}\nOLD\n{END_MARKER}\n\n"
        "# after user\n"
    )
    adapter = DummyAdapter(entry=entry)
    result = adapter.apply("NEW INNER")
    assert result.success
    assert not result.first_sync
    text = entry.read_text()
    assert "# user wrote" in text
    assert "# after user" in text
    assert "OLD" not in text
    assert "NEW INNER" in text


def test_apply_dry_run_no_write(tmp_path: Path) -> None:
    entry = tmp_path / "dry.md"
    adapter = DummyAdapter(entry=entry)
    result = adapter.apply(adapter.render([], []), dry_run=True)
    assert result.success
    assert not result.written
    assert not entry.exists()
    assert result.diff  # 有 diff 输出


def test_apply_corrupted_returns_failed_result(tmp_path: Path) -> None:
    entry = tmp_path / "bad.md"
    entry.write_text(f"{START_MARKER}\nbroken")  # 只 start 没 end
    adapter = DummyAdapter(entry=entry)
    result = adapter.apply("X")
    assert not result.success
    assert "corrupted" in result.error.lower()
    # 文件没被改
    assert entry.read_text() == f"{START_MARKER}\nbroken"


def test_apply_creates_parent_dir(tmp_path: Path) -> None:
    """父 dir 不存在, apply 应自动建."""
    entry = tmp_path / "deep" / "nested" / "config.md"
    adapter = DummyAdapter(entry=entry)
    result = adapter.apply("hello")
    assert result.success
    assert entry.exists()


def test_project_adapter_requires_project_root() -> None:
    adapter = DummyProjectAdapter(project_root=None)
    with pytest.raises(ValueError):
        adapter.entry_file_path()


def test_project_adapter_resolves_under_root(tmp_path: Path) -> None:
    adapter = DummyProjectAdapter(project_root=tmp_path)
    assert adapter.entry_file_path() == tmp_path / ".dummy.conf"


def test_sync_result_dataclass_immutable() -> None:
    r = SyncResult(
        tool_name="x", entry_path=Path("/tmp/x"),
        success=True, first_sync=True, written=True,
    )
    with pytest.raises(Exception):
        r.success = False  # type: ignore


def test_preference_goal_dataclasses() -> None:
    p = Preference(title="t", body="b")
    g = Goal(id="g1", title="goal", progress="50%")
    assert p.title == "t"
    assert g.progress == "50%"
    # frozen
    with pytest.raises(Exception):
        p.title = "x"  # type: ignore
