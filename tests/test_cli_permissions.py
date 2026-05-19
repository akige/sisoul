"""tests for sisoul perms CLI (波 5 dev-C).

覆盖 list / set / revoke / reputation / scan-log 5 子命令.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli_commands.permissions import perms_app
from sisoul.friend.anti_abuse import scan_request_pattern
from sisoul.friend.permissions import (
    FriendPermission,
    LLMQuotaShare,
    load_permissions,
    save_permissions,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def tmp_perms_dir(tmp_path: Path) -> Path:
    p = tmp_path / "friends"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── list ─────────────────────────────────────────────────────────────────────


class TestList:
    def test_list_empty(self, runner: CliRunner, tmp_perms_dir: Path) -> None:
        r = runner.invoke(perms_app, ["list", "--perms-dir", str(tmp_perms_dir)])
        assert r.exit_code == 0
        assert "无朋友 perm 记录" in r.stdout

    def test_list_specific_friend_missing(
        self, runner: CliRunner, tmp_perms_dir: Path
    ) -> None:
        r = runner.invoke(
            perms_app,
            ["list", "--friend", "did:nope", "--perms-dir", str(tmp_perms_dir)],
        )
        assert r.exit_code == 1

    def test_list_after_save(
        self, runner: CliRunner, tmp_perms_dir: Path
    ) -> None:
        save_permissions(
            "did:alice",
            FriendPermission(
                friend_did="did:alice",
                llm_quota_share=LLMQuotaShare(
                    enabled=True, mode="strong-tie-auto", monthly_token_cap=1000
                ),
            ),
            perms_dir=tmp_perms_dir,
        )
        r = runner.invoke(perms_app, ["list", "--perms-dir", str(tmp_perms_dir)])
        assert r.exit_code == 0
        assert "did:alice" in r.stdout

    def test_list_json(self, runner: CliRunner, tmp_perms_dir: Path) -> None:
        save_permissions(
            "did:alice",
            FriendPermission(friend_did="did:alice"),
            perms_dir=tmp_perms_dir,
        )
        r = runner.invoke(
            perms_app, ["list", "--json", "--perms-dir", str(tmp_perms_dir)]
        )
        assert r.exit_code == 0
        data = json.loads(r.stdout)
        assert isinstance(data, list)
        assert data[0]["friend"] == "did:alice"

    def test_list_one_friend_json(
        self, runner: CliRunner, tmp_perms_dir: Path
    ) -> None:
        save_permissions(
            "did:alice",
            FriendPermission(friend_did="did:alice"),
            perms_dir=tmp_perms_dir,
        )
        r = runner.invoke(
            perms_app,
            [
                "list",
                "--friend",
                "did:alice",
                "--json",
                "--perms-dir",
                str(tmp_perms_dir),
            ],
        )
        assert r.exit_code == 0
        data = json.loads(r.stdout)
        assert data["friend"] == "did:alice"


# ── set ──────────────────────────────────────────────────────────────────────


class TestSet:
    def test_set_llm_quota(
        self, runner: CliRunner, tmp_perms_dir: Path
    ) -> None:
        r = runner.invoke(
            perms_app,
            [
                "set",
                "did:alice",
                "--mode",
                "strong-tie-auto",
                "--resource",
                "llm_quota",
                "--monthly-cap",
                "500000",
                "--rate-limit",
                "10",
                "-m",
                "claude-opus-4-7",
                "-m",
                "claude-sonnet-4-6",
                "--perms-dir",
                str(tmp_perms_dir),
            ],
        )
        assert r.exit_code == 0, r.stdout
        p = load_permissions("did:alice", perms_dir=tmp_perms_dir)
        assert p.llm_quota_share.monthly_token_cap == 500_000
        assert p.llm_quota_share.rate_limit == 10
        assert set(p.llm_quota_share.models) == {
            "claude-opus-4-7",
            "claude-sonnet-4-6",
        }

    def test_set_invalid_mode(
        self, runner: CliRunner, tmp_perms_dir: Path
    ) -> None:
        r = runner.invoke(
            perms_app,
            [
                "set",
                "did:alice",
                "--mode",
                "INVALID",
                "--perms-dir",
                str(tmp_perms_dir),
            ],
        )
        assert r.exit_code == 2

    def test_set_invalid_resource(
        self, runner: CliRunner, tmp_perms_dir: Path
    ) -> None:
        r = runner.invoke(
            perms_app,
            [
                "set",
                "did:alice",
                "--resource",
                "BOGUS",
                "--perms-dir",
                str(tmp_perms_dir),
            ],
        )
        assert r.exit_code == 2

    def test_set_ai_skill(self, runner: CliRunner, tmp_perms_dir: Path) -> None:
        r = runner.invoke(
            perms_app,
            [
                "set",
                "did:alice",
                "--mode",
                "per-request",
                "--resource",
                "ai_skill",
                "-s",
                "solidity-expert",
                "--per-session-max-minutes",
                "30",
                "--perms-dir",
                str(tmp_perms_dir),
            ],
        )
        assert r.exit_code == 0
        p = load_permissions("did:alice", perms_dir=tmp_perms_dir)
        assert p.ai_skill_share.skills == ["solidity-expert"]
        assert p.ai_skill_share.per_session_max_minutes == 30

    def test_set_clear_models(
        self, runner: CliRunner, tmp_perms_dir: Path
    ) -> None:
        # 先有 models
        save_permissions(
            "did:alice",
            FriendPermission(
                friend_did="did:alice",
                llm_quota_share=LLMQuotaShare(
                    enabled=True, mode="strong-tie-auto", models=["x"]
                ),
            ),
            perms_dir=tmp_perms_dir,
        )
        r = runner.invoke(
            perms_app,
            [
                "set",
                "did:alice",
                "--clear-models",
                "--perms-dir",
                str(tmp_perms_dir),
            ],
        )
        assert r.exit_code == 0
        p = load_permissions("did:alice", perms_dir=tmp_perms_dir)
        assert p.llm_quota_share.models == []


# ── revoke ───────────────────────────────────────────────────────────────────


class TestRevoke:
    def test_revoke_marks_perm(
        self, runner: CliRunner, tmp_perms_dir: Path
    ) -> None:
        save_permissions(
            "did:alice",
            FriendPermission(friend_did="did:alice"),
            perms_dir=tmp_perms_dir,
        )
        r = runner.invoke(
            perms_app,
            [
                "revoke",
                "did:alice",
                "--reason",
                "abuse",
                "--perms-dir",
                str(tmp_perms_dir),
            ],
        )
        assert r.exit_code == 0
        assert "revoked" in r.stdout
        p = load_permissions("did:alice", perms_dir=tmp_perms_dir)
        assert p.revoked

    def test_undo_revoke(
        self, runner: CliRunner, tmp_perms_dir: Path
    ) -> None:
        save_permissions(
            "did:alice",
            FriendPermission(friend_did="did:alice"),
            perms_dir=tmp_perms_dir,
        )
        runner.invoke(
            perms_app,
            ["revoke", "did:alice", "--perms-dir", str(tmp_perms_dir)],
        )
        r = runner.invoke(
            perms_app,
            [
                "revoke",
                "did:alice",
                "--undo",
                "--perms-dir",
                str(tmp_perms_dir),
            ],
        )
        assert r.exit_code == 0
        p = load_permissions("did:alice", perms_dir=tmp_perms_dir)
        assert not p.revoked

    def test_undo_missing_fails(
        self, runner: CliRunner, tmp_perms_dir: Path
    ) -> None:
        r = runner.invoke(
            perms_app,
            [
                "revoke",
                "did:nope",
                "--undo",
                "--perms-dir",
                str(tmp_perms_dir),
            ],
        )
        assert r.exit_code == 1


# ── reputation ──────────────────────────────────────────────────────────────


class TestReputation:
    def test_reputation_requires_did(self, runner: CliRunner) -> None:
        r = runner.invoke(perms_app, ["reputation"])
        assert r.exit_code == 2

    def test_reputation_for_self(self, runner: CliRunner) -> None:
        r = runner.invoke(
            perms_app,
            ["reputation", "--self-did", "did:me", "--borrows", "10", "--lends", "10"],
        )
        assert r.exit_code == 0
        assert "did:me" in r.stdout
        assert "grade" in r.stdout

    def test_reputation_for_friend_json(self, runner: CliRunner) -> None:
        r = runner.invoke(
            perms_app,
            [
                "reputation",
                "--friend",
                "did:alice",
                "--abuse-incidents",
                "3",
                "--json",
            ],
        )
        assert r.exit_code == 0
        data = json.loads(r.stdout)
        assert data["did"] == "did:alice"
        assert data["score"] == 40  # 100 - 3*20


# ── scan-log ────────────────────────────────────────────────────────────────


class TestScanLog:
    def test_scan_log_empty(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        r = runner.invoke(
            perms_app,
            ["scan-log", "--scan-db", str(tmp_path / "scan.db")],
        )
        assert r.exit_code == 0
        assert "无记录" in r.stdout

    def test_scan_log_after_block(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        db = tmp_path / "scan.db"
        # 触发 1 block
        scan_request_pattern(
            {
                "friend_did": "did:alice",
                "amount": 999_999_999,
                "model": "x",
                "prompt_hash": "h",
            },
            persist_db=db,
        )
        r = runner.invoke(
            perms_app,
            ["scan-log", "--scan-db", str(db), "--json"],
        )
        assert r.exit_code == 0
        data = json.loads(r.stdout)
        assert len(data) >= 1
        assert any("token_burst" in row["reason"] for row in data)
