"""tests for sisoul.vault.storage (Phase 1 W3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sisoul.vault.storage import (
    VaultPaths,
    list_files,
    read_file,
    vault_size,
    write_file,
)


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    """tmp vault dir (隔离, 不动 ~/.sisoul/)."""
    return tmp_path / "sisoul-test"


def test_write_then_read_roundtrip(vault_root: Path) -> None:
    fp = vault_root / "sub" / "a.md"
    written = write_file(fp, "hello world\n中文")
    assert written == fp
    assert fp.exists()
    assert read_file(fp) == "hello world\n中文"


def test_write_auto_mkdir(vault_root: Path) -> None:
    deep = vault_root / "a" / "b" / "c" / "x.md"
    write_file(deep, "x")
    assert deep.read_text() == "x"


def test_read_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_file(tmp_path / "nope.md")


def test_list_files_returns_sorted(vault_root: Path) -> None:
    vault_root.mkdir()
    write_file(vault_root / "b.md", "b")
    write_file(vault_root / "a.md", "a")
    write_file(vault_root / "c.txt", "c")  # not *.md
    result = list_files(vault_root, "*.md")
    assert [p.name for p in result] == ["a.md", "b.md"]


def test_list_files_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert list_files(tmp_path / "nope") == []


def test_vault_size_recursive(vault_root: Path) -> None:
    write_file(vault_root / "a.md", "abc")  # 3 bytes
    write_file(vault_root / "sub" / "b.md", "defgh")  # 5 bytes
    assert vault_size(vault_root) == 8


def test_vault_size_missing_returns_zero(tmp_path: Path) -> None:
    assert vault_size(tmp_path / "no") == 0


def test_vault_paths_ensure_dirs(vault_root: Path) -> None:
    paths = VaultPaths(root=vault_root)
    paths.ensure_dirs()
    assert paths.preferences_dir.is_dir()
    assert paths.goals_dir.is_dir()
    assert paths.chat_history_dir.is_dir()
    # dna 是文件路径, 不自动建
    assert not paths.dna.exists()
