"""tests · cli_commands.snapshot (5 命令 mock 流程)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli_commands.snapshot import snapshot_app
from sisoul.identity.seed import generate_mnemonic

runner = CliRunner()


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / "preferences").mkdir(parents=True)
    (v / "preferences" / "a.md").write_text("# a\n", encoding="utf-8")
    return v


@pytest.fixture(autouse=True)
def isolate_home_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """每个 test 拿独立 HOME + seed file + history path, 不污染真用户 ~/.sisoul."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # 让 ArweaveSnapshot._derive_encryption_key 走 SISOUL_MNEMONIC env
    monkeypatch.setenv("SISOUL_MNEMONIC", generate_mnemonic(128))
    monkeypatch.delenv("PINATA_JWT", raising=False)
    monkeypatch.delenv("ARWEAVE_WALLET", raising=False)
    monkeypatch.delenv("ARWEAVE_ALLOW_MAINNET", raising=False)
    # Path.home() 在 macOS 不一定看 HOME, 直接 monkey patch
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: fake_home))
    # DEFAULT_HISTORY_PATH 在 import 时 resolve 了, 每个 test 独立 patch 到 tmp
    import sisoul.onchain.arweave as arw_mod
    import sisoul.cli_commands.snapshot as cli_mod

    fake_history = tmp_path / "snapshot_history.json"
    monkeypatch.setattr(arw_mod, "DEFAULT_HISTORY_PATH", fake_history)
    # CONFIG_PATH 同样隔离 (test_config_* 会写)
    monkeypatch.setattr(cli_mod, "CONFIG_PATH", tmp_path / "snapshot_config.json")


def test_now_mock_network(vault: Path) -> None:
    result = runner.invoke(
        snapshot_app,
        ["now", "--upload", "both", "--network", "mock", "--vault-dir", str(vault)],
    )
    assert result.exit_code == 0, result.output
    assert "snapshot" in result.output.lower()
    assert "mockcid-" in result.output
    assert "mocktx-" in result.output


def test_now_vault_missing(tmp_path: Path) -> None:
    result = runner.invoke(
        snapshot_app,
        ["now", "--vault-dir", str(tmp_path / "noexist"), "--network", "mock"],
    )
    assert result.exit_code == 1
    assert "vault 不存在" in result.output


def test_now_invalid_upload(vault: Path) -> None:
    result = runner.invoke(
        snapshot_app,
        ["now", "--upload", "garbage", "--vault-dir", str(vault), "--network", "mock"],
    )
    assert result.exit_code == 2
    assert "--upload" in result.output


def test_list_empty() -> None:
    result = runner.invoke(snapshot_app, ["list"])
    assert result.exit_code == 0
    assert "无 snapshot" in result.output


def test_list_after_now(vault: Path) -> None:
    runner.invoke(
        snapshot_app, ["now", "--upload", "both", "--network", "mock", "--vault-dir", str(vault)]
    )
    result = runner.invoke(snapshot_app, ["list"])
    assert result.exit_code == 0, result.output
    # table header
    assert "timestamp" in result.output


def test_list_json_format(vault: Path) -> None:
    runner.invoke(
        snapshot_app, ["now", "--upload", "ipfs", "--network", "mock", "--vault-dir", str(vault)]
    )
    result = runner.invoke(snapshot_app, ["list", "--format", "json"])
    assert result.exit_code == 0
    # 应该是 JSON array
    parsed = json.loads(result.output)
    assert isinstance(parsed, list) and len(parsed) >= 1
    assert parsed[0]["ipfs_cid"].startswith("mockcid-")


def test_schedule_monthly_dryrun() -> None:
    result = runner.invoke(snapshot_app, ["schedule", "--monthly", "--upload", "both"])
    assert result.exit_code == 0
    assert "monthly" in result.output
    # 不带 --install, 应该不写文件 (但有 unit_text 输出)
    assert "unit text" in result.output


def test_schedule_conflicting_flags() -> None:
    result = runner.invoke(snapshot_app, ["schedule", "--monthly", "--weekly"])
    assert result.exit_code == 2


def test_schedule_never() -> None:
    result = runner.invoke(snapshot_app, ["schedule", "--never"])
    assert result.exit_code == 0


def test_config_show_default() -> None:
    result = runner.invoke(snapshot_app, ["config", "--show"])
    assert result.exit_code == 0
    assert "config path" in result.output
    assert "ARWEAVE_ALLOW_MAINNET" in result.output


def test_config_set_pinata_jwt(tmp_path: Path) -> None:
    result = runner.invoke(snapshot_app, ["config", "--set-pinata-jwt", "test-jwt-xxxxxxxxxx"])
    assert result.exit_code == 0
    # show 应该脱敏
    result2 = runner.invoke(snapshot_app, ["config", "--show"])
    assert result2.exit_code == 0
    assert "test-jwt" in result2.output
    # 不应明文出完整
    assert "test-jwt-xxxxxxxxxx" not in result2.output


def test_config_clear() -> None:
    runner.invoke(snapshot_app, ["config", "--set-pinata-jwt", "xx"])
    result = runner.invoke(snapshot_app, ["config", "--clear"])
    assert result.exit_code == 0
    assert "已清空" in result.output


def test_restore_unknown_hash_fails(vault: Path) -> None:
    # 64-char hex 但不在 history
    fake_hash = "f" * 64
    result = runner.invoke(
        snapshot_app,
        [
            "restore", fake_hash,
            "--target", str(vault.parent / "restored"),
            "--network", "mock",
        ],
    )
    assert result.exit_code == 1
    assert "没找到" in result.output or "history" in result.output


def test_restore_invalid_source(vault: Path) -> None:
    result = runner.invoke(
        snapshot_app,
        [
            "restore", "Qm-anything",
            "--target", str(vault.parent / "r2"),
            "--source", "badsrc",
        ],
    )
    assert result.exit_code == 2
