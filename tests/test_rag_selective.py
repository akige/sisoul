"""tests · rag.selective (Phase 2 P2-2).

覆盖:
- frontmatter sisoul_rag: include / exclude / auto / 缺失 / 坏值
- auto: mtime 近 N 天 / prompt keyword 命中 / 都不满足
- vault 不存在 / 无 .md / 嵌套子目录
- build_context truncate
- inject_context 整合 + daemon endpoint smoke
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.rag import create_router as create_rag_router
from sisoul.rag.selective import (
    DEFAULT_AUTO_MTIME_DAYS,
    MODE_AUTO,
    MODE_EXCLUDE,
    MODE_INCLUDE,
    build_context,
    filter_files,
    inject_context,
)
from sisoul.vault.frontmatter import dump_frontmatter


# ────────────────────────────────────────────────────────────
# fixture: tmp vault with files of different sisoul_rag modes
# ────────────────────────────────────────────────────────────


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """tmp vault 预置 5 文件 + 嵌套 subdir."""
    root = tmp_path / "vault"
    root.mkdir()
    # include 强制
    (root / "always-on.md").write_text(
        dump_frontmatter(
            {"sisoul_rag": "include", "title": "Always on"},
            "always inject this rule.",
        ),
        encoding="utf-8",
    )
    # exclude 强制 (即使关键词命中也排除)
    (root / "secret.md").write_text(
        dump_frontmatter(
            {"sisoul_rag": "exclude", "title": "Secret note"},
            "deepseek grok docker stuff",
        ),
        encoding="utf-8",
    )
    # auto + 近 mtime + keyword 命中
    (root / "polaris-strategy.md").write_text(
        dump_frontmatter(
            {"sisoul_rag": "auto", "title": "Polaris strategy", "tags": ["quant", "polaris"]},
            "polaris param tune for live trading.",
        ),
        encoding="utf-8",
    )
    # auto + 近 mtime + 无 keyword (prompt 不匹配, 仅靠 mtime)
    (root / "fresh-note.md").write_text(
        dump_frontmatter(
            {"sisoul_rag": "auto", "title": "Fresh random"},
            "random thoughts about coffee.",
        ),
        encoding="utf-8",
    )
    # auto + 老 mtime (会被人为设老)
    old = root / "ancient.md"
    old.write_text(
        dump_frontmatter(
            {"sisoul_rag": "auto", "title": "Ancient note"},
            "polaris old note (keyword hits but mtime old).",
        ),
        encoding="utf-8",
    )
    # 把 mtime 设到 100 天前
    long_ago = time.time() - 100 * 86400
    os.utime(old, (long_ago, long_ago))

    # 嵌套 subdir 也算
    sub = root / "sub"
    sub.mkdir()
    (sub / "nested.md").write_text(
        dump_frontmatter(
            {"sisoul_rag": "auto"},
            "nested polaris content.",
        ),
        encoding="utf-8",
    )
    return root


# ────────────────────────────────────────────────────────────
# 1. frontmatter mode tests
# ────────────────────────────────────────────────────────────


class TestModeInclude:
    def test_include_always_selected_even_no_keyword(self, vault):
        files = filter_files(vault, prompt="")
        names = {p.name for p in files}
        assert "always-on.md" in names

    def test_include_selected_with_any_prompt(self, vault):
        files = filter_files(vault, prompt="totally unrelated query xyz")
        names = {p.name for p in files}
        assert "always-on.md" in names


class TestModeExclude:
    def test_exclude_never_selected_even_keyword_hit(self, vault):
        # prompt 命中 secret.md body 的 "deepseek grok docker"
        files = filter_files(vault, prompt="deepseek grok docker")
        names = {p.name for p in files}
        assert "secret.md" not in names

    def test_exclude_overrides_mtime(self, vault):
        # 强制 exclude, mtime 再新也不选
        files = filter_files(vault, prompt="")
        names = {p.name for p in files}
        assert "secret.md" not in names


class TestModeAuto:
    def test_auto_mtime_old_keyword_hit_still_skipped_when_no_keyword(self, vault):
        # 老文件 (100d) ancient.md, prompt 空 → mtime 失败 + 无 keyword → 不选
        files = filter_files(vault, prompt="", mtime_days=DEFAULT_AUTO_MTIME_DAYS)
        names = {p.name for p in files}
        assert "ancient.md" not in names

    def test_auto_old_mtime_but_keyword_hit_included(self, vault):
        # ancient.md 老但 body 含 polaris, prompt 给 polaris → keyword 命中 → 选
        files = filter_files(vault, prompt="polaris")
        names = {p.name for p in files}
        assert "ancient.md" in names

    def test_auto_fresh_mtime_no_keyword_included(self, vault):
        # fresh-note.md mtime 新, prompt 空 → 仅 mtime 命中 → 选
        files = filter_files(vault, prompt="")
        names = {p.name for p in files}
        assert "fresh-note.md" in names

    def test_auto_nested_subdir_scanned(self, vault):
        files = filter_files(vault, prompt="polaris")
        names = {p.name for p in files}
        assert "nested.md" in names


# ────────────────────────────────────────────────────────────
# 2. frontmatter 缺/坏 reverse cases
# ────────────────────────────────────────────────────────────


class TestFrontmatterBroken:
    def test_no_frontmatter_treated_as_auto(self, tmp_path):
        root = tmp_path / "v"
        root.mkdir()
        f = root / "naked.md"
        f.write_text("just a body, no frontmatter, contains polaris keyword", encoding="utf-8")
        files = filter_files(root, prompt="polaris")
        assert f in files

    def test_invalid_mode_value_treated_as_auto(self, tmp_path):
        root = tmp_path / "v"
        root.mkdir()
        (root / "bad-mode.md").write_text(
            dump_frontmatter(
                {"sisoul_rag": "not-a-valid-mode", "title": "x"},
                "polaris content",
            ),
            encoding="utf-8",
        )
        files = filter_files(root, prompt="polaris")
        assert len(files) == 1  # 当 auto 处理, keyword 命中 → 选

    def test_mode_non_string_treated_as_auto(self, tmp_path):
        root = tmp_path / "v"
        root.mkdir()
        (root / "weird.md").write_text(
            dump_frontmatter(
                {"sisoul_rag": 42, "title": "x"},
                "polaris stuff",
            ),
            encoding="utf-8",
        )
        files = filter_files(root, prompt="polaris")
        assert len(files) == 1


# ────────────────────────────────────────────────────────────
# 3. 反向 case: vault 无文件
# ────────────────────────────────────────────────────────────


class TestEmptyVault:
    def test_vault_not_exist(self, tmp_path):
        files = filter_files(tmp_path / "does-not-exist", prompt="anything")
        assert files == []

    def test_vault_empty_dir(self, tmp_path):
        root = tmp_path / "empty"
        root.mkdir()
        files = filter_files(root, prompt="polaris")
        assert files == []

    def test_vault_not_a_dir(self, tmp_path):
        f = tmp_path / "regular-file.txt"
        f.write_text("not a vault", encoding="utf-8")
        files = filter_files(f, prompt="anything")
        assert files == []


# ────────────────────────────────────────────────────────────
# 4. build_context truncate
# ────────────────────────────────────────────────────────────


class TestBuildContext:
    def test_build_context_empty(self):
        ctx, n = build_context([])
        assert ctx == ""
        assert n == 0

    def test_build_context_includes_separator(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_text("alpha", encoding="utf-8")
        ctx, n = build_context([f])
        assert "alpha" in ctx
        assert "--- a.md ---" in ctx
        assert n > 0

    def test_build_context_truncate(self, tmp_path):
        # 5 个文件 each 1000 chars; max_chars=2000 → 只装得下前 1-2 个
        files = []
        for i in range(5):
            f = tmp_path / f"f{i}.md"
            f.write_text("x" * 1000, encoding="utf-8")
            files.append(f)
        ctx, n = build_context(files, max_chars=2000)
        assert n <= 2000
        assert len(ctx) <= 2000


# ────────────────────────────────────────────────────────────
# 5. inject_context (整合)
# ────────────────────────────────────────────────────────────


class TestInjectContext:
    def test_inject_returns_all_fields(self, vault):
        result = inject_context("polaris", vault)
        assert "selected_files" in result
        assert "context_chars" in result
        assert "filtered_count" in result
        assert "context" in result
        # filtered_count == 总 .md (6 file: always-on/secret/polaris-strategy/fresh-note/ancient/sub/nested)
        assert result["filtered_count"] >= 5
        # 至少包含 always-on (include) + polaris-strategy (auto keyword)
        names = result["selected_files"]
        assert any("always-on" in n for n in names)


# ────────────────────────────────────────────────────────────
# 6. Daemon endpoint
# ────────────────────────────────────────────────────────────


@pytest.fixture()
def rag_client(vault, monkeypatch) -> TestClient:
    monkeypatch.setenv("SISOUL_VAULT_ROOT", str(vault))
    app = FastAPI()
    app.include_router(create_rag_router())
    return TestClient(app)


class TestRagEndpoint:
    def test_inject_endpoint_basic(self, rag_client):
        r = rag_client.post(
            "/sisoul/rag/inject",
            json={"prompt": "polaris", "include_context": False},
        )
        assert r.status_code == 200
        data = r.json()
        assert "selected_files" in data
        assert "filtered_count" in data
        assert data["context_chars"] >= 0
        # include_context=False → context 为空
        assert data["context"] == ""

    def test_inject_endpoint_with_context(self, rag_client):
        r = rag_client.post(
            "/sisoul/rag/inject",
            json={"prompt": "polaris", "include_context": True},
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["context"]) > 0

    def test_inject_endpoint_empty_prompt(self, rag_client):
        r = rag_client.post("/sisoul/rag/inject", json={"prompt": ""})
        assert r.status_code == 200
        data = r.json()
        # always-on (include) + fresh-note (mtime new) 至少 2 个
        assert len(data["selected_files"]) >= 1

    def test_preview_endpoint(self, rag_client):
        r = rag_client.get("/sisoul/rag/preview", params={"prompt": "polaris"})
        assert r.status_code == 200
        data = r.json()
        assert "decisions" in data
        # 必含 include + exclude 各至少一条
        modes = {d["mode"] for d in data["decisions"]}
        assert MODE_INCLUDE in modes
        assert MODE_EXCLUDE in modes
        assert MODE_AUTO in modes
