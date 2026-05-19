"""CLI `sisoul sync` 集成测试 (tmp_path 隔离, 不写真实 home)."""

from __future__ import annotations

from pathlib import Path

import typer
from typer.testing import CliRunner

from sisoul.cli_commands.sync import sync_app
from sisoul.sync import ALL_ADAPTERS
from sisoul.sync.managed_section import START_MARKER

runner = CliRunner()


def _make_vault(root: Path) -> None:
    """建一个 mini vault: 1 preference + 1 goal."""
    (root / "preferences").mkdir(parents=True, exist_ok=True)
    (root / "goals").mkdir(parents=True, exist_ok=True)
    (root / "preferences" / "2026-05-18.md").write_text(
        "# Tailwind\n\n我前端用 Tailwind v4\n"
    )
    (root / "goals" / "g-001.md").write_text(
        "# 完成 sisoul v1.0\n\nM1-M5 全部 ship\n"
    )


def test_sync_dry_run_all_with_project_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    vault = home / ".sisoul"
    home.mkdir()
    proj.mkdir()
    _make_vault(vault)

    result = runner.invoke(
        sync_app,
        [
            "--dry-run",
            "--vault-root", str(vault),
            "--home", str(home),
            "--project-root", str(proj),
        ],
    )
    assert result.exit_code == 0, result.output
    # 5 工具全部出现在输出
    for name in ALL_ADAPTERS.keys():
        assert name in result.output, f"工具 {name} 没在输出: {result.output}"
    # dry-run 不写任何文件
    assert not (home / ".claude" / "CLAUDE.md").exists()
    assert not (home / ".codex" / "AGENTS.md").exists()
    assert not (proj / ".cursorrules").exists()


def test_sync_apply_writes_all_5_tools(tmp_path: Path) -> None:
    home = tmp_path / "home"
    proj = tmp_path / "proj"
    vault = home / ".sisoul"
    home.mkdir()
    proj.mkdir()
    _make_vault(vault)

    result = runner.invoke(
        sync_app,
        [
            "--vault-root", str(vault),
            "--home", str(home),
            "--project-root", str(proj),
        ],
    )
    assert result.exit_code == 0, result.output

    # 5 入口文件全部建出
    entries = {
        "claude_code": home / ".claude" / "CLAUDE.md",
        "codex": home / ".codex" / "AGENTS.md",
        "cursor": proj / ".cursorrules",
        "aider": proj / ".aider.conf.yml",
        "opencode": proj / ".opencode" / "config.md",
    }
    for name, p in entries.items():
        assert p.exists(), f"{name} 入口 {p} 没建出"
        text = p.read_text()
        # 都含 marker + 偏好内容
        assert "Tailwind" in text, f"{name} 没含 Tailwind 偏好"


def test_sync_single_tool(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    vault = home / ".sisoul"
    _make_vault(vault)

    result = runner.invoke(
        sync_app,
        [
            "--tool", "claude_code",
            "--vault-root", str(vault),
            "--home", str(home),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (home / ".claude" / "CLAUDE.md").exists()
    # 其他工具没写
    assert not (home / ".codex" / "AGENTS.md").exists()


def test_sync_project_tool_without_root_fails(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    vault = home / ".sisoul"
    _make_vault(vault)

    result = runner.invoke(
        sync_app,
        [
            "--tool", "cursor",  # 项目级但没传 --project-root
            "--vault-root", str(vault),
            "--home", str(home),
        ],
    )
    # cursor.entry_file_path() 会 ValueError → adapter.apply 抛
    # 因为我们没在 sync_one_tool 里捕 ValueError, 应该 exit 非 0
    assert result.exit_code != 0


def test_sync_unknown_tool_errors(tmp_path: Path) -> None:
    result = runner.invoke(sync_app, ["--tool", "nonexistent"])
    assert result.exit_code != 0
    assert "未知" in result.output or "nonexistent" in result.output


def test_sync_dry_run_and_apply_mutex(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    vault = home / ".sisoul"
    _make_vault(vault)
    result = runner.invoke(
        sync_app,
        ["--dry-run", "--apply", "--home", str(home), "--vault-root", str(vault)],
    )
    assert result.exit_code != 0


def test_sync_increment_keeps_user_section(tmp_path: Path) -> None:
    """模拟用户先有 ~/.claude/CLAUDE.md, sync 后用户段保留."""
    home = tmp_path / "home"
    home.mkdir()
    vault = home / ".sisoul"
    _make_vault(vault)
    user_claude = home / ".claude" / "CLAUDE.md"
    user_claude.parent.mkdir(parents=True)
    user_claude.write_text(
        "# 我的 CLAUDE.md\n\n## 顶规\n\n- 顶级硬规则 1\n- 顶级硬规则 2\n"
    )

    result = runner.invoke(
        sync_app,
        [
            "--tool", "claude_code",
            "--vault-root", str(vault),
            "--home", str(home),
        ],
    )
    assert result.exit_code == 0, result.output
    text = user_claude.read_text()
    assert "# 我的 CLAUDE.md" in text
    assert "- 顶级硬规则 1" in text
    assert "- 顶级硬规则 2" in text
    assert START_MARKER in text
    assert "Tailwind" in text  # vault 偏好同步进来


def test_sync_corrupted_managed_section_aborts(tmp_path: Path) -> None:
    """已有 corrupted marker → sync 报错 + 不写."""
    home = tmp_path / "home"
    home.mkdir()
    vault = home / ".sisoul"
    _make_vault(vault)
    user_claude = home / ".claude" / "CLAUDE.md"
    user_claude.parent.mkdir(parents=True)
    user_claude.write_text(f"{START_MARKER}\nbroken no end marker\n")

    result = runner.invoke(
        sync_app,
        [
            "--tool", "claude_code",
            "--vault-root", str(vault),
            "--home", str(home),
        ],
    )
    assert result.exit_code != 0
    # 原文件没被破坏 (仍是 corrupted 原状)
    text = user_claude.read_text()
    assert "broken no end marker" in text
