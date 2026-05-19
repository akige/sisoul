"""tests for sisoul attest CLI (波 4 dev-B).

5 命令 (queue/flush/history/verify/config) happy + error path.

注: attest_app 是 Typer subapp, 测试时直接构造一个临时 root app 挂上, 不污染 cli.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import typer
from typer.testing import CliRunner

from sisoul.cli_commands.attest import attest_app
from sisoul.onchain.eas import (
    AttestConfig,
    AttestQueue,
    AuditAttestation,
    save_config,
    upload_batch,
)


runner = CliRunner()


@pytest.fixture
def app() -> typer.Typer:
    root = typer.Typer()
    root.add_typer(attest_app, name="attest")
    return root


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


@pytest.fixture
def tmp_cfg(tmp_path: Path) -> Path:
    return tmp_path / "config.json"


def _seed_queue(db: Path, n: int = 3) -> list[AuditAttestation]:
    out = []
    with AttestQueue(db_path=db) as q:
        for i in range(n):
            att = AuditAttestation.from_audit_payload(
                "did:sisoul:alice", "rm", f"/tmp/f{i}", f"prompt-{i}", "claude-code"
            )
            q.enqueue(att)
            out.append(att)
    return out


# ── attest queue ─────────────────────────────────────────────────────────────


class TestQueueCmd:
    def test_queue_empty(self, app: typer.Typer, tmp_db: Path) -> None:
        r = runner.invoke(app, ["attest", "queue", "--queue-db", str(tmp_db)])
        assert r.exit_code == 0
        assert "无 pending 项" in r.output or "pending=0" in r.output

    def test_queue_lists_items(self, app: typer.Typer, tmp_db: Path) -> None:
        _seed_queue(tmp_db, n=3)
        r = runner.invoke(app, ["attest", "queue", "--queue-db", str(tmp_db)])
        assert r.exit_code == 0
        assert "pending=3" in r.output
        assert "/tmp/f0" in r.output

    def test_queue_json(self, app: typer.Typer, tmp_db: Path) -> None:
        _seed_queue(tmp_db, n=2)
        r = runner.invoke(
            app, ["attest", "queue", "--queue-db", str(tmp_db), "--json"]
        )
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["stats"]["pending"] == 2
        assert len(data["items"]) == 2

    def test_queue_status_all(self, app: typer.Typer, tmp_db: Path) -> None:
        _seed_queue(tmp_db, n=1)
        r = runner.invoke(
            app, ["attest", "queue", "--queue-db", str(tmp_db), "--status", "all"]
        )
        assert r.exit_code == 0


# ── attest flush ─────────────────────────────────────────────────────────────


class TestFlushCmd:
    def test_flush_empty_queue(
        self, app: typer.Typer, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock"), tmp_cfg)
        r = runner.invoke(
            app,
            [
                "attest", "flush",
                "--queue-db", str(tmp_db),
                "--config", str(tmp_cfg),
            ],
        )
        assert r.exit_code == 1
        assert "queue 无 pending" in r.output

    def test_flush_mock_success(
        self, app: typer.Typer, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock", batch_size=3), tmp_cfg)
        _seed_queue(tmp_db, n=3)
        r = runner.invoke(
            app,
            [
                "attest", "flush",
                "--queue-db", str(tmp_db),
                "--config", str(tmp_cfg),
            ],
        )
        assert r.exit_code == 0
        assert "✅ batch 已上链" in r.output
        assert "count:     3" in r.output
        assert "mock" in r.output

    def test_flush_mainnet_rejected(
        self, app: typer.Typer, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="optimism-mainnet"), tmp_cfg)
        _seed_queue(tmp_db, n=1)
        r = runner.invoke(
            app,
            [
                "attest", "flush",
                "--queue-db", str(tmp_db),
                "--config", str(tmp_cfg),
            ],
        )
        assert r.exit_code == 3
        assert "mainnet" in r.output

    def test_flush_force_takes_all(
        self, app: typer.Typer, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock", batch_size=2), tmp_cfg)
        _seed_queue(tmp_db, n=5)
        r = runner.invoke(
            app,
            [
                "attest", "flush",
                "--queue-db", str(tmp_db),
                "--config", str(tmp_cfg),
                "--force",
            ],
        )
        assert r.exit_code == 0
        assert "count:     5" in r.output

    def test_flush_json(
        self, app: typer.Typer, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock", batch_size=2), tmp_cfg)
        _seed_queue(tmp_db, n=2)
        r = runner.invoke(
            app,
            [
                "attest", "flush",
                "--queue-db", str(tmp_db),
                "--config", str(tmp_cfg),
                "--json",
            ],
        )
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["count"] == 2
        assert data["network"] == "mock"


# ── attest history ───────────────────────────────────────────────────────────


class TestHistoryCmd:
    def test_history_local_empty(
        self, app: typer.Typer, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock"), tmp_cfg)
        r = runner.invoke(
            app,
            [
                "attest", "history",
                "--queue-db", str(tmp_db),
                "--config", str(tmp_cfg),
                "--source", "local",
            ],
        )
        assert r.exit_code == 0
        assert "本地无 batch" in r.output

    def test_history_local_after_batch(
        self, app: typer.Typer, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock", batch_size=2), tmp_cfg)
        _seed_queue(tmp_db, n=2)
        with AttestQueue(db_path=tmp_db) as q:
            upload_batch(q, AttestConfig(network="mock", batch_size=2))
        r = runner.invoke(
            app,
            [
                "attest", "history",
                "--queue-db", str(tmp_db),
                "--config", str(tmp_cfg),
                "--source", "local",
            ],
        )
        assert r.exit_code == 0
        assert "batch_uid" in r.output

    def test_history_onchain_mock_empty(
        self, app: typer.Typer, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock"), tmp_cfg)
        r = runner.invoke(
            app,
            [
                "attest", "history",
                "--queue-db", str(tmp_db),
                "--config", str(tmp_cfg),
                "--source", "onchain",
            ],
        )
        assert r.exit_code == 0
        assert "链上无" in r.output or "mock" in r.output

    def test_history_bad_source(
        self, app: typer.Typer, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock"), tmp_cfg)
        r = runner.invoke(
            app,
            [
                "attest", "history",
                "--queue-db", str(tmp_db),
                "--config", str(tmp_cfg),
                "--source", "badsrc",
            ],
        )
        assert r.exit_code == 1

    def test_history_onchain_mainnet_rejected(
        self, app: typer.Typer, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="optimism-mainnet"), tmp_cfg)
        r = runner.invoke(
            app,
            [
                "attest", "history",
                "--queue-db", str(tmp_db),
                "--config", str(tmp_cfg),
                "--source", "onchain",
            ],
        )
        assert r.exit_code == 3
        assert "mainnet" in r.output


# ── attest verify ────────────────────────────────────────────────────────────


class TestVerifyCmd:
    def test_verify_not_found(
        self, app: typer.Typer, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock"), tmp_cfg)
        r = runner.invoke(
            app,
            [
                "attest", "verify", "0xNOPE",
                "--queue-db", str(tmp_db),
                "--config", str(tmp_cfg),
            ],
        )
        assert r.exit_code == 1
        assert "valid:  False" in r.output

    def test_verify_valid(
        self, app: typer.Typer, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock", batch_size=1), tmp_cfg)
        _seed_queue(tmp_db, n=1)
        with AttestQueue(db_path=tmp_db) as q:
            r = upload_batch(q, AttestConfig(network="mock", batch_size=1))
            uid = r.attestation_uids[0]
        out = runner.invoke(
            app,
            [
                "attest", "verify", uid,
                "--queue-db", str(tmp_db),
                "--config", str(tmp_cfg),
            ],
        )
        assert out.exit_code == 0
        assert "valid:  True" in out.output

    def test_verify_json(
        self, app: typer.Typer, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock", batch_size=1), tmp_cfg)
        _seed_queue(tmp_db, n=1)
        with AttestQueue(db_path=tmp_db) as q:
            r = upload_batch(q, AttestConfig(network="mock", batch_size=1))
            uid = r.attestation_uids[0]
        out = runner.invoke(
            app,
            [
                "attest", "verify", uid,
                "--queue-db", str(tmp_db),
                "--config", str(tmp_cfg),
                "--json",
            ],
        )
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data["uid"] == uid
        assert data["local"]["valid"] is True


# ── attest config ────────────────────────────────────────────────────────────


class TestConfigCmd:
    def test_config_show_default(
        self, app: typer.Typer, tmp_cfg: Path
    ) -> None:
        r = runner.invoke(
            app, ["attest", "config", "--config", str(tmp_cfg), "--show"]
        )
        assert r.exit_code == 0
        assert "optimism-sepolia" in r.output
        assert "batch_size" in r.output

    def test_config_set_network(
        self, app: typer.Typer, tmp_cfg: Path
    ) -> None:
        r = runner.invoke(
            app,
            [
                "attest", "config",
                "--config", str(tmp_cfg),
                "--set-network", "mock",
                "--set-batch-size", "5",
                "--set-rpc", "https://example.com",
            ],
        )
        assert r.exit_code == 0
        assert "config 已保存" in r.output
        data = json.loads(tmp_cfg.read_text())
        assert data["network"] == "mock"
        assert data["batch_size"] == 5
        assert data["rpc_url"] == "https://example.com"

    def test_config_invalid_network(
        self, app: typer.Typer, tmp_cfg: Path
    ) -> None:
        r = runner.invoke(
            app,
            [
                "attest", "config",
                "--config", str(tmp_cfg),
                "--set-network", "ethereum-mainnet",
            ],
        )
        assert r.exit_code == 1
        assert "network 必须是" in r.output

    def test_config_invalid_batch_size(
        self, app: typer.Typer, tmp_cfg: Path
    ) -> None:
        r = runner.invoke(
            app,
            [
                "attest", "config",
                "--config", str(tmp_cfg),
                "--set-batch-size", "0",
            ],
        )
        assert r.exit_code == 1

    def test_config_json_output(
        self, app: typer.Typer, tmp_cfg: Path
    ) -> None:
        r = runner.invoke(
            app, ["attest", "config", "--config", str(tmp_cfg), "--show", "--json"]
        )
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["network"] == "optimism-sepolia"
