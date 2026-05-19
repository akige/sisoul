"""波 2 qa-E · e2e 集成测试 (跨 dev 模块联调).

用 tmp_path + env HOME 隔离, 不污染真 ~/.sisoul / ~/.claude / ~/.codex.

跑: pytest qa/test_e2e_integration.py -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest


SISOUL_BIN = shutil.which("sisoul") or str(
    Path(__file__).parent.parent / ".venv" / "bin" / "sisoul"
)


def _run(cmd: list[str], env: dict, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)


@pytest.fixture
def isolated_env(tmp_path):
    """返回 (env, paths dict) — HOME / vault / project_root 全隔离."""
    home = tmp_path / "home"
    home.mkdir()
    paths = {
        "home": home,
        "vault": home / ".sisoul",
        "project": tmp_path / "project",
        "export_zip": tmp_path / "export.zip",
        "restore_vault": tmp_path / "restored-vault",
    }
    paths["project"].mkdir()

    env = {
        **os.environ,
        "HOME": str(home),
        "ANTHROPIC_API_KEY": "test-key-mock",
        # 防止 hook 干扰 (e.g. pre_sync_destructive)
        "ALLOW_CHANGELOG_PENDING": "1",
    }
    return env, paths


def test_e2e_full_user_journey(isolated_env):
    """Day-1 端到端: init → login(mock) → remember×5 → sync (tmp) → export → restore → verify."""
    env, paths = isolated_env

    # 1. init
    r = _run(
        [
            SISOUL_BIN,
            "init",
            "--vault-dir",
            str(paths["vault"]),
            "--goals",
            "做 $10k MRR,学 Rust,写小说",
        ],
        env,
    )
    assert r.returncode == 0, f"init failed: stderr={r.stderr} stdout={r.stdout}"
    assert paths["vault"].exists()
    assert (paths["vault"] / "dna.json").exists()
    assert (paths["vault"] / "goals").exists()
    assert (paths["vault"] / "preferences").exists()
    # 3 个目标 = 3 个 goal-NNN.md
    goal_files = list((paths["vault"] / "goals").glob("goal-*.md"))
    assert len(goal_files) == 3, f"expected 3 goals, got {len(goal_files)}: {goal_files}"

    # 2. login (skip-verify, 不真打 anthropic API)
    r = _run(
        [
            SISOUL_BIN,
            "login",
            "--provider",
            "ollama",  # ollama 不需 key 验证最稳
            "--skip-verify",
        ],
        env,
    )
    # login 把 config 存到 ~/.sisoul/config.yaml (HOME 隔离已生效)
    # 接受 returncode 0 (成功) 或 1 (verify failed but config saved)
    assert r.returncode in (0, 1), f"login unexpected exit: {r.returncode}, stderr={r.stderr}"

    # 3. remember 5 偏好
    prefs = [
        "用 Tailwind v4",
        "数据库选 SQLite",
        "部署 CF Workers",
        "测试框架 pytest",
        "linter 用 ruff",
    ]
    for pref in prefs:
        r = _run(
            [SISOUL_BIN, "remember", pref, "--vault-dir", str(paths["vault"])],
            env,
            timeout=10,
        )
        assert r.returncode == 0, f"remember '{pref}' failed: {r.stderr}"

    # 验证 preferences 真写入 (同日累积到 1 文件)
    pref_files = list((paths["vault"] / "preferences").glob("*.md"))
    assert len(pref_files) >= 1, f"no preferences file created: {pref_files}"
    pref_content = pref_files[0].read_text(encoding="utf-8")
    # 5 个偏好都应该在文件里
    for pref in prefs:
        assert pref in pref_content, f"pref '{pref}' not in file content"

    # 4. sync 5 工具到 tmp project_root + tmp HOME (不污染真 ~/.claude)
    r = _run(
        [
            SISOUL_BIN,
            "sync",
            "--apply",
            "--project-root",
            str(paths["project"]),
            "--home",
            str(paths["home"]),
            "--vault-root",
            str(paths["vault"]),
        ],
        env,
    )
    # sync_all 任一工具失败 → exit 非 0, 但应该全 OK (tmp 全新)
    assert r.returncode == 0, f"sync failed: stderr={r.stderr} stdout={r.stdout}"

    # 验证 5 工具入口真写出 sisoul-managed 段
    claude_entry = paths["home"] / ".claude" / "CLAUDE.md"
    codex_entry = paths["home"] / ".codex" / "AGENTS.md"
    cursor_entry = paths["project"] / ".cursorrules"
    aider_entry = paths["project"] / ".aider.conf.yml"
    opencode_entry = paths["project"] / ".opencode" / "config.md"

    for entry in [claude_entry, codex_entry, cursor_entry, aider_entry, opencode_entry]:
        assert entry.exists(), f"sync did not create entry: {entry}"
        text = entry.read_text(encoding="utf-8")
        assert (
            "sisoul-managed-start" in text
        ), f"sisoul marker missing in {entry}, content: {text[:200]}"
        assert "sisoul-managed-end" in text, f"sisoul-managed-end missing in {entry}"

    # 至少有一个 entry 内含偏好关键词 (claude_code markdown 最易渲染)
    claude_text = claude_entry.read_text(encoding="utf-8")
    # 偏好应至少有一条嵌入
    assert any(p in claude_text for p in prefs) or any(
        kw in claude_text.lower() for kw in ["tailwind", "sqlite", "pytest", "ruff"]
    ), f"no preference content found in claude entry: {claude_text[:500]}"

    # 5. export ZIP
    r = _run(
        [
            SISOUL_BIN,
            "export",
            "--output",
            str(paths["export_zip"]),
            "--vault-dir",
            str(paths["vault"]),
        ],
        env,
    )
    assert r.returncode == 0, f"export failed: {r.stderr}"
    assert paths["export_zip"].exists()
    zip_size = paths["export_zip"].stat().st_size
    assert zip_size > 500, f"export zip too small: {zip_size} bytes"

    # 验证 ZIP 内容有 dna + preferences + goals
    with zipfile.ZipFile(paths["export_zip"]) as z:
        names = z.namelist()
        assert any("dna.json" in n for n in names), f"dna.json not in zip: {names}"
        assert any(
            "preferences" in n and n.endswith(".md") for n in names
        ), f"preferences/*.md not in zip: {names}"
        assert any(
            "goals" in n and n.endswith(".md") for n in names
        ), f"goals/*.md not in zip: {names}"

    # 6. restore 到新 vault dir
    r = _run(
        [
            SISOUL_BIN,
            "restore",
            "--from-zip",
            str(paths["export_zip"]),
            "--vault-dir",
            str(paths["restore_vault"]),
            "--force",
        ],
        env,
    )
    assert r.returncode == 0, f"restore failed: {r.stderr} {r.stdout}"
    assert paths["restore_vault"].exists()
    assert (paths["restore_vault"] / "dna.json").exists()

    # 验证 preferences + goals 全恢复
    restored_prefs = list((paths["restore_vault"] / "preferences").glob("*.md"))
    restored_goals = list((paths["restore_vault"] / "goals").glob("goal-*.md"))
    assert len(restored_prefs) >= 1, f"no preferences restored: {restored_prefs}"
    assert len(restored_goals) == 3, f"expected 3 goals restored, got {len(restored_goals)}"

    # 验证恢复后内容跟原 vault 一致 (dna.json 比对)
    orig_dna = json.loads((paths["vault"] / "dna.json").read_text())
    restored_dna = json.loads((paths["restore_vault"] / "dna.json").read_text())
    # 注意: dna 可能含 timestamp, 至少业务字段 (e.g. did / handle) 一致
    # 简单验证: 长度 / key set 大致一致
    assert set(orig_dna.keys()) == set(
        restored_dna.keys()
    ), f"dna keys mismatch: orig={orig_dna.keys()} restored={restored_dna.keys()}"


def test_e2e_status_command(isolated_env):
    """status 命令: init 后跑 status, 应输出 markdown 表 (vault 大小 + 目标数)."""
    env, paths = isolated_env

    _run(
        [
            SISOUL_BIN,
            "init",
            "--vault-dir",
            str(paths["vault"]),
            "--goals",
            "g1,g2",
        ],
        env,
    )
    r = _run([SISOUL_BIN, "status", "--vault-dir", str(paths["vault"])], env)
    assert r.returncode == 0, f"status failed: {r.stderr}"
    # 关键字段验证
    assert "vault" in r.stdout.lower() or "vault" in r.stderr.lower(), f"no vault info: {r.stdout}"


def test_e2e_goals_subcommands(isolated_env):
    """goals add/list/progress 子命令端到端."""
    env, paths = isolated_env

    _run(
        [
            SISOUL_BIN,
            "init",
            "--vault-dir",
            str(paths["vault"]),
            "--goals",
            "g1",
        ],
        env,
    )

    # add
    r = _run(
        [SISOUL_BIN, "goals", "add", "新目标", "--vault-dir", str(paths["vault"])],
        env,
    )
    assert r.returncode == 0, f"goals add failed: {r.stderr}"

    # list
    r = _run([SISOUL_BIN, "goals", "list", "--vault-dir", str(paths["vault"])], env)
    assert r.returncode == 0, f"goals list failed: {r.stderr}"
    assert "g1" in r.stdout or "g1" in r.stderr, f"goal g1 not in list: {r.stdout}"
    assert "新目标" in r.stdout or "新目标" in r.stderr, f"新目标 not in list: {r.stdout}"

    # progress (goal-001 应该是 g1)
    r = _run(
        [
            SISOUL_BIN,
            "goals",
            "progress",
            "goal-001",
            "30",
            "--vault-dir",
            str(paths["vault"]),
        ],
        env,
    )
    assert r.returncode == 0, f"goals progress failed: {r.stderr}"
