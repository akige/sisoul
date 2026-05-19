"""tests for sisoul.daemon_routes.permissions · FastAPI TestClient (波 5 dev-C).

覆盖 6 endpoints:
- GET  /sisoul/perms/list
- POST /sisoul/perms/set
- POST /sisoul/perms/revoke
- POST /sisoul/perms/check  (核心: dev-D borrow 内部调)
- GET  /sisoul/perms/reputation
- GET  /sisoul/perms/scan-log
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.permissions import permissions_router
from sisoul.friend.anti_abuse import scan_request_pattern
from sisoul.friend.permissions import (
    FriendPermission,
    LLMQuotaShare,
    load_permissions,
    save_permissions,
)


@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(permissions_router)
    return a


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def tmp_perms(tmp_path: Path) -> Path:
    p = tmp_path / "friends"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── /list ────────────────────────────────────────────────────────────────────


class TestList:
    def test_empty(self, client: TestClient, tmp_perms: Path) -> None:
        r = client.get("/sisoul/perms/list", params={"perms_dir": str(tmp_perms)})
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_with_friends(self, client: TestClient, tmp_perms: Path) -> None:
        save_permissions(
            "did:alice",
            FriendPermission(
                friend_did="did:alice",
                llm_quota_share=LLMQuotaShare(enabled=True, mode="strong-tie-auto"),
            ),
            perms_dir=tmp_perms,
        )
        r = client.get("/sisoul/perms/list", params={"perms_dir": str(tmp_perms)})
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        assert data["friends"][0]["friend"] == "did:alice"

    def test_friend_filter_404(self, client: TestClient, tmp_perms: Path) -> None:
        r = client.get(
            "/sisoul/perms/list",
            params={"perms_dir": str(tmp_perms), "friend": "did:nobody"},
        )
        assert r.status_code == 404


# ── /set ─────────────────────────────────────────────────────────────────────


class TestSet:
    def test_set_creates(self, client: TestClient, tmp_perms: Path) -> None:
        r = client.post(
            "/sisoul/perms/set",
            json={
                "friend_did": "did:alice",
                "llm_quota_share": {
                    "enabled": True,
                    "mode": "strong-tie-auto",
                    "monthly_token_cap": 500_000,
                    "rate_limit": 10,
                    "models": ["claude-opus-4-7"],
                },
                "perms_dir": str(tmp_perms),
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["friend"] == "did:alice"
        assert body["llm_quota_share"]["monthly_token_cap"] == 500_000

    def test_set_bad_mode_422(self, client: TestClient, tmp_perms: Path) -> None:
        r = client.post(
            "/sisoul/perms/set",
            json={
                "friend_did": "did:alice",
                "llm_quota_share": {
                    "enabled": True,
                    "mode": "BAD",
                },
                "perms_dir": str(tmp_perms),
            },
        )
        assert r.status_code == 422

    def test_set_patches_existing(
        self, client: TestClient, tmp_perms: Path
    ) -> None:
        save_permissions(
            "did:alice",
            FriendPermission(
                friend_did="did:alice",
                llm_quota_share=LLMQuotaShare(enabled=True, mode="per-request"),
            ),
            perms_dir=tmp_perms,
        )
        r = client.post(
            "/sisoul/perms/set",
            json={
                "friend_did": "did:alice",
                "ai_skill_share": {
                    "enabled": True,
                    "mode": "per-request",
                    "skills": ["solidity-expert"],
                },
                "perms_dir": str(tmp_perms),
            },
        )
        assert r.status_code == 200
        p = load_permissions("did:alice", perms_dir=tmp_perms)
        assert p.llm_quota_share.mode == "per-request"  # 不动
        assert p.ai_skill_share.skills == ["solidity-expert"]


# ── /revoke ──────────────────────────────────────────────────────────────────


class TestRevoke:
    def test_revoke(self, client: TestClient, tmp_perms: Path) -> None:
        save_permissions(
            "did:alice",
            FriendPermission(friend_did="did:alice"),
            perms_dir=tmp_perms,
        )
        r = client.post(
            "/sisoul/perms/revoke",
            json={
                "friend_did": "did:alice",
                "reason": "abuse",
                "perms_dir": str(tmp_perms),
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["revoked"] is True
        assert body["friend_did"] == "did:alice"
        p = load_permissions("did:alice", perms_dir=tmp_perms)
        assert p.revoked


# ── /check (dev-D borrow 调) ─────────────────────────────────────────────────


class TestCheck:
    def test_check_strong_tie_allowed(
        self, client: TestClient, tmp_perms: Path
    ) -> None:
        save_permissions(
            "did:alice",
            FriendPermission(
                friend_did="did:alice",
                llm_quota_share=LLMQuotaShare(
                    enabled=True, mode="strong-tie-auto", monthly_token_cap=1000
                ),
            ),
            perms_dir=tmp_perms,
        )
        r = client.post(
            "/sisoul/perms/check",
            json={
                "friend_did": "did:alice",
                "resource_type": "llm_quota",
                "amount": 100,
                "perms_dir": str(tmp_perms),
            },
        )
        assert r.status_code == 200
        assert r.json()["allowed"] is True

    def test_check_revoked(
        self, client: TestClient, tmp_perms: Path
    ) -> None:
        save_permissions(
            "did:alice",
            FriendPermission(
                friend_did="did:alice",
                llm_quota_share=LLMQuotaShare(enabled=True, mode="strong-tie-auto"),
                revoked=True,
                revoked_reason="x",
            ),
            perms_dir=tmp_perms,
        )
        r = client.post(
            "/sisoul/perms/check",
            json={
                "friend_did": "did:alice",
                "resource_type": "llm_quota",
                "amount": 100,
                "perms_dir": str(tmp_perms),
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["allowed"] is False
        assert "revoked" in body["reason"]

    def test_check_invalid_resource_422(
        self, client: TestClient, tmp_perms: Path
    ) -> None:
        r = client.post(
            "/sisoul/perms/check",
            json={
                "friend_did": "did:x",
                "resource_type": "BAD",
                "amount": 10,
                "perms_dir": str(tmp_perms),
            },
        )
        assert r.status_code == 422

    def test_check_no_config(
        self, client: TestClient, tmp_perms: Path
    ) -> None:
        r = client.post(
            "/sisoul/perms/check",
            json={
                "friend_did": "did:nobody",
                "resource_type": "llm_quota",
                "amount": 10,
                "perms_dir": str(tmp_perms),
            },
        )
        assert r.status_code == 200
        assert r.json()["allowed"] is False
        assert "no_permission_config" in r.json()["reason"]


# ── /reputation ──────────────────────────────────────────────────────────────


class TestReputation:
    def test_basic(self, client: TestClient) -> None:
        r = client.get(
            "/sisoul/perms/reputation",
            params={
                "did": "did:x",
                "borrows": 50,
                "lends": 50,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["did"] == "did:x"
        assert body["score"] == 120
        assert body["grade"] == "B"

    def test_publish_uses_publisher_or_returns_none(
        self, client: TestClient
    ) -> None:
        # 真 onchain 调可能失败 (无 EAS network), publish_reputation_attestation 内部 fail-open
        r = client.get(
            "/sisoul/perms/reputation",
            params={"did": "did:x", "publish": "true"},
        )
        assert r.status_code == 200
        body = r.json()
        # attestation_queue_id 可能是 str 也可能 None (取决于 EAS 是否成功 enqueue)
        assert "attestation_queue_id" in body


# ── /scan-log ────────────────────────────────────────────────────────────────


class TestScanLog:
    def test_empty(self, client: TestClient, tmp_path: Path) -> None:
        r = client.get(
            "/sisoul/perms/scan-log",
            params={"scan_db": str(tmp_path / "s.db")},
        )
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_with_blocks(self, client: TestClient, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        scan_request_pattern(
            {
                "friend_did": "did:alice",
                "amount": 999_999_999,
                "model": "x",
                "prompt_hash": "h",
            },
            persist_db=db,
        )
        r = client.get(
            "/sisoul/perms/scan-log", params={"scan_db": str(db)}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["count"] >= 1
        assert "token_burst" in body["events"][0]["reason"]
