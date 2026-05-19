"""tests for sisoul.daemon_routes.friend (统一 friend_router · 波 5 dev-D).

只测 dev-D 自己 endpoints + 整合 helper.
dev-A friend_relationship_router 由 dev-A test 覆盖.
dev-B / dev-C 子 router 未 ship 时 try-include 优雅 degrade (不阻塞).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.friend.borrow import (
    ProxyResult,
    _reset_proxy_sessions_for_test,
    set_mock_proxy,
)


@pytest.fixture
def app() -> FastAPI:
    # 注: import 时 dev-A relationship 模块如未 ship 会让 daemon_routes.friend import 炸.
    # 实测 dev-A 已 ship (friend_relationship_router 在同文件内 ship), 所以可 import.
    from sisoul.daemon_routes.friend import friend_router

    a = FastAPI()
    a.include_router(friend_router)
    return a


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def tmp_paths(tmp_path: Path) -> dict[str, str]:
    return {
        "lend_db": str(tmp_path / "lend.db"),
        "pending_file": str(tmp_path / "pending.json"),
        "ledger_db": str(tmp_path / "ledger.db"),
    }


@pytest.fixture(autouse=True)
def _clean() -> None:
    set_mock_proxy(None)
    _reset_proxy_sessions_for_test()
    yield
    set_mock_proxy(None)
    _reset_proxy_sessions_for_test()


# ── /sisoul/borrow ──────────────────────────────────────────────────────────


class TestBorrowEndpoint:
    def test_borrow_strong_tie(self, client: TestClient,
                                  tmp_paths: dict[str, str]) -> None:
        r = client.post("/sisoul/borrow", json={
            "borrower_did": "alice.eth",
            "lender_did": "bob.eth",
            "resource_type": "llm_quota",
            "amount": 100,
            "model": "claude-opus-4-7",
            "force_mode": "strong-tie-auto",
            "enqueue_onchain": False,
            **tmp_paths,
        })
        assert r.status_code == 200, r.text
        sess = r.json()["session"]
        assert sess["status"] == "completed"
        assert sess["ledger_entry_id"]

    def test_borrow_invalid_force_mode(self, client: TestClient,
                                          tmp_paths: dict[str, str]) -> None:
        r = client.post("/sisoul/borrow", json={
            "borrower_did": "a", "lender_did": "b",
            "amount": 1, "model": "x",
            "force_mode": "weird",
            **tmp_paths,
        })
        assert r.status_code == 400


# ── /sisoul/lend/* ──────────────────────────────────────────────────────────


class TestLendEndpoints:
    def test_request_approve_flow(self, client: TestClient,
                                      tmp_paths: dict[str, str]) -> None:
        r1 = client.post("/sisoul/lend/request", json={
            "borrower_did": "alice.eth",
            "lender_did": "bob.eth",
            "amount": 100,
            "model": "x",
            "mode": "per-request",
            **tmp_paths,
        })
        assert r1.status_code == 200, r1.text
        req = r1.json()["request"]
        assert req["status"] == "pending"

        r2 = client.get("/sisoul/lend/pending", params={
            "lend_db": tmp_paths["lend_db"],
            "pending_file": tmp_paths["pending_file"],
        })
        assert r2.json()["count"] == 1

        r3 = client.post("/sisoul/lend/approve", json={
            "request_id": req["id"],
            **tmp_paths,
        })
        assert r3.json()["request"]["status"] == "approved"

    def test_approve_not_found(self, client: TestClient,
                                  tmp_paths: dict[str, str]) -> None:
        r = client.post("/sisoul/lend/approve", json={
            "request_id": "lr_nope", **tmp_paths,
        })
        assert r.status_code == 404

    def test_deny(self, client: TestClient, tmp_paths: dict[str, str]) -> None:
        r1 = client.post("/sisoul/lend/request", json={
            "borrower_did": "a.eth", "lender_did": "b.eth",
            "amount": 1, "model": "x", "mode": "per-request",
            **tmp_paths,
        })
        rid = r1.json()["request"]["id"]
        r2 = client.post("/sisoul/lend/deny", json={
            "request_id": rid, "reason": "no thx", **tmp_paths,
        })
        assert r2.json()["request"]["status"] == "denied"
        assert r2.json()["request"]["denied_reason"] == "no thx"

    def test_invalid_mode_400(self, client: TestClient,
                                tmp_paths: dict[str, str]) -> None:
        r = client.post("/sisoul/lend/request", json={
            "borrower_did": "a", "lender_did": "b",
            "amount": 1, "model": "x", "mode": "weird",
            **tmp_paths,
        })
        assert r.status_code == 400

    def test_all_endpoint(self, client: TestClient,
                            tmp_paths: dict[str, str]) -> None:
        client.post("/sisoul/lend/request", json={
            "borrower_did": "a.eth", "lender_did": "b.eth",
            "amount": 1, "model": "x", "mode": "strong-tie-auto",
            **tmp_paths,
        })
        r = client.get("/sisoul/lend/all", params={
            "lend_db": tmp_paths["lend_db"],
            "pending_file": tmp_paths["pending_file"],
        })
        assert r.status_code == 200
        assert r.json()["count"] >= 1


# ── /sisoul/ledger/* ────────────────────────────────────────────────────────


class TestLedgerEndpoints:
    def test_ledger_friend_balance(self, client: TestClient,
                                       tmp_paths: dict[str, str]) -> None:
        # 先 seed: 通过 borrow 走完整流程写 ledger
        client.post("/sisoul/borrow", json={
            "borrower_did": "alice.eth",
            "lender_did": "bob.eth",
            "resource_type": "llm_quota",
            "amount": 1000,
            "model": "claude-opus-4-7",
            "force_mode": "strong-tie-auto",
            "enqueue_onchain": False,
            **tmp_paths,
        })
        r = client.get("/sisoul/ledger/bob.eth", params={
            "self_did": "alice.eth",
            "ledger_db": tmp_paths["ledger_db"],
        })
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["balance"]["borrowed_total"] >= 1  # 至少 1 个 token
        assert len(data["entries"]) >= 1

    def test_imbalance_endpoint(self, client: TestClient,
                                   tmp_paths: dict[str, str]) -> None:
        from sisoul.friend.ledger import ReciprocityLedger

        led = ReciprocityLedger(db_path=tmp_paths["ledger_db"], self_did="alice.eth")
        try:
            led.record_usage("alice.eth", "bob.eth", "llm_quota", 10000, "x",
                             enqueue_onchain=False)
            led.record_usage("bob.eth", "alice.eth", "llm_quota", 100, "x",
                             enqueue_onchain=False)
        finally:
            led.close()

        r = client.get("/sisoul/ledger/imbalance", params={
            "self_did": "alice.eth",
            "ledger_db": tmp_paths["ledger_db"],
        })
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 1
        assert any(w["friend_did"] == "bob.eth" for w in data["warnings"])

    def test_stats_endpoint(self, client: TestClient,
                                tmp_paths: dict[str, str]) -> None:
        r = client.get("/sisoul/ledger/stats", params={
            "ledger_db": tmp_paths["ledger_db"],
        })
        assert r.status_code == 200
        assert "stats" in r.json()


# ── /sisoul/borrow-proxy/* ─────────────────────────────────────────────────


class TestProxySessionEndpoints:
    def test_lifecycle(self, client: TestClient) -> None:
        r1 = client.post("/sisoul/borrow-proxy/start", json={
            "borrower_did": "alice.eth",
            "lender_did": "bob.eth",
            "model": "claude-opus-4-7",
        })
        assert r1.status_code == 200
        sid = r1.json()["session"]["session_id"]

        r2 = client.get(f"/sisoul/borrow-proxy/{sid}")
        assert r2.json()["session"]["session_id"] == sid

        r3 = client.get("/sisoul/borrow-proxy/list")
        assert r3.json()["count"] >= 1

        r4 = client.post(f"/sisoul/borrow-proxy/{sid}/stop")
        assert r4.json()["session"]["status"] == "stopped"

    def test_stop_not_found(self, client: TestClient) -> None:
        r = client.post("/sisoul/borrow-proxy/ps_nope/stop")
        assert r.status_code == 404

    def test_get_not_found(self, client: TestClient) -> None:
        r = client.get("/sisoul/borrow-proxy/ps_nope")
        assert r.status_code == 404


# ── friend_router export 形状 ───────────────────────────────────────────────


def test_friend_router_export_only_one_router() -> None:
    from sisoul.daemon_routes.friend import friend_router

    paths = {route.path for route in friend_router.routes}  # type: ignore[attr-defined]
    # dev-D 必有路径
    assert "/sisoul/borrow" in paths
    assert "/sisoul/lend/approve" in paths
    assert "/sisoul/ledger/imbalance" in paths
    assert "/sisoul/borrow-proxy/start" in paths
