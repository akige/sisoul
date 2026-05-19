"""tests for sisoul.daemon_routes.did (Phase 2 W21-W22, dev-B).

用 FastAPI TestClient 不真起 server, 验 4 个 endpoint 行为.
覆盖 happy path + 反向 (400 / 403 / 404).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.did import did_router


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "sisoul-test"


@pytest.fixture
def client() -> TestClient:
    """独立 FastAPI app 仅挂 did_router (避免依赖主 daemon.py 整合)."""
    app = FastAPI()
    app.include_router(did_router)
    return TestClient(app)


# ── GET /sisoul/did ─────────────────────────────────────────────────────────


class TestGetCurrent:
    def test_no_did_returns_has_false(self, client: TestClient, vault_root: Path) -> None:
        r = client.get("/sisoul/did", params={"vault_dir": str(vault_root)})
        assert r.status_code == 200
        body = r.json()
        assert body["has_did"] is False
        assert body["count"] == 0
        assert body["default"] is None

    def test_after_register_returns_default(
        self, client: TestClient, vault_root: Path
    ) -> None:
        client.post(
            "/sisoul/did/register",
            json={"handle": "alice", "network": "mock", "vault_dir": str(vault_root)},
        )
        r = client.get("/sisoul/did", params={"vault_dir": str(vault_root)})
        body = r.json()
        assert body["has_did"] is True
        assert body["count"] == 1
        assert body["default"]["handle"] == "alice"
        assert body["default"]["ens"] == "alice.sisoul.eth"


# ── POST /sisoul/did/register ───────────────────────────────────────────────


class TestRegister:
    def test_basic(self, client: TestClient, vault_root: Path) -> None:
        r = client.post(
            "/sisoul/did/register",
            json={"handle": "alice", "network": "mock", "vault_dir": str(vault_root)},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["did"] == "did:sisoul:alice"
        assert body["ens"] == "alice.sisoul.eth"
        assert body["network"] == "mock"
        assert body["public_key"]
        assert body["ens_tx_hash"]

    def test_invalid_handle_400(self, client: TestClient, vault_root: Path) -> None:
        r = client.post(
            "/sisoul/did/register",
            json={"handle": "a@b", "network": "mock", "vault_dir": str(vault_root)},
        )
        assert r.status_code == 400
        assert "非法字符" in r.json()["detail"] or "handle" in r.json()["detail"].lower()

    def test_mainnet_forbidden_403(
        self, client: TestClient, vault_root: Path
    ) -> None:
        r = client.post(
            "/sisoul/did/register",
            json={
                "handle": "alice",
                "network": "mainnet",
                "vault_dir": str(vault_root),
            },
        )
        assert r.status_code == 403
        assert "mainnet" in r.json()["detail"]

    def test_duplicate_400(self, client: TestClient, vault_root: Path) -> None:
        body = {"handle": "alice", "network": "mock", "vault_dir": str(vault_root)}
        client.post("/sisoul/did/register", json=body)
        r2 = client.post("/sisoul/did/register", json=body)
        assert r2.status_code == 400

    def test_register_with_social(self, client: TestClient, vault_root: Path) -> None:
        r = client.post(
            "/sisoul/did/register",
            json={
                "handle": "alice",
                "network": "mock",
                "vault_dir": str(vault_root),
                "social_provider": "github",
                "social_id": "gh-user-1",
            },
        )
        assert r.status_code == 201, r.text


# ── POST /sisoul/did/resolve ────────────────────────────────────────────────


class TestResolve:
    def test_resolve_by_did(self, client: TestClient, vault_root: Path) -> None:
        client.post(
            "/sisoul/did/register",
            json={"handle": "alice", "network": "mock", "vault_dir": str(vault_root)},
        )
        r = client.post(
            "/sisoul/did/resolve",
            json={"target": "did:sisoul:alice", "vault_dir": str(vault_root)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["did"] == "did:sisoul:alice"
        assert body["controllers"] == ["did:sisoul:alice"]
        assert body["document"] is None

    def test_resolve_with_document(
        self, client: TestClient, vault_root: Path
    ) -> None:
        client.post(
            "/sisoul/did/register",
            json={"handle": "alice", "network": "mock", "vault_dir": str(vault_root)},
        )
        r = client.post(
            "/sisoul/did/resolve",
            json={
                "target": "alice.sisoul.eth",
                "vault_dir": str(vault_root),
                "include_document": True,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["document"] is not None
        assert body["document"]["id"] == "did:sisoul:alice"

    def test_resolve_404(self, client: TestClient, vault_root: Path) -> None:
        r = client.post(
            "/sisoul/did/resolve",
            json={"target": "did:sisoul:ghost", "vault_dir": str(vault_root)},
        )
        assert r.status_code == 404

    def test_resolve_bad_format_400(
        self, client: TestClient, vault_root: Path
    ) -> None:
        r = client.post(
            "/sisoul/did/resolve",
            json={"target": "garbage", "vault_dir": str(vault_root)},
        )
        assert r.status_code == 400


# ── GET /sisoul/did/list ────────────────────────────────────────────────────


class TestList:
    def test_empty(self, client: TestClient, vault_root: Path) -> None:
        r = client.get("/sisoul/did/list", params={"vault_dir": str(vault_root)})
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert body["items"] == []

    def test_after_register(self, client: TestClient, vault_root: Path) -> None:
        for name in ("alice", "bob"):
            client.post(
                "/sisoul/did/register",
                json={"handle": name, "network": "mock", "vault_dir": str(vault_root)},
            )
        r = client.get("/sisoul/did/list", params={"vault_dir": str(vault_root)})
        body = r.json()
        assert body["count"] == 2
        handles = {item["handle"] for item in body["items"]}
        assert handles == {"alice", "bob"}
