"""测试 p2p.sync — inventory / diff / conflict 落盘 (波 4 dev-A)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from sisoul.p2p.sync import (
    CONFLICT_WINDOW_NS,
    EXCLUDE_PREFIXES,
    FileMeta,
    Inventory,
    apply_pull,
    build_inventory,
    compute_diff,
    decode_message,
    encode_message,
    record_conflict,
)


def _touch(path: Path, content: bytes = b"x", mtime_ns: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))


# ── FileMeta / Inventory ──────────────────────────────────────────────────────


class TestFileMeta:
    def test_to_dict_from_dict_roundtrip(self):
        m = FileMeta(rel_path="a.md", mtime_ns=100, size=5, sha256_hex="abc")
        d = m.to_dict()
        m2 = FileMeta.from_dict(d)
        assert m == m2


class TestInventory:
    def test_to_dict_from_dict_roundtrip(self):
        inv = Inventory()
        inv.files["a.md"] = FileMeta(rel_path="a.md", mtime_ns=1, size=1, sha256_hex="aa")
        inv.files["b.md"] = FileMeta(rel_path="b.md", mtime_ns=2, size=2, sha256_hex="bb")
        d = inv.to_dict()
        inv2 = Inventory.from_dict(d)
        assert len(inv2.files) == 2
        assert inv2.files["a.md"].sha256_hex == "aa"


# ── build_inventory ──────────────────────────────────────────────────────────


class TestBuildInventory:
    def test_empty_vault(self, tmp_path):
        inv = build_inventory(tmp_path)
        assert inv.files == {}

    def test_nonexistent_vault(self, tmp_path):
        inv = build_inventory(tmp_path / "nonexistent")
        assert inv.files == {}

    def test_scans_files_with_hash(self, tmp_path):
        _touch(tmp_path / "preferences" / "2026-05-18.md", b"hello")
        _touch(tmp_path / "goals" / "g1.md", b"goal 1")
        inv = build_inventory(tmp_path)
        assert len(inv.files) == 2
        assert "preferences/2026-05-18.md" in inv.files
        assert "goals/g1.md" in inv.files
        # hash 正确
        import hashlib
        expected = hashlib.sha256(b"hello").hexdigest()
        assert inv.files["preferences/2026-05-18.md"].sha256_hex == expected

    def test_excludes_seed_txt(self, tmp_path):
        _touch(tmp_path / "seed.txt", b"abandon abandon ...")
        _touch(tmp_path / "preferences" / "x.md", b"x")
        inv = build_inventory(tmp_path)
        assert "seed.txt" not in inv.files
        assert "preferences/x.md" in inv.files

    def test_excludes_p2p_state(self, tmp_path):
        _touch(tmp_path / "p2p" / "peers.json", b"{}")
        _touch(tmp_path / "p2p" / "conflicts.log", b"")
        _touch(tmp_path / "preferences" / "y.md", b"y")
        inv = build_inventory(tmp_path)
        assert all("p2p/" not in p for p in inv.files.keys())
        assert "preferences/y.md" in inv.files

    def test_excludes_lock_tmp(self, tmp_path):
        _touch(tmp_path / ".lock", b"")
        _touch(tmp_path / ".tmp", b"")
        _touch(tmp_path / "good.md", b"k")
        inv = build_inventory(tmp_path)
        # 注: 排除前缀 == 完整 rel_path 是 .lock / .tmp 本身, .lockxxx 也排除 (前缀匹配)
        assert ".lock" not in inv.files
        assert "good.md" in inv.files

    def test_exclude_prefixes_constant(self):
        assert "seed.txt" in EXCLUDE_PREFIXES
        assert "p2p/" in EXCLUDE_PREFIXES


# ── compute_diff ─────────────────────────────────────────────────────────────


class TestComputeDiff:
    def _meta(self, rel: str, mtime_ns: int, sha: str = "x", size: int = 1) -> FileMeta:
        return FileMeta(rel_path=rel, mtime_ns=mtime_ns, size=size, sha256_hex=sha)

    def test_empty_both_no_diff(self):
        diff = compute_diff(Inventory(), Inventory())
        assert diff.pull == [] and diff.push == [] and diff.conflicts == []

    def test_pull_only(self):
        local = Inventory()
        remote = Inventory()
        remote.files["a.md"] = self._meta("a.md", 100, "a")
        diff = compute_diff(local, remote)
        assert diff.pull == ["a.md"]
        assert diff.push == []
        assert diff.conflicts == []

    def test_push_only(self):
        local = Inventory()
        local.files["b.md"] = self._meta("b.md", 100, "b")
        diff = compute_diff(local, Inventory())
        assert diff.push == ["b.md"]
        assert diff.pull == []

    def test_same_hash_no_op(self):
        local = Inventory()
        remote = Inventory()
        local.files["a.md"] = self._meta("a.md", 100, "samehash")
        remote.files["a.md"] = self._meta("a.md", 200, "samehash")
        diff = compute_diff(local, remote)
        assert diff.pull == [] and diff.push == [] and diff.conflicts == []

    def test_newer_remote_wins_pull(self):
        """remote mtime >> local 且 hash 不同 → pull."""
        local = Inventory()
        remote = Inventory()
        local.files["a.md"] = self._meta("a.md", 100, "local-hash")
        remote.files["a.md"] = self._meta("a.md", 100 + CONFLICT_WINDOW_NS + 1, "remote-hash")
        diff = compute_diff(local, remote)
        assert diff.pull == ["a.md"]
        assert diff.conflicts == []

    def test_newer_local_wins_push(self):
        local = Inventory()
        remote = Inventory()
        remote.files["a.md"] = self._meta("a.md", 100, "remote-hash")
        local.files["a.md"] = self._meta("a.md", 100 + CONFLICT_WINDOW_NS + 1, "local-hash")
        diff = compute_diff(local, remote)
        assert diff.push == ["a.md"]
        assert diff.conflicts == []

    def test_close_mtime_different_hash_is_conflict(self):
        local = Inventory()
        remote = Inventory()
        local.files["a.md"] = self._meta("a.md", 100, "local-hash")
        remote.files["a.md"] = self._meta("a.md", 100 + 1000, "remote-hash")  # 1us 差 < 2s 窗口
        diff = compute_diff(local, remote)
        assert diff.conflicts == ["a.md"]
        assert diff.pull == [] and diff.push == []


# ── apply_pull + record_conflict ──────────────────────────────────────────────


class TestApplyPull:
    def test_basic_write(self, tmp_path):
        path = apply_pull(tmp_path, "preferences/2026.md", b"content", mtime_ns=1700000000_000_000_000)
        assert path.exists()
        assert path.read_bytes() == b"content"
        # mtime 设置生效
        assert abs(path.stat().st_mtime_ns - 1700000000_000_000_000) < 1000

    def test_atomic_tmp_cleaned(self, tmp_path):
        apply_pull(tmp_path, "a.md", b"x")
        # 不应有 .tmp_p2p 残留
        assert not list(tmp_path.glob("*.tmp_p2p"))

    def test_overwrite_existing(self, tmp_path):
        target = tmp_path / "a.md"
        target.write_bytes(b"old")
        apply_pull(tmp_path, "a.md", b"new")
        assert target.read_bytes() == b"new"


class TestRecordConflict:
    def test_writes_conflict_copy_and_log(self, tmp_path):
        local = FileMeta(rel_path="a.md", mtime_ns=100, size=3, sha256_hex="local")
        remote = FileMeta(rel_path="a.md", mtime_ns=200, size=3, sha256_hex="remote")
        cpath = record_conflict(tmp_path, "a.md", local, remote, peer_id="peerabcdef", remote_content=b"R")
        # conflict 副本存在
        assert cpath.exists()
        assert cpath.read_bytes() == b"R"
        assert "conflict-peerabcd-200" in cpath.name
        # log 写入
        log_path = tmp_path / "p2p" / "conflicts.log"
        assert log_path.exists()
        line = log_path.read_text().strip()
        rec = json.loads(line)
        assert rec["rel_path"] == "a.md"
        assert rec["local_sha256"] == "local"
        assert rec["remote_sha256"] == "remote"
        assert rec["peer_id"] == "peerabcdef"


# ── encode/decode message ───────────────────────────────────────────────────


class TestEncodeDecodeMessage:
    def test_roundtrip(self):
        raw = encode_message("INVENTORY_REQUEST", {"hello": "world", "n": 42})
        msg_type, payload = decode_message(raw)
        assert msg_type == "INVENTORY_REQUEST"
        assert payload == {"hello": "world", "n": 42}

    def test_message_includes_ts(self):
        raw = encode_message("X", {})
        obj = json.loads(raw.decode("utf-8"))
        assert "ts" in obj
        assert obj["ts"] > 0
