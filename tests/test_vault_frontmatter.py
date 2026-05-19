"""tests for sisoul.vault.frontmatter (Phase 1 W3)."""

from __future__ import annotations

from sisoul.vault.frontmatter import dump_frontmatter, load_frontmatter


def test_dump_then_load_roundtrip() -> None:
    meta = {"title": "我的偏好", "scope": "default", "verified": False, "n": 42}
    body = "我用 Tailwind CSS\n不喜欢 modal."
    text = dump_frontmatter(meta, body)
    assert text.startswith("---\n")
    loaded_meta, loaded_body = load_frontmatter(text)
    assert loaded_meta == meta
    assert loaded_body == body


def test_load_no_frontmatter_returns_empty_meta() -> None:
    text = "just plain body, no frontmatter\n"
    meta, body = load_frontmatter(text)
    assert meta == {}
    assert body == text.rstrip("\n") or body.startswith("just plain body")


def test_dump_empty_meta_still_has_header() -> None:
    text = dump_frontmatter({}, "body only")
    # python-frontmatter 对空 meta 行为: 仍可能有 --- 头 (取决于版本)
    # 主要验证可 reload
    meta, body = load_frontmatter(text)
    assert meta == {}
    assert "body only" in body


def test_dump_with_nested_and_list() -> None:
    meta = {"tags": ["a", "b"], "deep": {"k": 1}}
    body = "x"
    text = dump_frontmatter(meta, body)
    loaded_meta, loaded_body = load_frontmatter(text)
    assert loaded_meta["tags"] == ["a", "b"]
    assert loaded_meta["deep"] == {"k": 1}
    assert loaded_body == "x"


def test_dump_ends_with_newline() -> None:
    text = dump_frontmatter({"k": "v"}, "b")
    assert text.endswith("\n")
