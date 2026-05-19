"""ClaudeCodeAdapter 测试 (~/.claude/CLAUDE.md 同步)."""

from __future__ import annotations

from pathlib import Path

from sisoul.sync.claude_code import ClaudeCodeAdapter
from sisoul.sync.base import Goal, Preference
from sisoul.sync.managed_section import END_MARKER, START_MARKER


def test_entry_path_under_home(tmp_path: Path) -> None:
    a = ClaudeCodeAdapter(home=tmp_path)
    assert a.entry_file_path() == tmp_path / ".claude" / "CLAUDE.md"


def test_render_contains_preferences(tmp_path: Path) -> None:
    a = ClaudeCodeAdapter(home=tmp_path)
    out = a.render(
        [Preference(title="Tailwind", body="我前端用 Tailwind v4")],
        [],
    )
    assert "Tailwind" in out
    assert "我前端用 Tailwind v4" in out
    assert "## sisoul vault" in out


def test_render_includes_goals(tmp_path: Path) -> None:
    a = ClaudeCodeAdapter(home=tmp_path)
    out = a.render(
        [],
        [Goal(id="g-001", title="完成 sisoul v1", progress="40%")],
    )
    assert "g-001" in out
    assert "完成 sisoul v1" in out
    assert "40%" in out


def test_render_empty_shows_placeholder(tmp_path: Path) -> None:
    a = ClaudeCodeAdapter(home=tmp_path)
    out = a.render([], [])
    assert "sisoul remember" in out  # 提示用户加偏好


def test_apply_first_sync_isolated_home(tmp_path: Path) -> None:
    """关键: 用 tmp_path 当 home, 绝不污染用户真 ~/.claude/CLAUDE.md."""
    a = ClaudeCodeAdapter(home=tmp_path)
    prefs = [Preference(title="X", body="Y")]
    managed = a.render(prefs, [])
    result = a.apply(managed)
    assert result.success
    assert result.first_sync
    entry = tmp_path / ".claude" / "CLAUDE.md"
    assert entry.exists()
    text = entry.read_text()
    assert START_MARKER in text
    assert END_MARKER in text
    assert "X" in text and "Y" in text


def test_apply_preserves_user_section(tmp_path: Path) -> None:
    """已有用户手写 CLAUDE.md, sync 后用户段保留."""
    entry = tmp_path / ".claude" / "CLAUDE.md"
    entry.parent.mkdir(parents=True)
    user_content = "# 我的 CLAUDE.md\n\n## 顶规\n\n- 中文回复\n- 不动 ssh\n"
    entry.write_text(user_content)

    a = ClaudeCodeAdapter(home=tmp_path)
    managed = a.render([Preference(title="P1", body="B1")], [])
    result = a.apply(managed)
    assert result.success
    text = entry.read_text()
    # 用户原有 markdown 完整保留
    assert "# 我的 CLAUDE.md" in text
    assert "- 中文回复" in text
    assert "- 不动 ssh" in text
    # 新加的 sisoul-managed 段
    assert "P1" in text and "B1" in text
