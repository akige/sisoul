"""tests/test_cli_restore.py — restore from ZIP + 验证 vault 完整 (Phase 1 W13).

覆盖:
- 正常 restore: ZIP → vault 完整还原
- dna.json 验证 (完整 / 缺字段)
- 已存在 vault 时 --force 行为
- 已存在 vault 无 --force → exit 1
- ZIP 不存在 → exit 1
- 无效 ZIP (非 zip 文件) → exit 1
- ZIP 内无 vault/ 目录 → exit 1
- _validate_dna 边界 case
- CLI 命令集成测试
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli import app
from sisoul.cli_commands.restore import (
    RestoreError,
    _validate_dna,
    run_restore,
)
from sisoul.vault import VaultPaths

runner = CliRunner()


# ── helpers ───────────────────────────────────────────────────

def _make_export_zip(tmp_path: Path, dna: dict | None = None, with_vault_dir: bool = True) -> Path:
    """建一个模拟 sisoul export 生成的 ZIP."""
    if dna is None:
        dna = {
            "sisoul_version": "0.1.0-dev",
            "vault_created_at": "2026-05-18T00:00:00+00:00",
            "master_key_hash": "abc123",
            "schema_version": 1,
        }

    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        if with_vault_dir:
            # vault/dna.json
            zf.writestr("vault/dna.json", json.dumps(dna, indent=2))
            # vault/preferences/2026-05-18.md
            zf.writestr("vault/preferences/2026-05-18.md", "---\ndate: 2026-05-18\n---\n用 Tailwind")
            # vault/goals/goal-001.md
            zf.writestr("vault/goals/goal-001.md", "---\nid: goal-001\n---\n# 目标一")
        # README-export.md (总在顶层)
        zf.writestr("README-export.md", "# export readme")
    return zip_path


# ── _validate_dna ─────────────────────────────────────────────

def test_validate_dna_ok(tmp_path: Path) -> None:
    dna = {"sisoul_version": "0.1.0-dev", "vault_created_at": "2026-05-18T00:00:00+00:00"}
    dna_path = tmp_path / "dna.json"
    dna_path.write_text(json.dumps(dna), encoding="utf-8")
    result = _validate_dna(dna_path)
    assert result["sisoul_version"] == "0.1.0-dev"


def test_validate_dna_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RestoreError, match="dna.json 缺失"):
        _validate_dna(tmp_path / "nonexistent.json")


def test_validate_dna_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "dna.json"
    bad.write_text("not json {{{", encoding="utf-8")
    with pytest.raises(RestoreError, match="解析失败"):
        _validate_dna(bad)


def test_validate_dna_missing_fields(tmp_path: Path) -> None:
    dna = {"sisoul_version": "0.1.0-dev"}  # 缺 vault_created_at
    dna_path = tmp_path / "dna.json"
    dna_path.write_text(json.dumps(dna), encoding="utf-8")
    with pytest.raises(RestoreError, match="缺字段"):
        _validate_dna(dna_path)


def test_validate_dna_empty_json(tmp_path: Path) -> None:
    dna_path = tmp_path / "dna.json"
    dna_path.write_text("{}", encoding="utf-8")
    with pytest.raises(RestoreError, match="缺字段"):
        _validate_dna(dna_path)


# ── run_restore 正常路径 ──────────────────────────────────────

def test_restore_creates_vault(tmp_path: Path) -> None:
    """restore 在新目录建出 vault."""
    zip_path = _make_export_zip(tmp_path)
    vault_dir = tmp_path / "new_vault"

    paths = run_restore(zip_path=zip_path, vault_dir=vault_dir)

    assert paths.root == vault_dir
    assert vault_dir.exists()


def test_restore_dna_present(tmp_path: Path) -> None:
    """restore 后 dna.json 存在且内容正确."""
    zip_path = _make_export_zip(tmp_path)
    vault_dir = tmp_path / "vault"

    paths = run_restore(zip_path=zip_path, vault_dir=vault_dir)

    assert paths.dna.exists()
    dna = json.loads(paths.dna.read_text("utf-8"))
    assert dna["sisoul_version"] == "0.1.0-dev"
    assert dna["vault_created_at"] == "2026-05-18T00:00:00+00:00"


def test_restore_preferences_present(tmp_path: Path) -> None:
    """restore 后 preferences/ 下有文件."""
    zip_path = _make_export_zip(tmp_path)
    vault_dir = tmp_path / "vault"
    paths = run_restore(zip_path=zip_path, vault_dir=vault_dir)

    pref_files = list(paths.preferences_dir.glob("*.md"))
    assert len(pref_files) >= 1


def test_restore_goals_present(tmp_path: Path) -> None:
    """restore 后 goals/ 下有文件."""
    zip_path = _make_export_zip(tmp_path)
    vault_dir = tmp_path / "vault"
    paths = run_restore(zip_path=zip_path, vault_dir=vault_dir)

    goal_files = list(paths.goals_dir.glob("*.md"))
    assert len(goal_files) >= 1


def test_restore_returns_vault_paths(tmp_path: Path) -> None:
    """run_restore 返回 VaultPaths 对象."""
    zip_path = _make_export_zip(tmp_path)
    vault_dir = tmp_path / "vault"
    paths = run_restore(zip_path=zip_path, vault_dir=vault_dir)
    assert isinstance(paths, VaultPaths)


# ── run_restore 错误路径 ──────────────────────────────────────

def test_restore_nonexistent_zip(tmp_path: Path) -> None:
    """ZIP 不存在 → exit 1."""
    with pytest.raises(SystemExit) as exc_info:
        run_restore(zip_path=tmp_path / "ghost.zip", vault_dir=tmp_path / "vault")
    assert exc_info.value.code == 1


def test_restore_invalid_zip(tmp_path: Path) -> None:
    """非 ZIP 文件 → exit 1."""
    not_a_zip = tmp_path / "fake.zip"
    not_a_zip.write_bytes(b"this is not a zip file at all")
    with pytest.raises(SystemExit) as exc_info:
        run_restore(zip_path=not_a_zip, vault_dir=tmp_path / "vault")
    assert exc_info.value.code == 1


def test_restore_zip_without_vault_dir(tmp_path: Path) -> None:
    """ZIP 内无 vault/ 目录 → exit 1."""
    zip_path = _make_export_zip(tmp_path, with_vault_dir=False)
    with pytest.raises(SystemExit) as exc_info:
        run_restore(zip_path=zip_path, vault_dir=tmp_path / "vault")
    assert exc_info.value.code == 1


def test_restore_existing_vault_no_force(tmp_path: Path) -> None:
    """vault 已存在且无 --force → exit 1."""
    zip_path = _make_export_zip(tmp_path)
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "dummy.txt").write_text("existing file")

    with pytest.raises(SystemExit) as exc_info:
        run_restore(zip_path=zip_path, vault_dir=vault_dir, force=False)
    assert exc_info.value.code == 1


def test_restore_existing_vault_with_force(tmp_path: Path) -> None:
    """vault 已存在 + --force → 成功覆盖."""
    zip_path = _make_export_zip(tmp_path)
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "dummy.txt").write_text("existing file")

    # 不应该抛异常
    paths = run_restore(zip_path=zip_path, vault_dir=vault_dir, force=True)
    assert paths.dna.exists()


def test_restore_bad_dna_in_zip(tmp_path: Path) -> None:
    """ZIP 里 dna.json 缺字段 → exit 1."""
    bad_dna = {"sisoul_version": "0.1.0-dev"}  # 缺 vault_created_at
    zip_path = _make_export_zip(tmp_path, dna=bad_dna)

    with pytest.raises(SystemExit) as exc_info:
        run_restore(zip_path=zip_path, vault_dir=tmp_path / "vault")
    assert exc_info.value.code == 1


# ── CLI 命令集成测试 ──────────────────────────────────────────

def test_cli_restore_from_zip(tmp_path: Path) -> None:
    """CLI: sisoul restore --from-zip <path> 正常跑通."""
    zip_path = _make_export_zip(tmp_path)
    vault_dir = tmp_path / "vault"

    result = runner.invoke(
        app,
        ["restore", "--from-zip", str(zip_path), "--vault-dir", str(vault_dir)],
    )
    assert result.exit_code == 0
    assert "restored from ZIP" in result.stdout
    assert vault_dir.exists()


def test_cli_restore_nonexistent_zip(tmp_path: Path) -> None:
    """CLI: 不存在的 ZIP → exit != 0."""
    result = runner.invoke(
        app,
        ["restore", "--from-zip", str(tmp_path / "ghost.zip")],
    )
    assert result.exit_code != 0


def test_cli_restore_output_shows_version(tmp_path: Path) -> None:
    """CLI: restore 输出含 sisoul_version."""
    zip_path = _make_export_zip(tmp_path)
    vault_dir = tmp_path / "vault"

    result = runner.invoke(
        app,
        ["restore", "--from-zip", str(zip_path), "--vault-dir", str(vault_dir)],
    )
    assert "0.1.0-dev" in result.stdout


def test_cli_restore_output_shows_vault_path(tmp_path: Path) -> None:
    """CLI: restore 输出含 vault 路径."""
    zip_path = _make_export_zip(tmp_path)
    vault_dir = tmp_path / "vault"

    result = runner.invoke(
        app,
        ["restore", "--from-zip", str(zip_path), "--vault-dir", str(vault_dir)],
    )
    assert str(vault_dir) in result.stdout


# ── export → restore 往返测试 ────────────────────────────────

def test_export_restore_roundtrip(tmp_path: Path) -> None:
    """export 再 restore, 文件内容完整一致."""
    from sisoul.cli_commands.export import run_export
    from sisoul.vault.storage import write_file

    # 建原始 vault
    original_vault = tmp_path / "original"
    paths = VaultPaths(root=original_vault)
    paths.ensure_dirs()

    dna = {
        "sisoul_version": "0.1.0-dev",
        "vault_created_at": "2026-05-18T00:00:00+00:00",
        "master_key_hash": "roundtrip_test",
        "schema_version": 1,
    }
    write_file(paths.dna, json.dumps(dna, indent=2))
    write_file(paths.preferences_dir / "pref.md", "# 偏好\n用 Tailwind")
    write_file(paths.goals_dir / "goal-001.md", "---\nid: goal-001\n---\n# 目标一")

    # export
    zip_out = tmp_path / "roundtrip.zip"
    run_export(output=zip_out, vault_dir=paths.root)
    assert zip_out.exists()

    # restore 到新 vault
    restored_vault = tmp_path / "restored"
    restored_paths = run_restore(zip_path=zip_out, vault_dir=restored_vault)

    # 验证内容完整
    restored_dna = json.loads(restored_paths.dna.read_text("utf-8"))
    assert restored_dna["master_key_hash"] == "roundtrip_test"

    restored_pref = (restored_paths.preferences_dir / "pref.md").read_text("utf-8")
    assert "Tailwind" in restored_pref

    restored_goal = (restored_paths.goals_dir / "goal-001.md").read_text("utf-8")
    assert "goal-001" in restored_goal
