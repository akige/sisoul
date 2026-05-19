"""tests for sisoul.cli_commands.did (Phase 2 W21-W22, dev-B).

覆盖 5 子命令: register / resolve / list / link-friend / link-social.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli_commands.did import did_app


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "sisoul-test"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ── register ─────────────────────────────────────────────────────────────────


class TestRegisterCmd:
    def test_register_mock_success(self, runner: CliRunner, vault_root: Path) -> None:
        r = runner.invoke(
            did_app,
            ["register", "alice", "--network", "mock", "--vault-dir", str(vault_root)],
        )
        assert r.exit_code == 0, r.output
        assert "DID 已注册" in r.output
        assert "alice.sisoul.eth" in r.output

    def test_register_sepolia_default(self, runner: CliRunner, vault_root: Path) -> None:
        r = runner.invoke(
            did_app, ["register", "alice", "--vault-dir", str(vault_root)]
        )
        assert r.exit_code == 0, r.output
        assert "网络: sepolia" in r.output

    def test_register_mainnet_forbidden(
        self, runner: CliRunner, vault_root: Path
    ) -> None:
        r = runner.invoke(
            did_app,
            ["register", "alice", "--network", "mainnet", "--vault-dir", str(vault_root)],
        )
        assert r.exit_code == 1
        assert "mainnet" in r.output

    def test_register_invalid_handle(self, runner: CliRunner, vault_root: Path) -> None:
        r = runner.invoke(
            did_app,
            ["register", "a@b", "--network", "mock", "--vault-dir", str(vault_root)],
        )
        assert r.exit_code == 1
        assert "非法字符" in r.output or "handle" in r.output

    def test_register_duplicate(self, runner: CliRunner, vault_root: Path) -> None:
        runner.invoke(
            did_app,
            ["register", "alice", "--network", "mock", "--vault-dir", str(vault_root)],
        )
        r = runner.invoke(
            did_app,
            ["register", "alice", "--network", "mock", "--vault-dir", str(vault_root)],
        )
        assert r.exit_code == 1
        assert "已有" in r.output or "taken" in r.output.lower()

    def test_register_json_output(self, runner: CliRunner, vault_root: Path) -> None:
        r = runner.invoke(
            did_app,
            [
                "register",
                "alice",
                "--network",
                "mock",
                "--vault-dir",
                str(vault_root),
                "--json",
            ],
        )
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)
        assert data["handle"] == "alice"
        assert data["network"] == "mock"


# ── resolve ──────────────────────────────────────────────────────────────────


class TestResolveCmd:
    def test_resolve_by_did(self, runner: CliRunner, vault_root: Path) -> None:
        runner.invoke(
            did_app,
            ["register", "alice", "--network", "mock", "--vault-dir", str(vault_root)],
        )
        r = runner.invoke(
            did_app,
            ["resolve", "did:sisoul:alice", "--vault-dir", str(vault_root)],
        )
        assert r.exit_code == 0, r.output
        assert "alice.sisoul.eth" in r.output

    def test_resolve_by_ens(self, runner: CliRunner, vault_root: Path) -> None:
        runner.invoke(
            did_app,
            ["register", "alice", "--network", "mock", "--vault-dir", str(vault_root)],
        )
        r = runner.invoke(
            did_app,
            ["resolve", "alice.sisoul.eth", "--vault-dir", str(vault_root)],
        )
        assert r.exit_code == 0, r.output
        assert "did:sisoul:alice" in r.output

    def test_resolve_not_found(self, runner: CliRunner, vault_root: Path) -> None:
        r = runner.invoke(
            did_app,
            ["resolve", "did:sisoul:ghost", "--vault-dir", str(vault_root)],
        )
        assert r.exit_code == 1
        assert "未找到" in r.output or "找不到" in r.output or "无 handle" in r.output

    def test_resolve_document_json(self, runner: CliRunner, vault_root: Path) -> None:
        runner.invoke(
            did_app,
            ["register", "alice", "--network", "mock", "--vault-dir", str(vault_root)],
        )
        r = runner.invoke(
            did_app,
            [
                "resolve",
                "alice.sisoul.eth",
                "--vault-dir",
                str(vault_root),
                "--document",
            ],
        )
        assert r.exit_code == 0, r.output
        doc = json.loads(r.output)
        assert doc["id"] == "did:sisoul:alice"
        assert "@context" in doc


# ── list ─────────────────────────────────────────────────────────────────────


class TestListCmd:
    def test_list_empty(self, runner: CliRunner, vault_root: Path) -> None:
        r = runner.invoke(did_app, ["list", "--vault-dir", str(vault_root)])
        assert r.exit_code == 0
        assert "无已注册" in r.output

    def test_list_after_register(self, runner: CliRunner, vault_root: Path) -> None:
        for name in ("alice", "bob"):
            runner.invoke(
                did_app,
                ["register", name, "--network", "mock", "--vault-dir", str(vault_root)],
            )
        r = runner.invoke(did_app, ["list", "--vault-dir", str(vault_root)])
        assert r.exit_code == 0, r.output
        assert "alice" in r.output
        assert "bob" in r.output


# ── link-friend ──────────────────────────────────────────────────────────────


class TestLinkFriendCmd:
    def test_link_no_local_did(self, runner: CliRunner, vault_root: Path) -> None:
        r = runner.invoke(
            did_app,
            ["link-friend", "did:sisoul:bob", "--vault-dir", str(vault_root)],
        )
        assert r.exit_code == 1
        assert "本地无 DID" in r.output

    def test_link_basic(self, runner: CliRunner, vault_root: Path) -> None:
        runner.invoke(
            did_app,
            ["register", "alice", "--network", "mock", "--vault-dir", str(vault_root)],
        )
        r = runner.invoke(
            did_app,
            ["link-friend", "did:sisoul:bob", "--vault-dir", str(vault_root)],
        )
        assert r.exit_code == 0, r.output
        assert "朋友关系记录已加" in r.output

    def test_link_bad_format(self, runner: CliRunner, vault_root: Path) -> None:
        runner.invoke(
            did_app,
            ["register", "alice", "--network", "mock", "--vault-dir", str(vault_root)],
        )
        r = runner.invoke(
            did_app,
            ["link-friend", "garbage", "--vault-dir", str(vault_root)],
        )
        assert r.exit_code == 1
        assert "格式" in r.output

    def test_link_with_as_handle(self, runner: CliRunner, vault_root: Path) -> None:
        for name in ("alice", "bob"):
            runner.invoke(
                did_app,
                ["register", name, "--network", "mock", "--vault-dir", str(vault_root)],
            )
        r = runner.invoke(
            did_app,
            [
                "link-friend",
                "did:sisoul:charlie",
                "--as",
                "bob",
                "--vault-dir",
                str(vault_root),
            ],
        )
        assert r.exit_code == 0, r.output
        assert "did:sisoul:bob" in r.output

    def test_link_unknown_as_handle(
        self, runner: CliRunner, vault_root: Path
    ) -> None:
        runner.invoke(
            did_app,
            ["register", "alice", "--network", "mock", "--vault-dir", str(vault_root)],
        )
        r = runner.invoke(
            did_app,
            [
                "link-friend",
                "did:sisoul:bob",
                "--as",
                "ghost",
                "--vault-dir",
                str(vault_root),
            ],
        )
        assert r.exit_code == 1
        assert "ghost" in r.output


# ── link-social ──────────────────────────────────────────────────────────────


class TestLinkSocialCmd:
    def test_github_with_token(self, runner: CliRunner) -> None:
        r = runner.invoke(did_app, ["link-social", "github", "--token", "gh_abc"])
        assert r.exit_code == 0, r.output
        assert "github" in r.output
        assert "embedded wallet" in r.output
        assert "0x" in r.output

    def test_email_with_email(self, runner: CliRunner) -> None:
        r = runner.invoke(
            did_app, ["link-social", "email", "--email", "alice@example.com"]
        )
        assert r.exit_code == 0, r.output
        assert "email" in r.output

    def test_email_missing(self, runner: CliRunner) -> None:
        r = runner.invoke(did_app, ["link-social", "email"])
        assert r.exit_code == 1

    def test_invalid_provider(self, runner: CliRunner) -> None:
        r = runner.invoke(did_app, ["link-social", "myspace", "--token", "x"])
        assert r.exit_code == 1

    def test_json_output(self, runner: CliRunner) -> None:
        r = runner.invoke(
            did_app, ["link-social", "github", "--token", "gh_abc", "--json"]
        )
        assert r.exit_code == 0, r.output
        data = json.loads(r.output)
        assert data["provider"] == "github"
        assert data["embedded_wallet_address"].startswith("0x")
