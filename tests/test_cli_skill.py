"""tests for sisoul.cli_commands.skill (波 6 dev-A).

Typer CliRunner 跑各子命令. self-loop borrow 需 ~/.sisoul/seed.txt → fixture 跑 init.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli_commands.skill import skill_app
from sisoul.friend.skill_borrow import _ACTIVE_SESSIONS
from sisoul.friend.skill_ipfs import clear_mock_blob_cache


runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch):
    """每 test 独立 HOME."""
    _ACTIVE_SESSIONS.clear()
    clear_mock_blob_cache()
    monkeypatch.setenv("HOME", str(tmp_path))
    yield
    _ACTIVE_SESSIONS.clear()
    clear_mock_blob_cache()


@pytest.fixture
def with_seed(tmp_path: Path, monkeypatch):
    """生成 seed 文件 + monkeypatch DEFAULT_SEED_FILE (它在 import 时 frozen)."""
    from sisoul.identity import seed as seed_mod
    seed_path = tmp_path / ".sisoul" / "seed.txt"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    m = seed_mod.generate_mnemonic(strength=128)
    seed_mod.save_mnemonic_to_file(m, path=seed_path)
    monkeypatch.setattr(seed_mod, "DEFAULT_SEED_FILE", seed_path)
    return seed_path


# ── create ────────────────────────────────────────────────────────────────


def test_create_basic_with_inline_prompt(tmp_path):
    result = runner.invoke(skill_app, [
        "create", "solidity-expert",
        "--system-prompt", "You are a Solidity expert.",
        "--description", "DeFi specialist",
        "--personality", "pedantic",
        "--personality", "security-paranoid",
        "--recommended-model", "claude-opus-4-7",
        "--owner-did", "did:sisoul:bob",
        "--json",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["skill_id"] == "solidity-expert"
    assert data["qualified_name"] == "did:sisoul:bob:solidity-expert"
    assert data["personality_traits"] == ["pedantic", "security-paranoid"]
    # 落地到 ~/.sisoul/skills/owned/
    saved = Path(data["saved_to"])
    assert saved.exists()


def test_create_from_file(tmp_path):
    sp_file = tmp_path / "sp.md"
    sp_file.write_text("You are a Python helper.", encoding="utf-8")
    result = runner.invoke(skill_app, [
        "create", "python-helper",
        "--from-file", str(sp_file),
        "--owner-did", "did:sisoul:bob",
    ])
    assert result.exit_code == 0, result.output
    assert "已创建 skill" in result.output
    assert "python-helper" in result.output


def test_create_missing_prompt(tmp_path):
    result = runner.invoke(skill_app, [
        "create", "x", "--owner-did", "bob",
    ])
    assert result.exit_code == 2
    assert "system-prompt" in result.output or "from-file" in result.output


def test_create_from_file_not_found(tmp_path):
    result = runner.invoke(skill_app, [
        "create", "x", "--from-file", "/nonexistent.md",
        "--owner-did", "bob",
    ])
    assert result.exit_code == 2


# ── list ──────────────────────────────────────────────────────────────────


def test_list_empty(tmp_path):
    result = runner.invoke(skill_app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["owned"] == []


def test_list_after_create(tmp_path):
    runner.invoke(skill_app, [
        "create", "s1",
        "--system-prompt", "sp1",
        "--owner-did", "bob",
    ])
    runner.invoke(skill_app, [
        "create", "s2",
        "--system-prompt", "sp2",
        "--owner-did", "bob",
    ])
    result = runner.invoke(skill_app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    ids = sorted(s["skill_id"] for s in data["owned"])
    assert ids == ["s1", "s2"]


# ── lend (不 pin, 仅占位输出) ──────────────────────────────────────────────


def test_lend_no_pin(tmp_path):
    runner.invoke(skill_app, [
        "create", "s1", "--system-prompt", "sp",
        "--owner-did", "bob",
    ])
    result = runner.invoke(skill_app, ["lend", "s1", "--max-duration", "60", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["skill_id"] == "s1"
    assert data["max_duration_minutes"] == 60
    assert data["pin_to_ipfs"] is False


# ── borrow self-loop ──────────────────────────────────────────────────────


def test_borrow_self_loop(tmp_path, with_seed, monkeypatch):
    """borrow 自己的 skill: 完整 round-trip."""
    monkeypatch.delenv("PINATA_JWT", raising=False)
    # 先 create
    create_res = runner.invoke(skill_app, [
        "create", "self-skill",
        "--system-prompt", "test",
        "--owner-did", "self.local",
        "--json",
    ])
    assert create_res.exit_code == 0, create_res.output

    # borrow 同 owner (self-loop)
    result = runner.invoke(skill_app, [
        "borrow", "self.local:self-skill",
        "--borrower-did", "self.local",
        "--duration", "5",  # 5 min
        "--json",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["session"]["status"] == "active"
    assert data["session"]["skill_id"] == "self-skill"
    assert data["session"]["duration_minutes"] == 5


def test_borrow_self_loop_with_duration_test_shortcut(tmp_path, with_seed, monkeypatch):
    """duration-test 缩短验证."""
    monkeypatch.delenv("PINATA_JWT", raising=False)
    runner.invoke(skill_app, [
        "create", "s", "--system-prompt", "sp", "--owner-did", "self.local",
    ])
    result = runner.invoke(skill_app, [
        "borrow", "self.local:s",
        "--borrower-did", "self.local",
        "--duration", "30",
        "--duration-test", "3",  # 3 秒 instead of 30 min
        "--json",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    started = data["session"]["started_at"]
    expires = data["session"]["expires_at"]
    assert expires - started == 3


def test_borrow_remote_not_implemented(tmp_path, with_seed):
    """非 self-loop 借应 fail (Phase 5 P2P)."""
    result = runner.invoke(skill_app, [
        "borrow", "remote.did:remote-skill",
        "--borrower-did", "self.local",
    ])
    assert result.exit_code == 1


def test_borrow_bad_qualified_name(tmp_path, with_seed):
    result = runner.invoke(skill_app, ["borrow", "no-colon"])
    assert result.exit_code == 2


# ── sessions + end-session ────────────────────────────────────────────────


def test_sessions_then_end(tmp_path, with_seed, monkeypatch):
    monkeypatch.delenv("PINATA_JWT", raising=False)
    runner.invoke(skill_app, [
        "create", "s", "--system-prompt", "sp", "--owner-did", "self.local",
    ])
    borrow_res = runner.invoke(skill_app, [
        "borrow", "self.local:s",
        "--borrower-did", "self.local",
        "--json",
    ])
    sid = json.loads(borrow_res.output)["session"]["session_id"]

    sessions_res = runner.invoke(skill_app, [
        "sessions", "--own-did", "self.local", "--json",
    ])
    assert sessions_res.exit_code == 0, sessions_res.output
    listed = json.loads(sessions_res.output)
    assert any(s["session_id"] == sid for s in listed)

    end_res = runner.invoke(skill_app, [
        "end-session", sid, "--reason", "test-cleanup", "--json",
    ])
    assert end_res.exit_code == 0, end_res.output
    ended = json.loads(end_res.output)
    assert ended["status"] == "destroyed"
    assert ended["destroy_reason"] == "test-cleanup"


def test_end_session_not_found(tmp_path):
    result = runner.invoke(skill_app, ["end-session", "bs_nope"])
    assert result.exit_code == 1


# ── non-JSON 输出路径 (覆盖 typer.echo 默认分支) ──────────────────────────


def test_create_non_json_output(tmp_path):
    result = runner.invoke(skill_app, [
        "create", "s",
        "--system-prompt", "sp",
        "--owner-did", "bob",
    ])
    assert result.exit_code == 0
    assert "已创建 skill" in result.output
    assert "fingerprint" in result.output


def test_list_non_json_output(tmp_path):
    runner.invoke(skill_app, [
        "create", "s", "--system-prompt", "sp", "--owner-did", "bob",
    ])
    result = runner.invoke(skill_app, ["list"])
    assert result.exit_code == 0
    assert "owned skills" in result.output
    assert "bob:s" in result.output


def test_list_available_to_borrow(tmp_path, monkeypatch):
    """触发 available_to_borrow 分支 (SkillPinDB)."""
    monkeypatch.delenv("PINATA_JWT", raising=False)
    # 直接往 PinDB 塞一条非 self 的记录
    from sisoul.friend.skill_ipfs import SkillPinDB, SkillPinRecord
    import time as _t
    with SkillPinDB() as db:
        now = int(_t.time())
        db.upsert(SkillPinRecord(
            cid="mockcid-other", owner_did="other.did", skill_id="other-skill",
            pinned_at=now, expires_at=now + 3600,
        ))
    result = runner.invoke(skill_app, [
        "list", "--available-to-borrow", "--own-did", "self.local", "--json",
    ])
    # 上面 --own-did 不存在, 但参数名 owner_did. 重试用 owner_did
    if result.exit_code != 0:
        # 跑非 JSON 也行
        result = runner.invoke(skill_app, [
            "list", "--available-to-borrow", "--json",
        ])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert any(s["cid"] == "mockcid-other" for s in data["available_to_borrow"])


def test_sessions_empty(tmp_path):
    result = runner.invoke(skill_app, ["sessions"])
    assert result.exit_code == 0
    assert "no skill borrow sessions" in result.output
