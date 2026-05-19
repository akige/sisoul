"""tests/test_cli_export.py — export ZIP + 验证内容 (Phase 1 W13).

覆盖:
- export ZIP 生成 + 内容验证
- 默认路径时间戳格式
- --output 指定路径
- vault 不存在时 exit 1
- dna.json 缺失时 exit 1
- ZIP 内含 README-export.md
- ZIP 内含 vault/ 目录结构
- _should_exclude 过滤规则
- _human_size 格式化
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli import app
from sisoul.cli_commands.export import (
    README_EXPORT_CONTENT,
    README_EXPORT_NAME,
    _human_size,
    _should_exclude,
    run_export,
)
from sisoul.vault import VaultPaths

runner = CliRunner()


# ── fixtures ─────────────────────────────────────────────────

def _make_vault(tmp_path: Path, with_dna: bool = True, extra_files: bool = True) -> VaultPaths:
    """建一个最小化 vault (用于测试)."""
    vault_dir = tmp_path / "vault"
    paths = VaultPaths(root=vault_dir)
    paths.ensure_dirs()

    if with_dna:
        dna = {
            "sisoul_version": "0.1.0-dev",
            "vault_created_at": "2026-05-18T00:00:00+00:00",
            "master_key_hash": "abc123",
            "schema_version": 1,
        }
        paths.dna.write_text(json.dumps(dna, indent=2), encoding="utf-8")

    if extra_files:
        # preferences 文件
        pref = paths.preferences_dir / "2026-05-18.md"
        pref.write_text("---\ndate: 2026-05-18\n---\n用 Tailwind", encoding="utf-8")

        # goals 文件
        goal = paths.goals_dir / "goal-001.md"
        goal.write_text("---\nid: goal-001\n---\n# 目标一", encoding="utf-8")

    return paths


# ── _human_size ───────────────────────────────────────────────

def test_human_size_bytes() -> None:
    assert _human_size(500) == "500 B"


def test_human_size_kb() -> None:
    result = _human_size(2048)
    assert "KB" in result
    assert "2.0" in result


def test_human_size_mb() -> None:
    result = _human_size(3 * 1024 * 1024)
    assert "MB" in result
    assert "3.0" in result


def test_human_size_gb() -> None:
    result = _human_size(2 * 1024 ** 3)
    assert "GB" in result


# ── _should_exclude ───────────────────────────────────────────

def test_exclude_venv() -> None:
    assert _should_exclude(".venv/lib/python3.12/foo.py") is True


def test_exclude_pycache() -> None:
    assert _should_exclude("__pycache__/foo.cpython.pyc") is True


def test_exclude_pyc() -> None:
    assert _should_exclude("preferences/foo.pyc") is True


def test_exclude_git() -> None:
    assert _should_exclude(".git/config") is True


def test_not_exclude_md() -> None:
    assert _should_exclude("preferences/2026-05-18.md") is False


def test_not_exclude_dna() -> None:
    assert _should_exclude("dna.json") is False


# ── run_export ────────────────────────────────────────────────

def test_export_creates_zip(tmp_path: Path) -> None:
    """run_export 生成有效 ZIP 文件."""
    paths = _make_vault(tmp_path)
    out = tmp_path / "out.zip"

    result_path = run_export(output=out, vault_dir=paths.root)

    assert result_path == out
    assert out.exists()
    assert zipfile.is_zipfile(out)


def test_export_zip_contains_dna(tmp_path: Path) -> None:
    """ZIP 内含 vault/dna.json."""
    paths = _make_vault(tmp_path)
    out = tmp_path / "out.zip"
    run_export(output=out, vault_dir=paths.root)

    with zipfile.ZipFile(out, "r") as zf:
        names = zf.namelist()
    assert "vault/dna.json" in names


def test_export_zip_contains_readme(tmp_path: Path) -> None:
    """ZIP 内含 README-export.md."""
    paths = _make_vault(tmp_path)
    out = tmp_path / "out.zip"
    run_export(output=out, vault_dir=paths.root)

    with zipfile.ZipFile(out, "r") as zf:
        names = zf.namelist()
    assert README_EXPORT_NAME in names


def test_export_readme_content(tmp_path: Path) -> None:
    """README-export.md 内容含关键装机指引."""
    paths = _make_vault(tmp_path)
    out = tmp_path / "out.zip"
    run_export(output=out, vault_dir=paths.root)

    with zipfile.ZipFile(out, "r") as zf:
        content = zf.read(README_EXPORT_NAME).decode("utf-8")

    assert "sisoul init --import" in content
    assert "sisoul restore --from-zip" in content


def test_export_zip_contains_preferences(tmp_path: Path) -> None:
    """ZIP 内含 vault/preferences/ 下的文件."""
    paths = _make_vault(tmp_path)
    out = tmp_path / "out.zip"
    run_export(output=out, vault_dir=paths.root)

    with zipfile.ZipFile(out, "r") as zf:
        names = zf.namelist()

    pref_entries = [n for n in names if n.startswith("vault/preferences/")]
    assert len(pref_entries) >= 1


def test_export_file_count(tmp_path: Path) -> None:
    """ZIP 内文件数 >= 3 (dna + preferences + goals + README)."""
    paths = _make_vault(tmp_path)
    out = tmp_path / "out.zip"
    run_export(output=out, vault_dir=paths.root)

    with zipfile.ZipFile(out, "r") as zf:
        count = len(zf.namelist())

    # dna + 1 pref + 1 goal + README = 4
    assert count >= 4


def test_export_no_vault_exits_1(tmp_path: Path) -> None:
    """vault 不存在时 exit code 1."""
    out = tmp_path / "out.zip"
    with pytest.raises(SystemExit) as exc_info:
        run_export(output=out, vault_dir=tmp_path / "nonexistent")
    assert exc_info.value.code == 1


def test_export_missing_dna_exits_1(tmp_path: Path) -> None:
    """dna.json 缺失时 exit code 1."""
    paths = _make_vault(tmp_path, with_dna=False)
    out = tmp_path / "out.zip"
    with pytest.raises(SystemExit) as exc_info:
        run_export(output=out, vault_dir=paths.root)
    assert exc_info.value.code == 1


def test_export_default_path_format(tmp_path: Path) -> None:
    """默认输出路径含时间戳格式 sisoul-export-YYYY-MM-DD-HHMM."""
    paths = _make_vault(tmp_path)

    # 用 CLI runner 跑, 但覆盖 vault_dir
    # 直接调 run_export 不传 output, 得到默认路径
    result_path = run_export(output=None, vault_dir=paths.root)

    # 验证格式 (文件名模式)
    name = result_path.name
    assert name.startswith("sisoul-export-")
    assert name.endswith(".zip")
    # 清理生成的文件
    if result_path.exists():
        result_path.unlink()


def test_export_cli_command_no_vault(tmp_path: Path) -> None:
    """CLI: sisoul export --vault-dir 不存在的路径 → exit 1."""
    result = runner.invoke(
        app,
        ["export", "--vault-dir", str(tmp_path / "nonexistent")],
    )
    assert result.exit_code != 0


def test_export_cli_command_with_vault(tmp_path: Path) -> None:
    """CLI: sisoul export 在有效 vault 上成功."""
    paths = _make_vault(tmp_path)
    out = tmp_path / "cli-out.zip"

    result = runner.invoke(
        app,
        ["export", "--output", str(out), "--vault-dir", str(paths.root)],
    )
    assert result.exit_code == 0
    assert out.exists()
    assert "export 完成" in result.stdout


def test_export_zip_no_venv_entries(tmp_path: Path) -> None:
    """ZIP 内不含 .venv 或 __pycache__ 条目."""
    paths = _make_vault(tmp_path)

    # 故意在 vault 里建 __pycache__ (测试排除)
    pycache = paths.root / "__pycache__"
    pycache.mkdir()
    (pycache / "foo.pyc").write_bytes(b"PK")

    out = tmp_path / "out.zip"
    run_export(output=out, vault_dir=paths.root)

    with zipfile.ZipFile(out, "r") as zf:
        names = zf.namelist()

    assert not any("__pycache__" in n for n in names)
