"""集成 test: 同机 2 sisoul DID 模拟 friend 完整生命周期 (波 5 dev-A).

模拟 Alice 与 Bob 在同一机器 (但各自独立 vault / friend_db / attest_queue):
1. Alice 注册 DID:alice (复用波 3 dev-B)
2. Bob   注册 DID:bob
3. Alice → Bob FRIEND_REQUEST (走 daemon HTTP API)
4. Bob daemon 收到 inbound (本 wave: 手工 POST /receive 模拟 P2P 推送)
5. Bob accept → 产生 Bob FRIEND_ACCEPT attestation
6. Alice 也收到 inbound (Bob 的 FRIEND_ACCEPT 由 P2P 推回) → Alice accept (产生 Alice attest)
7. 双向 confirm-mutual: 各自把对方 accept_uid 写进 mutual_attestation_uid
8. 双方 GET /list 都看到对方 active + mutual
9. 双方 GET /info 看到 strong_tie_score >= 1.0 (base 1.0)

注意: M4 真 daemon 双实例由 qa-E 在 G3 阶段做; 这里用 TestClient + 2 router instance
模拟双 daemon 即可, 验证业务路径完整 (不验真 HTTP server bind 端口).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.friend import friend_relationship_router
from sisoul.identity.did import register_did


@pytest.fixture
def alice_setup(tmp_path: Path) -> dict[str, str]:
    """Alice vault + DID registry."""
    vault = tmp_path / "alice_vault"
    (vault / "identity").mkdir(parents=True, exist_ok=True)
    registry = vault / "identity" / "dids.json"
    register_did("alice", network="mock", registry_path=registry)
    return {
        "vault_dir": str(vault),
        "friend_db": str(tmp_path / "alice_friends.db"),
        "attest_queue_db": str(tmp_path / "alice_attest.db"),
    }


@pytest.fixture
def bob_setup(tmp_path: Path) -> dict[str, str]:
    vault = tmp_path / "bob_vault"
    (vault / "identity").mkdir(parents=True, exist_ok=True)
    registry = vault / "identity" / "dids.json"
    register_did("bob", network="mock", registry_path=registry)
    return {
        "vault_dir": str(vault),
        "friend_db": str(tmp_path / "bob_friends.db"),
        "attest_queue_db": str(tmp_path / "bob_attest.db"),
    }


@pytest.fixture
def alice_client() -> TestClient:
    """Alice 的 daemon (独立 FastAPI instance, 但同进程模拟)."""
    app = FastAPI()
    app.include_router(friend_relationship_router)
    return TestClient(app)


@pytest.fixture
def bob_client() -> TestClient:
    app = FastAPI()
    app.include_router(friend_relationship_router)
    return TestClient(app)


class TestTwoDIDLifecycle:
    def test_full_friend_lifecycle(
        self,
        alice_setup: dict[str, str],
        bob_setup: dict[str, str],
        alice_client: TestClient,
        bob_client: TestClient,
    ) -> None:
        # ─── 1. Alice → Bob FRIEND_REQUEST ──────────────────────────────────
        r = alice_client.post(
            "/sisoul/friend/request",
            json={
                "target_did": "did:sisoul:bob",
                "message": "加个朋友, 想借你 Claude Opus quota",
                **alice_setup,
            },
        )
        assert r.status_code == 200, r.text
        alice_req = r.json()
        assert alice_req["requester_did"] == "did:sisoul:alice"
        alice_request_attestation_uid = alice_req["attestation_uid"]
        assert alice_request_attestation_uid

        # ─── 2. Bob daemon 收到 inbound (P2P 推送 → /receive 模拟) ─────────
        r = bob_client.post(
            "/sisoul/friend/receive",
            json={
                "requester_did": "did:sisoul:alice",
                "message": "加个朋友, 想借你 Claude Opus quota",
                "attestation_uid": alice_request_attestation_uid,
                **bob_setup,
            },
        )
        assert r.status_code == 200, r.text
        bob_inbound = r.json()
        assert bob_inbound["direction"] == "inbound"
        bob_request_id = bob_inbound["request_id"]

        # ─── 3. Bob accept → 产生 Bob 的 FRIEND_ACCEPT attestation ────────
        r = bob_client.post(
            "/sisoul/friend/accept",
            json={"request_id": bob_request_id, **bob_setup},
        )
        assert r.status_code == 200, r.text
        bob_friend = r.json()
        assert bob_friend["status"] == "active"
        bob_accept_attestation_uid = bob_friend["accept_attestation_uid"]
        assert bob_accept_attestation_uid
        # Bob 此时还没收到 Alice 的 FRIEND_ACCEPT, 单边 mutual=False
        assert bob_friend["is_mutual"] is False

        # ─── 4. Alice daemon 收到 inbound Bob FRIEND_ACCEPT (P2P 推回) ────
        #     设计上 Alice 也得 accept 一次, 产生 Alice FRIEND_ACCEPT attestation
        r = alice_client.post(
            "/sisoul/friend/receive",
            json={
                "requester_did": "did:sisoul:bob",
                "attestation_uid": bob_accept_attestation_uid,
                **alice_setup,
            },
        )
        assert r.status_code == 200, r.text
        alice_inbound_req_id = r.json()["request_id"]

        r = alice_client.post(
            "/sisoul/friend/accept",
            json={"request_id": alice_inbound_req_id, **alice_setup},
        )
        assert r.status_code == 200, r.text
        alice_friend = r.json()
        alice_accept_attestation_uid = alice_friend["accept_attestation_uid"]
        assert alice_accept_attestation_uid
        assert alice_friend["status"] == "active"
        assert alice_friend["is_mutual"] is False  # 还没 confirm

        # ─── 5. 双向 confirm mutual (对方 accept_uid → 本端 mutual_uid) ──
        r = alice_client.post(
            "/sisoul/friend/confirm-mutual",
            json={
                "friend_did": "did:sisoul:bob",
                "mutual_attestation_uid": bob_accept_attestation_uid,
                **alice_setup,
            },
        )
        assert r.status_code == 200, r.text
        alice_friend_mutual = r.json()
        assert alice_friend_mutual["is_mutual"] is True
        assert alice_friend_mutual["strong_tie_score"] >= 1.0

        r = bob_client.post(
            "/sisoul/friend/confirm-mutual",
            json={
                "friend_did": "did:sisoul:alice",
                "mutual_attestation_uid": alice_accept_attestation_uid,
                **bob_setup,
            },
        )
        assert r.status_code == 200, r.text
        bob_friend_mutual = r.json()
        assert bob_friend_mutual["is_mutual"] is True
        assert bob_friend_mutual["strong_tie_score"] >= 1.0

        # ─── 6. 双方 GET /list 都看到对方 active + mutual ──────────────────
        alice_list_params = {
            k: v for k, v in alice_setup.items() if k != "attest_queue_db"
        }
        bob_list_params = {
            k: v for k, v in bob_setup.items() if k != "attest_queue_db"
        }
        r = alice_client.get(
            "/sisoul/friend/list",
            params={**alice_list_params, "status": "active"},
        )
        assert r.status_code == 200
        alice_list = r.json()
        assert len(alice_list) == 1
        assert alice_list[0]["did"] == "did:sisoul:bob"
        assert alice_list[0]["is_mutual"] is True

        r = bob_client.get(
            "/sisoul/friend/list",
            params={**bob_list_params, "status": "active"},
        )
        assert r.status_code == 200
        bob_list = r.json()
        assert len(bob_list) == 1
        assert bob_list[0]["did"] == "did:sisoul:alice"
        assert bob_list[0]["is_mutual"] is True

        # ─── 7. 双方 GET /info 看到 strong_tie_score 细分 ───────────────────
        r = alice_client.get(
            "/sisoul/friend/info/did:sisoul:bob",
            params=alice_list_params,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["friend"]["is_mutual"] is True
        assert d["score_breakdown"]["base"] == 1.0
        # mutual 但刚 active → score 约 1.0 (months_score ~ 0)
        assert d["score_breakdown"]["total"] >= 1.0
        # ledger 由 dev-D ship; 本测试只验字段存在 (兼容 dev-D ship 前/后)
        assert "available" in d["ledger_summary"]

        # ─── 8. Alice revoke Bob → 双向解除 (Bob 那边 confirm 通过 P2P) ───
        r = alice_client.post(
            "/sisoul/friend/revoke",
            json={"did": "did:sisoul:bob", **alice_setup},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "revoked"
        assert d["revoke_attestation_uid"]
        assert d["strong_tie_score"] == 0.0


class TestTwoDIDIndependence:
    """Alice / Bob 的 friend_db 完全独立, 不应有任何 cross-contamination."""

    def test_alice_revoke_does_not_affect_bob_local(
        self,
        alice_setup: dict[str, str],
        bob_setup: dict[str, str],
        alice_client: TestClient,
        bob_client: TestClient,
    ) -> None:
        # setup mutual
        alice_client.post(
            "/sisoul/friend/request",
            json={"target_did": "did:sisoul:bob", **alice_setup},
        )
        bob_inbound = bob_client.post(
            "/sisoul/friend/receive",
            json={"requester_did": "did:sisoul:alice", **bob_setup},
        ).json()
        bob_friend = bob_client.post(
            "/sisoul/friend/accept",
            json={"request_id": bob_inbound["request_id"], **bob_setup},
        ).json()

        # Alice revoke
        alice_client.post(
            "/sisoul/friend/revoke",
            json={"did": "did:sisoul:bob", **alice_setup},
        )

        # Bob 本地 cache 仍是 active (链上 REVOKE 推送 + Bob daemon 收到才更新)
        # 本 wave: 验证 Bob 本地未受 alice 直接影响 (链上同步是另一回事)
        bob_list_params = {
            k: v for k, v in bob_setup.items() if k != "attest_queue_db"
        }
        r = bob_client.get("/sisoul/friend/list", params=bob_list_params)
        bob_list = r.json()
        # Bob 仍能看到 Alice (是 active 状态, 因 Bob 这边的 attestation 没 revoke)
        assert len(bob_list) == 1
        assert bob_list[0]["did"] == "did:sisoul:alice"
        assert bob_list[0]["status"] == "active"


class TestEASQueueIsolation:
    """Alice / Bob 各自的 EAS queue 独立 (各自 attest_queue_db 路径不同)."""

    def test_attest_queues_isolated(
        self,
        alice_setup: dict[str, str],
        bob_setup: dict[str, str],
        alice_client: TestClient,
        bob_client: TestClient,
    ) -> None:
        from sisoul.onchain.eas import AttestQueue

        # Alice 发 request, Bob 也独立发一个 request
        alice_client.post(
            "/sisoul/friend/request",
            json={"target_did": "did:sisoul:bob", **alice_setup},
        )
        bob_client.post(
            "/sisoul/friend/request",
            json={"target_did": "did:sisoul:charlie", **bob_setup},
        )

        # Alice queue 只有 alice 发的 attestation
        with AttestQueue(db_path=Path(alice_setup["attest_queue_db"])) as q:
            items = q.all_items(status="pending", limit=50)
            assert len(items) == 1
            assert items[0].actor_did == "did:sisoul:alice"
            assert items[0].target == "did:sisoul:bob"

        # Bob queue 只有 bob 发的
        with AttestQueue(db_path=Path(bob_setup["attest_queue_db"])) as q:
            items = q.all_items(status="pending", limit=50)
            assert len(items) == 1
            assert items[0].actor_did == "did:sisoul:bob"
            assert items[0].target == "did:sisoul:charlie"
