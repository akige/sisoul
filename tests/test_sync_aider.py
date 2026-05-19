"""AiderAdapter 测试 (项目级 .aider.conf.yml; yaml comment marker)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sisoul.sync.aider import AiderAdapter
from sisoul.sync.base import Goal, Preference
from sisoul.sync.managed_section import (
    MarkerPair,
    YAML_END_MARKER,
    YAML_START_MARKER,
    extract_managed_section,
)


def test_entry_path(tmp_path: Path) -> None:
    a = AiderAdapter(project_root=tmp_path)
    assert a.entry_file_path() == tmp_path / ".aider.conf.yml"


def test_uses_yaml_markers(tmp_path: Path) -> None:
    a = AiderAdapter(project_root=tmp_path)
    assert a.markers.start == YAML_START_MARKER
    assert a.markers.end == YAML_END_MARKER


def test_render_only_yaml_comments(tmp_path: Path) -> None:
    """所有非空行都应是 yaml 注释行 (# 开头), 不然 aider yaml parser 会炸."""
    a = AiderAdapter(project_root=tmp_path)
    out = a.render(
        [Preference(title="P", body="B")],
        [Goal(id="g1", title="G", progress="")],
    )
    for line in out.splitlines():
        if line.strip():
            assert line.startswith("#"), f"非注释行 '{line}' 会破坏 aider yaml"


def test_apply_first_sync(tmp_path: Path) -> None:
    a = AiderAdapter(project_root=tmp_path)
    managed = a.render([Preference(title="X", body="Y")], [])
    result = a.apply(managed)
    assert result.success
    assert result.first_sync
    text = (tmp_path / ".aider.conf.yml").read_text()
    assert YAML_START_MARKER in text
    assert YAML_END_MARKER in text


def test_apply_preserves_user_yaml(tmp_path: Path) -> None:
    """关键: 用户 yaml dict 必须保留 (model / edit_format 字段不动)."""
    entry = tmp_path / ".aider.conf.yml"
    entry.write_text(
        "model: gpt-4o\n"
        "edit_format: diff\n"
        "auto_commits: false\n\n"
        f"{YAML_START_MARKER}\n# old\n{YAML_END_MARKER}\n"
    )
    a = AiderAdapter(project_root=tmp_path)
    new_managed = a.render([Preference(title="P", body="B")], [])
    result = a.apply(new_managed)
    assert result.success
    text = entry.read_text()
    assert "model: gpt-4o" in text
    assert "edit_format: diff" in text
    assert "auto_commits: false" in text
    assert "# old" not in text
    assert "P: B" in text


def test_round_trip_extract(tmp_path: Path) -> None:
    a = AiderAdapter(project_root=tmp_path)
    managed = a.render([Preference(title="X", body="Y")], [])
    result = a.apply(managed)
    assert result.success
    text = (tmp_path / ".aider.conf.yml").read_text()
    extracted = extract_managed_section(text, markers=MarkerPair.yaml())
    assert extracted is not None
    assert "X: Y" in extracted


def test_aider_yaml_still_parses(tmp_path: Path) -> None:
    """end-to-end sanity: sisoul 写完的 .aider.conf.yml 仍是合法 yaml."""
    pytest.importorskip("yaml")
    import yaml

    entry = tmp_path / ".aider.conf.yml"
    entry.write_text(
        "model: gpt-4o\nedit_format: diff\n"
    )
    a = AiderAdapter(project_root=tmp_path)
    result = a.apply(a.render([Preference(title="X", body="Y")], []))
    assert result.success
    text = entry.read_text()
    # yaml.safe_load 不报错 (注释段被忽略)
    parsed = yaml.safe_load(text)
    assert parsed.get("model") == "gpt-4o"
    assert parsed.get("edit_format") == "diff"
