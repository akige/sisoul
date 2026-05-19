"""tests for sisoul.daemon_routes.attest — FastAPI TestClient (波 4 dev-B).

覆盖:
- POST /sisoul/audit hook 接收 + 入队
- GET /sisoul/attest/queue
- POST /sisoul/attest/flush mock + mainnet 403 + empty 409
- GET /sisoul/attest/history local + onchain mock
- GET /sisoul/attest/verify/{uid} valid + 404 path
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.attest import attest_router, audit_router
from sisoul.onchain.eas import (
    AttestConfig,
    AttestQueue,
    AuditAttestation,
    save_config,
    upload_batch,
)


@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(attest_router)
    a.include_router(audit_router)
    return a


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


@pytest.fixture
def tmp_cfg(tmp_path: Path) -> Path:
    return tmp_path / "cfg.json"


def _seed(tmp_db: Path, n: int = 3) -> list[AuditAttestation]:
    out = []
    with AttestQueue(db_path=tmp_db) as q:
        for i in range(n):
            att = AuditAttestation.from_audit_payload(
                "did:sisoul:alice", "rm", f"/tmp/f{i}", f"p-{i}", "claude-code"
            )
            q.enqueue(att)
            out.append(att)
    return out


# ── POST /sisoul/audit ───────────────────────────────────────────────────────


class TestAuditEndpoint:
    def test_audit_minimal(
        self, client: TestClient, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock", batch_size=100), tmp_cfg)
        r = client.post(
            "/sisoul/audit",
            json={
                "action_type": "rm",
                "target": "/tmp/x",
                "prompt": "rm -rf /tmp/x",
                "tool_name": "claude-code",
                "actor_did": "did:sisoul:alice",
                "queue_db": str(tmp_db),
                "config_path": str(tmp_cfg),
                "auto_flush": False,
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["queue_id"]
        assert data["actor_did"] == "did:sisoul:alice"
        assert data["auto_flushed"] is False

    def test_audit_auto_flush_triggers(
        self, client: TestClient, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        """batch_size=1 → 第 1 条入队就触发 flush."""
        save_config(AttestConfig(network="mock", batch_size=1), tmp_cfg)
        r = client.post(
            "/sisoul/audit",
            json={
                "action_type": "git-push",
                "target": "origin/main",
                "prompt": "git push --force",
                "tool_name": "codex",
                "actor_did": "did:sisoul:bob",
                "queue_db": str(tmp_db),
                "config_path": str(tmp_cfg),
                "auto_flush": True,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["auto_flushed"] is True
        assert data["batch_uid"]
        assert data["tx_hash"].startswith("0x")

    def test_audit_no_did_fallback_unknown(
        self, client: TestClient, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        """没显式 actor_did 也没本地 registry → fail-open 用 unknown."""
        save_config(
            AttestConfig(network="mock", batch_size=100, attester_did=None), tmp_cfg
        )
        with patch(
            "sisoul.daemon_routes.attest.resolve_attester_did",
            side_effect=__import__("sisoul.onchain.eas", fromlist=["EASError"]).EASError("no did"),
        ):
            r = client.post(
                "/sisoul/audit",
                json={
                    "action_type": "rm",
                    "target": "/x",
                    "prompt": "p",
                    "tool_name": "t",
                    "queue_db": str(tmp_db),
                    "config_path": str(tmp_cfg),
                    "auto_flush": False,
                },
            )
        assert r.status_code == 200
        assert r.json()["actor_did"] == "did:sisoul:unknown"

    def test_audit_missing_required_field(self, client: TestClient) -> None:
        r = client.post(
            "/sisoul/audit",
            json={"prompt": "no action"},  # 缺 action_type / target
        )
        assert r.status_code == 422


# ── GET /sisoul/attest/queue ─────────────────────────────────────────────────


class TestQueueEndpoint:
    def test_queue_empty(self, client: TestClient, tmp_db: Path) -> None:
        r = client.get("/sisoul/attest/queue", params={"queue_db": str(tmp_db)})
        assert r.status_code == 200
        data = r.json()
        assert data["stats"]["pending"] == 0
        assert data["items"] == []

    def test_queue_lists(self, client: TestClient, tmp_db: Path) -> None:
        _seed(tmp_db, n=2)
        r = client.get("/sisoul/attest/queue", params={"queue_db": str(tmp_db)})
        assert r.status_code == 200
        data = r.json()
        assert data["stats"]["pending"] == 2
        assert len(data["items"]) == 2
        assert data["items"][0]["action_type"] == "rm"

    def test_queue_status_all(self, client: TestClient, tmp_db: Path) -> None:
        _seed(tmp_db, n=1)
        r = client.get(
            "/sisoul/attest/queue",
            params={"queue_db": str(tmp_db), "status": "all"},
        )
        assert r.status_code == 200

    def test_queue_bad_status(self, client: TestClient, tmp_db: Path) -> None:
        r = client.get(
            "/sisoul/attest/queue",
            params={"queue_db": str(tmp_db), "status": "bogus"},
        )
        assert r.status_code == 400


# ── POST /sisoul/attest/flush ────────────────────────────────────────────────


class TestFlushEndpoint:
    def test_flush_empty_409(
        self, client: TestClient, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock"), tmp_cfg)
        r = client.post(
            "/sisoul/attest/flush",
            json={"queue_db": str(tmp_db), "config_path": str(tmp_cfg)},
        )
        assert r.status_code == 409
        assert "无 pending" in r.json()["detail"]

    def test_flush_success(
        self, client: TestClient, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock", batch_size=3), tmp_cfg)
        _seed(tmp_db, n=3)
        r = client.post(
            "/sisoul/attest/flush",
            json={"queue_db": str(tmp_db), "config_path": str(tmp_cfg)},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["count"] == 3
        assert data["network"] == "mock"
        assert data["method"] == "mock"
        assert len(data["attestation_uids"]) == 3
        assert data["gas_used_estimate"] > 0

    def test_flush_mainnet_403(
        self, client: TestClient, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="optimism-mainnet"), tmp_cfg)
        _seed(tmp_db, n=1)
        r = client.post(
            "/sisoul/attest/flush",
            json={"queue_db": str(tmp_db), "config_path": str(tmp_cfg)},
        )
        assert r.status_code == 403


# ── GET /sisoul/attest/history ───────────────────────────────────────────────


class TestHistoryEndpoint:
    def test_history_local_empty(
        self, client: TestClient, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock"), tmp_cfg)
        r = client.get(
            "/sisoul/attest/history",
            params={
                "queue_db": str(tmp_db),
                "config_path": str(tmp_cfg),
                "source": "local",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["source"] == "local"
        assert data["items"] == []

    def test_history_local_after_batch(
        self, client: TestClient, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock", batch_size=2), tmp_cfg)
        _seed(tmp_db, n=2)
        with AttestQueue(db_path=tmp_db) as q:
            upload_batch(q, AttestConfig(network="mock", batch_size=2))
        r = client.get(
            "/sisoul/attest/history",
            params={
                "queue_db": str(tmp_db),
                "config_path": str(tmp_cfg),
                "source": "local",
            },
        )
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["count"] == 2

    def test_history_onchain_mock(
        self, client: TestClient, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock"), tmp_cfg)
        r = client.get(
            "/sisoul/attest/history",
            params={
                "queue_db": str(tmp_db),
                "config_path": str(tmp_cfg),
                "source": "onchain",
            },
        )
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_history_bad_source_400(
        self, client: TestClient, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock"), tmp_cfg)
        r = client.get(
            "/sisoul/attest/history",
            params={
                "queue_db": str(tmp_db),
                "config_path": str(tmp_cfg),
                "source": "elsewhere",
            },
        )
        assert r.status_code == 400

    def test_history_onchain_mainnet_403(
        self, client: TestClient, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="optimism-mainnet"), tmp_cfg)
        r = client.get(
            "/sisoul/attest/history",
            params={"config_path": str(tmp_cfg), "source": "onchain"},
        )
        assert r.status_code == 403


# ── GET /sisoul/attest/verify/{uid} ──────────────────────────────────────────


class TestVerifyEndpoint:
    def test_verify_not_found(
        self, client: TestClient, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock"), tmp_cfg)
        r = client.get(
            "/sisoul/attest/verify/0xNOPE",
            params={"queue_db": str(tmp_db), "config_path": str(tmp_cfg)},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["local"]["valid"] is False

    def test_verify_valid(
        self, client: TestClient, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="mock", batch_size=1), tmp_cfg)
        _seed(tmp_db, n=1)
        with AttestQueue(db_path=tmp_db) as q:
            res = upload_batch(q, AttestConfig(network="mock", batch_size=1))
            uid = res.attestation_uids[0]
        r = client.get(
            f"/sisoul/attest/verify/{uid}",
            params={"queue_db": str(tmp_db), "config_path": str(tmp_cfg)},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["uid"] == uid
        assert data["local"]["valid"] is True
        assert data["onchain"] is None  # 默认不查链上

    def test_verify_onchain_mainnet_403(
        self, client: TestClient, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        save_config(AttestConfig(network="optimism-mainnet"), tmp_cfg)
        r = client.get(
            "/sisoul/attest/verify/0xabc",
            params={
                "queue_db": str(tmp_db),
                "config_path": str(tmp_cfg),
                "onchain": True,
            },
        )
        assert r.status_code == 403


# ── 集成 smoke: 整链 audit → queue → flush → verify ──────────────────────────


class TestE2E:
    def test_full_pipeline_audit_to_verify(
        self, client: TestClient, tmp_db: Path, tmp_cfg: Path
    ) -> None:
        """波 2 hook → audit → queue 自动 flush → verify 全链路."""
        save_config(AttestConfig(network="mock", batch_size=1), tmp_cfg)

        # 1. hook 发 audit
        r1 = client.post(
            "/sisoul/audit",
            json={
                "action_type": "chmod",
                "target": "/etc/passwd",
                "prompt": "chmod 777 /etc/passwd",
                "tool_name": "claude-code",
                "actor_did": "did:sisoul:alice",
                "queue_db": str(tmp_db),
                "config_path": str(tmp_cfg),
                "auto_flush": True,
            },
        )
        assert r1.status_code == 200
        assert r1.json()["auto_flushed"] is True
        batch_uid = r1.json()["batch_uid"]

        # 2. 历史里有这个 batch
        r2 = client.get(
            "/sisoul/attest/history",
            params={
                "queue_db": str(tmp_db),
                "config_path": str(tmp_cfg),
                "source": "local",
            },
        )
        assert r2.status_code == 200
        items = r2.json()["items"]
        assert any(it["batch_uid"] == batch_uid for it in items)

        # 3. queue 已清 pending
        r3 = client.get(
            "/sisoul/attest/queue", params={"queue_db": str(tmp_db)}
        )
        assert r3.json()["stats"]["pending"] == 0
        assert r3.json()["stats"]["confirmed"] == 1

        # 4. confirmed item 取出 attestation_uid → verify
        r4_all = client.get(
            "/sisoul/attest/queue",
            params={"queue_db": str(tmp_db), "status": "confirmed"},
        )
        uid = r4_all.json()["items"][0]["attestation_uid"]
        assert uid

        r5 = client.get(
            f"/sisoul/attest/verify/{uid}",
            params={"queue_db": str(tmp_db), "config_path": str(tmp_cfg)},
        )
        assert r5.status_code == 200
        assert r5.json()["local"]["valid"] is True
