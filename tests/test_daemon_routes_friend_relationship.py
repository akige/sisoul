"""Tests for sisoul.daemon_routes.friend (波 5 dev-A).

FastAPI TestClient 覆盖 dev-A ship 的 9 endpoints (request / receive / accept /
confirm-mutual / list / requests / revoke / info / score/manual).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.friend import friend_relationship_router


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(friend_relationship_router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def alice_dbs(tmp_path: Path) -> dict[str, str]:
    return {
        "own_did": "did:sisoul:alice",
        "friend_db": str(tmp_path / "friends.db"),
        "attest_queue_db": str(tmp_path / "attest.db"),
    }


class TestRequestEndpoint:
    def test_post_request_ok(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        r = client.post(
            "/sisoul/friend/request",
            json={"target_did": "did:sisoul:bob", "message": "hi", **alice_dbs},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["target_did"] == "did:sisoul:bob"
        assert d["direction"] == "outbound"
        assert d["attestation_uid"]
        assert d["status"] == "pending"

    def test_post_request_to_self_400(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        r = client.post(
            "/sisoul/friend/request",
            json={"target_did": "did:sisoul:alice", **alice_dbs},
        )
        assert r.status_code == 400


class TestReceiveEndpoint:
    def test_receive_creates_inbound(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        r = client.post(
            "/sisoul/friend/receive",
            json={
                "requester_did": "did:sisoul:bob",
                "message": "hi",
                "attestation_uid": "0x_bob_req",
                **alice_dbs,
            },
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["direction"] == "inbound"
        assert d["requester_did"] == "did:sisoul:bob"
        assert d["target_did"] == "did:sisoul:alice"


class TestAcceptEndpoint:
    def test_accept_inbound(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        # setup: receive 进一条
        rcv = client.post(
            "/sisoul/friend/receive",
            json={
                "requester_did": "did:sisoul:bob",
                "attestation_uid": "0x_bob_req",
                **alice_dbs,
            },
        )
        req_id = rcv.json()["request_id"]
        r = client.post(
            "/sisoul/friend/accept",
            json={"request_id": req_id, **alice_dbs},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "active"
        assert d["accept_attestation_uid"]

    def test_accept_unknown_404(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        r = client.post(
            "/sisoul/friend/accept",
            json={"request_id": "nonexistent", **alice_dbs},
        )
        assert r.status_code == 404


class TestConfirmMutualEndpoint:
    def test_confirm_mutual(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        # setup: receive + accept
        rcv = client.post(
            "/sisoul/friend/receive",
            json={"requester_did": "did:sisoul:bob", **alice_dbs},
        ).json()
        client.post(
            "/sisoul/friend/accept",
            json={"request_id": rcv["request_id"], **alice_dbs},
        )
        r = client.post(
            "/sisoul/friend/confirm-mutual",
            json={
                "friend_did": "did:sisoul:bob",
                "mutual_attestation_uid": "0x_bob_accept",
                **alice_dbs,
            },
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["is_mutual"] is True
        assert d["mutual_attestation_uid"] == "0x_bob_accept"

    def test_confirm_unknown_404(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        r = client.post(
            "/sisoul/friend/confirm-mutual",
            json={
                "friend_did": "did:sisoul:nobody",
                "mutual_attestation_uid": "0x_x",
                **alice_dbs,
            },
        )
        assert r.status_code == 404


class TestListEndpoint:
    def test_list_empty(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        r = client.get(
            "/sisoul/friend/list",
            params={k: v for k, v in alice_dbs.items()},
        )
        assert r.status_code == 200
        assert r.json() == []

    def test_list_after_request(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        client.post(
            "/sisoul/friend/request",
            json={"target_did": "did:sisoul:bob", **alice_dbs},
        )
        r = client.get("/sisoul/friend/list", params=alice_dbs)
        assert r.status_code == 200
        d = r.json()
        assert len(d) == 1
        assert d[0]["did"] == "did:sisoul:bob"
        assert d[0]["status"] == "pending"

    def test_list_filter_status(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        client.post(
            "/sisoul/friend/request",
            json={"target_did": "did:sisoul:bob", **alice_dbs},
        )
        r = client.get(
            "/sisoul/friend/list",
            params={**alice_dbs, "status": "active"},
        )
        assert r.json() == []
        r = client.get(
            "/sisoul/friend/list",
            params={**alice_dbs, "status": "pending"},
        )
        assert len(r.json()) == 1

    def test_list_invalid_status_400(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        r = client.get(
            "/sisoul/friend/list",
            params={**alice_dbs, "status": "garbage"},
        )
        assert r.status_code == 400


class TestRequestsEndpoint:
    def test_list_inbound(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        client.post(
            "/sisoul/friend/receive",
            json={"requester_did": "did:sisoul:bob", **alice_dbs},
        )
        r = client.get(
            "/sisoul/friend/requests",
            params={**alice_dbs, "direction": "inbound"},
        )
        assert r.status_code == 200
        d = r.json()
        assert len(d) == 1
        assert d[0]["direction"] == "inbound"


class TestRevokeEndpoint:
    def test_revoke(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        client.post(
            "/sisoul/friend/request",
            json={"target_did": "did:sisoul:bob", **alice_dbs},
        )
        r = client.post(
            "/sisoul/friend/revoke",
            json={"did": "did:sisoul:bob", **alice_dbs},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "revoked"
        assert d["revoke_attestation_uid"]

    def test_revoke_unknown_404(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        r = client.post(
            "/sisoul/friend/revoke",
            json={"did": "did:sisoul:nobody", **alice_dbs},
        )
        assert r.status_code == 404


class TestInfoEndpoint:
    def test_info(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        client.post(
            "/sisoul/friend/request",
            json={"target_did": "did:sisoul:bob", **alice_dbs},
        )
        r = client.get(
            "/sisoul/friend/info/did:sisoul:bob",
            params={k: v for k, v in alice_dbs.items() if k != "attest_queue_db"},
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["friend"]["did"] == "did:sisoul:bob"
        assert "score_breakdown" in d
        assert "ledger_summary" in d
        # ledger_summary 由 dev-D ship; 本测试只验字段存在 (兼容 dev-D ship 前/后)
        assert "available" in d["ledger_summary"]

    def test_info_unknown_404(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        r = client.get(
            "/sisoul/friend/info/did:sisoul:nobody",
            params={k: v for k, v in alice_dbs.items() if k != "attest_queue_db"},
        )
        assert r.status_code == 404


class TestManualScoreEndpoint:
    def test_set_then_clear(
        self, client: TestClient, alice_dbs: dict[str, str]
    ) -> None:
        rcv = client.post(
            "/sisoul/friend/receive",
            json={"requester_did": "did:sisoul:bob", **alice_dbs},
        ).json()
        client.post(
            "/sisoul/friend/accept",
            json={"request_id": rcv["request_id"], **alice_dbs},
        )
        r = client.post(
            "/sisoul/friend/score/manual",
            json={"did": "did:sisoul:bob", "score": 9.0, **alice_dbs},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["manual_score_override"] == 9.0
        assert d["strong_tie_score"] == 9.0

        # clear
        r2 = client.post(
            "/sisoul/friend/score/manual",
            json={"did": "did:sisoul:bob", "score": None, **alice_dbs},
        )
        assert r2.status_code == 200
        assert r2.json()["manual_score_override"] is None
