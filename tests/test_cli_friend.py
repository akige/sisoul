"""Tests for sisoul.cli_commands.friend (波 5 dev-A).

5 子命令: request / accept / list / revoke / info.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli_commands.friend import friend_app
from sisoul.friend.relationship import FriendRelationship

runner = CliRunner()


@pytest.fixture
def alice_args(tmp_path: Path) -> list[str]:
    """公共 args (含 attest_queue_db, 适用于 request/accept/revoke 写 attestation 命令).

    list / info 命令没有 --attest-queue-db (它们不写 attestation), 用 alice_args_readonly.
    """
    return [
        "--own-did", "did:sisoul:alice",
        "--friend-db", str(tmp_path / "friends.db"),
        "--attest-queue-db", str(tmp_path / "attest.db"),
    ]


@pytest.fixture
def alice_args_readonly(tmp_path: Path) -> list[str]:
    """list / info 命令 (不写 attestation, 不带 --attest-queue-db)."""
    return [
        "--own-did", "did:sisoul:alice",
        "--friend-db", str(tmp_path / "friends.db"),
    ]


@pytest.fixture
def alice_rel(tmp_path: Path) -> FriendRelationship:
    """直建一个 alice_rel 用于 setup data (非 CLI)."""
    return FriendRelationship(
        own_did="did:sisoul:alice",
        db_path=tmp_path / "friends.db",
        attest_queue_db=tmp_path / "attest.db",
    )


class TestFriendRequestCmd:
    def test_request_text(self, alice_args: list[str]) -> None:
        result = runner.invoke(
            friend_app, ["request", "did:sisoul:bob", "--message", "hi"]
            + alice_args
        )
        assert result.exit_code == 0, result.output
        assert "friend request 已发出" in result.output
        assert "did:sisoul:bob" in result.output
        assert "request_id" in result.output

    def test_request_json(self, alice_args: list[str]) -> None:
        result = runner.invoke(
            friend_app, ["request", "did:sisoul:bob", "--json"] + alice_args
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["target_did"] == "did:sisoul:bob"
        assert data["direction"] == "outbound"

    def test_request_self_errors(self, alice_args: list[str]) -> None:
        result = runner.invoke(
            friend_app, ["request", "did:sisoul:alice"] + alice_args
        )
        assert result.exit_code != 0
        assert "ERROR" in result.output


class TestFriendAcceptCmd:
    def test_accept_inbound(
        self, alice_args: list[str], alice_rel: FriendRelationship
    ) -> None:
        # setup: 模拟 Bob 给 Alice 发了 inbound request
        inbound = alice_rel.receive_friend_request(
            "did:sisoul:bob", attestation_uid="0x_bob"
        )
        result = runner.invoke(
            friend_app, ["accept", inbound.request_id] + alice_args
        )
        assert result.exit_code == 0, result.output
        assert "accepted friend" in result.output
        assert "did:sisoul:bob" in result.output

    def test_accept_unknown_request_id(
        self, alice_args: list[str]
    ) -> None:
        result = runner.invoke(
            friend_app, ["accept", "nonexistent-uuid"] + alice_args
        )
        assert result.exit_code != 0
        assert "ERROR" in result.output


class TestFriendListCmd:
    def test_list_empty(self, alice_args_readonly: list[str]) -> None:
        result = runner.invoke(friend_app, ["list"] + alice_args_readonly)
        assert result.exit_code == 0, result.output
        assert "本地无 friend" in result.output

    def test_list_with_friends(
        self, alice_args_readonly: list[str], alice_rel: FriendRelationship
    ) -> None:
        alice_rel.send_friend_request("did:sisoul:bob")
        result = runner.invoke(friend_app, ["list"] + alice_args_readonly)
        assert result.exit_code == 0, result.output
        assert "did:sisoul:bob" in result.output
        assert "pending" in result.output

    def test_list_show_score(
        self, alice_args_readonly: list[str], alice_rel: FriendRelationship
    ) -> None:
        inbound = alice_rel.receive_friend_request("did:sisoul:bob")
        alice_rel.accept_friend_request(inbound.request_id)
        alice_rel.confirm_mutual_attestation("did:sisoul:bob", "0x_bob_uid")
        result = runner.invoke(
            friend_app, ["list", "--show-score"] + alice_args_readonly
        )
        assert result.exit_code == 0
        assert "score" in result.output.lower() or "strong" in result.output.lower()

    def test_list_filter_status(
        self, alice_args_readonly: list[str], alice_rel: FriendRelationship
    ) -> None:
        alice_rel.send_friend_request("did:sisoul:bob")
        inbound = alice_rel.receive_friend_request("did:sisoul:charlie")
        alice_rel.accept_friend_request(inbound.request_id)
        result_active = runner.invoke(
            friend_app, ["list", "--status", "active"] + alice_args_readonly
        )
        result_pending = runner.invoke(
            friend_app, ["list", "--status", "pending"] + alice_args_readonly
        )
        assert "did:sisoul:charlie" in result_active.output
        assert "did:sisoul:bob" not in result_active.output
        assert "did:sisoul:bob" in result_pending.output

    def test_list_invalid_status(self, alice_args_readonly: list[str]) -> None:
        result = runner.invoke(
            friend_app, ["list", "--status", "garbage"] + alice_args_readonly
        )
        assert result.exit_code != 0
        assert "ERROR" in result.output

    def test_list_json(
        self, alice_args_readonly: list[str], alice_rel: FriendRelationship
    ) -> None:
        alice_rel.send_friend_request("did:sisoul:bob")
        result = runner.invoke(
            friend_app, ["list", "--json"] + alice_args_readonly
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["did"] == "did:sisoul:bob"


class TestFriendRevokeCmd:
    def test_revoke(
        self, alice_args: list[str], alice_rel: FriendRelationship
    ) -> None:
        alice_rel.send_friend_request("did:sisoul:bob")
        result = runner.invoke(
            friend_app, ["revoke", "did:sisoul:bob"] + alice_args
        )
        assert result.exit_code == 0, result.output
        assert "revoked friend" in result.output

    def test_revoke_unknown(self, alice_args: list[str]) -> None:
        result = runner.invoke(
            friend_app, ["revoke", "did:sisoul:nobody"] + alice_args
        )
        assert result.exit_code != 0
        assert "ERROR" in result.output


class TestFriendInfoCmd:
    def test_info_text(
        self, alice_args_readonly: list[str], alice_rel: FriendRelationship
    ) -> None:
        alice_rel.send_friend_request("did:sisoul:bob")
        result = runner.invoke(
            friend_app, ["info", "did:sisoul:bob"] + alice_args_readonly
        )
        assert result.exit_code == 0, result.output
        assert "did:sisoul:bob" in result.output
        assert "强连接评分" in result.output
        assert "互惠 ledger" in result.output

    def test_info_json(
        self, alice_args_readonly: list[str], alice_rel: FriendRelationship
    ) -> None:
        alice_rel.send_friend_request("did:sisoul:bob")
        result = runner.invoke(
            friend_app, ["info", "did:sisoul:bob", "--json"] + alice_args_readonly
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["friend"]["did"] == "did:sisoul:bob"
        assert "score_breakdown" in data
        assert "ledger_summary" in data
        # ledger_summary 由 dev-D ship; 本测试不强检 available 状态 (兼容 dev-D ship 前/后)
        assert "available" in data["ledger_summary"]

    def test_info_unknown(self, alice_args_readonly: list[str]) -> None:
        result = runner.invoke(
            friend_app, ["info", "did:sisoul:nobody"] + alice_args_readonly
        )
        assert result.exit_code != 0
        assert "ERROR" in result.output


class TestFriendHelp:
    def test_help_lists_subcommands(self) -> None:
        result = runner.invoke(friend_app, ["--help"])
        assert result.exit_code == 0
        for cmd in ["request", "accept", "list", "revoke", "info"]:
            assert cmd in result.output
