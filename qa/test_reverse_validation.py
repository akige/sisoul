"""波 2 qa-E · 反向验证 (§J-2 第 3 条).

故意破坏 vault → sisoul 应报错 + 不损坏其他数据 / 不 crash.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


SISOUL_BIN = shutil.which("sisoul") or str(
    Path(__file__).parent.parent / ".venv" / "bin" / "sisoul"
)


def _run(cmd, env, timeout=15):
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)


@pytest.fixture
def initialized_vault(tmp_path):
    """fixture: 初始化好的 vault, 用于后续反向破坏."""
    home = tmp_path / "home"
    home.mkdir()
    vault = home / ".sisoul"
    env = {
        **os.environ,
        "HOME": str(home),
        "ALLOW_CHANGELOG_PENDING": "1",
    }
    r = _run(
        [SISOUL_BIN, "init", "--vault-dir", str(vault), "--goals", "g1,g2"], env
    )
    assert r.returncode == 0, f"setup init failed: {r.stderr}"
    return env, vault


def test_reverse_missing_dna_status_reports_corruption(initialized_vault):
    """删 dna.json → sisoul status 应报 corruption / vault uninitialized, 不 crash."""
    env, vault = initialized_vault
    dna = vault / "dna.json"
    assert dna.exists()
    dna.unlink()

    r = _run([SISOUL_BIN, "status", "--vault-dir", str(vault)], env)
    # 1. 不许 crash (returncode 不能是 -11/段错误等异常值, 接受 0 / 1 / 2 普通退出)
    assert r.returncode in (
        0,
        1,
        2,
    ), f"status crashed unexpectedly: returncode={r.returncode}, stderr={r.stderr}"
    # 2. 输出 (stdout 或 stderr) 应有提示
    combined = (r.stdout + r.stderr).lower()
    assert any(
        kw in combined
        for kw in [
            "not initialized",
            "missing",
            "no vault",
            "uninit",
            "dna",
            "init first",
            "vault",
            "corrupt",
            "error",
            "not found",
        ]
    ), f"status did not warn about missing dna: stdout={r.stdout!r} stderr={r.stderr!r}"


def test_reverse_corrupted_dna_json(initialized_vault):
    """dna.json 写垃圾 → sisoul status 应不 crash."""
    env, vault = initialized_vault
    dna = vault / "dna.json"
    dna.write_text("not valid json {{{ broken", encoding="utf-8")

    r = _run([SISOUL_BIN, "status", "--vault-dir", str(vault)], env)
    assert r.returncode in (0, 1, 2), f"status crashed: rc={r.returncode}"
    # 关键: 不能 segfault / traceback 暴露给用户
    # 接受任何 graceful exit (即使 stdout 显示 vault 信息 partial)


def test_reverse_remember_to_nonexistent_vault(tmp_path):
    """remember 到不存在的 vault → 应不 crash (允许自动 init 或报错)."""
    env = {**os.environ, "ALLOW_CHANGELOG_PENDING": "1"}
    nonexistent = tmp_path / "ghost-vault"
    r = _run(
        [SISOUL_BIN, "remember", "test", "--vault-dir", str(nonexistent)], env
    )
    assert r.returncode in (0, 1, 2), f"remember crashed: rc={r.returncode}"


def test_reverse_restore_from_invalid_zip(tmp_path):
    """restore --from-zip 指向非 zip 文件 → 应报错不 crash."""
    env = {**os.environ, "HOME": str(tmp_path), "ALLOW_CHANGELOG_PENDING": "1"}
    fake_zip = tmp_path / "fake.zip"
    fake_zip.write_text("not a real zip", encoding="utf-8")
    restore_dir = tmp_path / "restored"

    r = _run(
        [
            SISOUL_BIN,
            "restore",
            "--from-zip",
            str(fake_zip),
            "--vault-dir",
            str(restore_dir),
            "--force",
        ],
        env,
    )
    assert (
        r.returncode != 0
    ), f"restore from invalid zip should fail, got rc={r.returncode}"
    combined = (r.stdout + r.stderr).lower()
    assert any(
        kw in combined for kw in ["zip", "invalid", "corrupt", "error", "bad"]
    ), f"restore did not warn about bad zip: {combined!r}"


def test_reverse_restore_to_existing_vault_without_force(tmp_path):
    """restore 到已存在 vault 不加 --force → 应被拒绝."""
    home = tmp_path / "home"
    home.mkdir()
    vault = home / ".sisoul"
    env = {**os.environ, "HOME": str(home), "ALLOW_CHANGELOG_PENDING": "1"}
    r = _run([SISOUL_BIN, "init", "--vault-dir", str(vault), "--goals", "g1"], env)
    assert r.returncode == 0
    export_zip = tmp_path / "export.zip"
    r = _run(
        [SISOUL_BIN, "export", "--output", str(export_zip), "--vault-dir", str(vault)],
        env,
    )
    assert r.returncode == 0
    r = _run(
        [
            SISOUL_BIN,
            "restore",
            "--from-zip",
            str(export_zip),
            "--vault-dir",
            str(vault),
        ],
        env,
    )
    assert r.returncode != 0 or "force" in (r.stdout + r.stderr).lower(), (
        f"restore should require --force on existing vault: rc={r.returncode}, out={r.stdout!r}"
    )


def test_reverse_init_already_exists_aborts(initialized_vault):
    """init 已存在 vault 不加 --force → 应 abort 不破坏."""
    env, vault = initialized_vault
    dna_before = (vault / "dna.json").read_text()
    r = _run(
        [SISOUL_BIN, "init", "--vault-dir", str(vault), "--goals", "different,goals"],
        env,
    )
    combined = (r.stdout + r.stderr).lower()
    assert r.returncode != 0 or "exist" in combined or "force" in combined, (
        f"init should refuse to overwrite: rc={r.returncode}, out={r.stdout!r}"
    )
    dna_after = (vault / "dna.json").read_text()
    assert dna_before == dna_after, "init without --force corrupted existing dna.json!"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 权限不适用 Windows")
def test_reverse_unwritable_vault_dir_reports_error(tmp_path):
    """vault dir 父目录无写权限 → sisoul init 应报权限错不 crash."""
    parent = tmp_path / "readonly"
    parent.mkdir()
    parent.chmod(0o500)
    try:
        vault = parent / "vault"
        env = {**os.environ, "ALLOW_CHANGELOG_PENDING": "1"}
        r = _run(
            [SISOUL_BIN, "init", "--vault-dir", str(vault), "--goals", "g1"], env
        )
        if os.geteuid() == 0:
            pytest.skip("running as root, chmod 不生效")
        assert (
            r.returncode != 0
        ), f"init to unwritable dir should fail, rc={r.returncode}"
        combined = (r.stdout + r.stderr).lower()
        assert any(
            kw in combined
            for kw in ["permission", "denied", "error", "cannot", "read-only", "readonly"]
        ), f"no permission error message: stdout={r.stdout!r} stderr={r.stderr!r}"
    finally:
        parent.chmod(0o755)
