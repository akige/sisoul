"""sisoul-managed 段插入 / 替换 / 边界 case 测试.

核心: insert_or_replace 必须保留用户手写段, 只覆盖 marker 内部.
"""

from __future__ import annotations

import pytest

from sisoul.sync.managed_section import (
    END_MARKER,
    START_MARKER,
    YAML_END_MARKER,
    YAML_START_MARKER,
    ManagedSectionError,
    MarkerPair,
    extract_managed_section,
    insert_or_replace,
)


class TestExtract:
    def test_no_marker_returns_none(self) -> None:
        assert extract_managed_section("plain text no marker") is None

    def test_extract_inner(self) -> None:
        content = f"{START_MARKER}\nhello\nworld\n{END_MARKER}"
        assert extract_managed_section(content) == "hello\nworld"

    def test_extract_inner_with_surrounding(self) -> None:
        content = (
            "user wrote this above\n\n"
            f"{START_MARKER}\nmanaged inner\n{END_MARKER}\n\n"
            "user wrote this below"
        )
        assert extract_managed_section(content) == "managed inner"

    def test_start_without_end_raises(self) -> None:
        with pytest.raises(ManagedSectionError):
            extract_managed_section(f"{START_MARKER}\nbroken")

    def test_end_without_start_raises(self) -> None:
        with pytest.raises(ManagedSectionError):
            extract_managed_section(f"broken\n{END_MARKER}")

    def test_two_starts_raises(self) -> None:
        with pytest.raises(ManagedSectionError):
            extract_managed_section(
                f"{START_MARKER}\n{END_MARKER}\n{START_MARKER}\n{END_MARKER}"
            )

    def test_reversed_order_raises(self) -> None:
        with pytest.raises(ManagedSectionError):
            extract_managed_section(f"{END_MARKER}\n... \n{START_MARKER}")


class TestInsertOrReplace:
    def test_empty_file_writes_only_managed(self) -> None:
        out = insert_or_replace("", "MANAGED CONTENT")
        assert START_MARKER in out
        assert END_MARKER in out
        assert "MANAGED CONTENT" in out
        # round-trip
        assert extract_managed_section(out) == "MANAGED CONTENT"

    def test_first_sync_appends_to_existing_user_content(self) -> None:
        user = "# my hand-written CLAUDE.md\n\nbody line\n"
        out = insert_or_replace(user, "NEW MANAGED")
        # user 内容保留
        assert "# my hand-written CLAUDE.md" in out
        assert "body line" in out
        # managed 在末尾
        assert out.rstrip().endswith(END_MARKER)
        assert extract_managed_section(out) == "NEW MANAGED"

    def test_replace_existing_managed_keeps_user(self) -> None:
        user_before = "## above user\n\nsomething\n\n"
        user_after = "\n\n## below user\n\nmore\n"
        existing = (
            user_before
            + f"{START_MARKER}\nOLD INNER\n{END_MARKER}"
            + user_after
        )
        out = insert_or_replace(existing, "NEW INNER")
        # 用户手写段保留 (前/后)
        assert "## above user" in out
        assert "## below user" in out
        # OLD 被替换
        assert "OLD INNER" not in out
        assert extract_managed_section(out) == "NEW INNER"
        # marker 数量仍是 1 对
        assert out.count(START_MARKER) == 1
        assert out.count(END_MARKER) == 1

    def test_corrupted_only_start_raises(self) -> None:
        with pytest.raises(ManagedSectionError):
            insert_or_replace(f"{START_MARKER}\nbroken no end", "X")

    def test_corrupted_only_end_raises(self) -> None:
        with pytest.raises(ManagedSectionError):
            insert_or_replace(f"broken\n{END_MARKER}", "X")

    def test_corrupted_two_starts_raises(self) -> None:
        bad = f"{START_MARKER}\na\n{END_MARKER}\n{START_MARKER}\nb\n{END_MARKER}"
        with pytest.raises(ManagedSectionError):
            insert_or_replace(bad, "X")

    def test_yaml_markers_dont_conflict_with_default(self) -> None:
        """yaml marker 段不影响默认 markdown marker 解析."""
        content = (
            "key: value\n"
            f"{YAML_START_MARKER}\nyaml inner\n{YAML_END_MARKER}\n"
        )
        # 用 default markdown markers 解析应当返回 None
        assert extract_managed_section(content) is None
        # 用 yaml markers 解析返回 inner
        assert (
            extract_managed_section(content, markers=MarkerPair.yaml())
            == "yaml inner"
        )

    def test_replace_with_yaml_markers(self) -> None:
        user_yaml = "model: gpt-4o\nedit_format: diff\n\n"
        existing = (
            user_yaml
            + f"{YAML_START_MARKER}\n# old prefs\n{YAML_END_MARKER}\n"
        )
        out = insert_or_replace(
            existing, "# new prefs", markers=MarkerPair.yaml()
        )
        assert "model: gpt-4o" in out
        assert "edit_format: diff" in out
        assert "# old prefs" not in out
        assert "# new prefs" in out
        # 仍然 1 对 marker
        assert out.count(YAML_START_MARKER) == 1
        assert out.count(YAML_END_MARKER) == 1

    def test_idempotent_multi_run(self) -> None:
        """连跑 3 次, 最终内容稳定 (不会层层嵌套)."""
        user = "# my CLAUDE.md\n"
        out1 = insert_or_replace(user, "X1")
        out2 = insert_or_replace(out1, "X2")
        out3 = insert_or_replace(out2, "X3")
        assert out3.count(START_MARKER) == 1
        assert out3.count(END_MARKER) == 1
        assert extract_managed_section(out3) == "X3"
        assert "# my CLAUDE.md" in out3
