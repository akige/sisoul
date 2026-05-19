"""集成测试: 5 层防御跨场景 + revoke 链上即时生效 + scan 拦 (波 5 dev-C).

场景:
- enforce_all_layers 综合 5 层流程
- L1 配额过 + L2 rate 过 + L5 scan 过 → approved
- L3 revoke 后立即拒
- L5 scan 拦下 后 写 scan-log + 通过 CLI/router 能查
- 配额 reach + emergency_flag (借 reserve 通过)
- 模拟"daemon 内一连串 request" 真实跑 rate-limiter sliding window
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.permissions import permissions_router
from sisoul.friend.anti_abuse import (
    RateLimiter,
    clear_scan_log,
    enforce_all_layers,
    list_scan_log,
    revoke_friend_permission,
    scan_request_pattern,
)
from sisoul.friend.permissions import (
    FriendPermission,
    LLMQuotaShare,
    check_permission,
    load_permissions,
    register_usage_provider,
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


@pytest.fixture(autouse=True)
def reset_provider() -> None:
    register_usage_provider(None)  # type: ignore[arg-type]
    yield
    register_usage_provider(None)  # type: ignore[arg-type]


# ── 场景 1: 5 层全通 ────────────────────────────────────────────────────────


def test_all_layers_pass(tmp_perms: Path, tmp_path: Path) -> None:
    save_permissions(
        "did:alice",
        FriendPermission(
            friend_did="did:alice",
            llm_quota_share=LLMQuotaShare(
                enabled=True,
                mode="strong-tie-auto",
                monthly_token_cap=500_000,
                rate_limit=10,
            ),
        ),
        perms_dir=tmp_perms,
    )
    rl = RateLimiter()
    md = {
        "friend_did": "did:alice",
        "amount": 5_000,
        "resource_type": "llm_quota",
        "model": "claude-opus-4-7",
        "prompt_hash": "0xabc",
        "ts": time.time(),
    }
    allowed, reason, bd = enforce_all_layers(
        "did:alice",
        md,
        perms_dir=tmp_perms,
        rate_limiter=rl,
        current_usage=0,
        scan_db=tmp_path / "s.db",
    )
    assert allowed, (reason, bd)
    assert bd["L3_revoke"] is None
    assert bd["L1_cap"].startswith("ok")
    assert bd["L2_rate"] == "ok"


# ── 场景 2: L1 cap 拒 ──────────────────────────────────────────────────────


def test_l1_cap_blocks(tmp_perms: Path, tmp_path: Path) -> None:
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
    md = {
        "friend_did": "did:alice",
        "amount": 5000,
        "resource_type": "llm_quota",
        "prompt_hash": "0x",
        "ts": time.time(),
    }
    allowed, reason, bd = enforce_all_layers(
        "did:alice",
        md,
        perms_dir=tmp_perms,
        current_usage=0,
        scan_db=tmp_path / "s.db",
    )
    assert not allowed
    assert "L1_monthly_cap_exceeded" in reason


# ── 场景 3: L2 rate 拒 ─────────────────────────────────────────────────────


def test_l2_rate_blocks(tmp_perms: Path, tmp_path: Path) -> None:
    save_permissions(
        "did:alice",
        FriendPermission(
            friend_did="did:alice",
            llm_quota_share=LLMQuotaShare(
                enabled=True, mode="strong-tie-auto", rate_limit=2
            ),
        ),
        perms_dir=tmp_perms,
    )
    rl = RateLimiter()
    md = {
        "friend_did": "did:alice",
        "amount": 100,
        "resource_type": "llm_quota",
        "prompt_hash": "0x",
        "ts": time.time(),
    }
    # 跑 2 次都过
    for _ in range(2):
        allowed, _, _ = enforce_all_layers(
            "did:alice",
            md,
            perms_dir=tmp_perms,
            rate_limiter=rl,
            current_usage=0,
            scan_db=tmp_path / "s.db",
        )
        assert allowed
    # 第 3 次撞 rate
    allowed, reason, _ = enforce_all_layers(
        "did:alice",
        md,
        perms_dir=tmp_perms,
        rate_limiter=rl,
        current_usage=0,
        scan_db=tmp_path / "s.db",
    )
    assert not allowed
    assert "L2_rate_limit_exceeded" in reason


# ── 场景 4: L3 revoke 链上即时生效 ──────────────────────────────────────────


def test_l3_revoke_immediate_via_router(
    client: TestClient, tmp_perms: Path
) -> None:
    # 先建 perm
    client.post(
        "/sisoul/perms/set",
        json={
            "friend_did": "did:alice",
            "llm_quota_share": {
                "enabled": True,
                "mode": "strong-tie-auto",
                "monthly_token_cap": 1000,
            },
            "perms_dir": str(tmp_perms),
        },
    )
    # 借: 通过
    r = client.post(
        "/sisoul/perms/check",
        json={
            "friend_did": "did:alice",
            "resource_type": "llm_quota",
            "amount": 100,
            "perms_dir": str(tmp_perms),
        },
    )
    assert r.json()["allowed"] is True

    # revoke
    r = client.post(
        "/sisoul/perms/revoke",
        json={
            "friend_did": "did:alice",
            "reason": "abuse",
            "perms_dir": str(tmp_perms),
        },
    )
    assert r.status_code == 200

    # 再借: 立即拒
    r = client.post(
        "/sisoul/perms/check",
        json={
            "friend_did": "did:alice",
            "resource_type": "llm_quota",
            "amount": 100,
            "perms_dir": str(tmp_perms),
        },
    )
    assert r.json()["allowed"] is False
    assert "revoked" in r.json()["reason"]


# ── 场景 5: L5 scan 拦下 + scan-log 可查 (CLI/router 都行) ──────────────────


def test_l5_scan_then_query_via_router(
    client: TestClient, tmp_perms: Path, tmp_path: Path
) -> None:
    save_permissions(
        "did:alice",
        FriendPermission(
            friend_did="did:alice",
            llm_quota_share=LLMQuotaShare(
                enabled=True,
                mode="strong-tie-auto",
                monthly_token_cap=0,  # 无 cap, 让 L5 scan 接力拦
            ),
        ),
        perms_dir=tmp_perms,
    )
    db = tmp_path / "s.db"
    md = {
        "friend_did": "did:alice",
        "amount": 999_999_999,  # 超 L5 token_burst (默认 200k)
        "resource_type": "llm_quota",
        "prompt_hash": "0x",
        "ts": time.time(),
    }
    allowed, reason, bd = enforce_all_layers(
        "did:alice",
        md,
        perms_dir=tmp_perms,
        current_usage=0,
        scan_db=db,
    )
    assert not allowed
    assert "L5_" in reason
    # 通过 router 查 scan-log
    r = client.get(
        "/sisoul/perms/scan-log", params={"scan_db": str(db)}
    )
    assert r.status_code == 200
    assert r.json()["count"] >= 1


# ── 场景 6: 配额 reach + emergency_flag 借 reserve 通过 ─────────────────────


def test_emergency_reserve_via_check_permission(tmp_perms: Path) -> None:
    save_permissions(
        "did:alice",
        FriendPermission(
            friend_did="did:alice",
            llm_quota_share=LLMQuotaShare(
                enabled=True,
                mode="emergency-only",
                monthly_token_cap=1000,
                emergency_reserve_tokens=500,
            ),
        ),
        perms_dir=tmp_perms,
    )
    # cap reach (usage=1000) + 借 400 (<reserve) + emergency flag → 通过
    ok, reason = check_permission(
        "did:alice",
        "llm_quota",
        400,
        perms_dir=tmp_perms,
        emergency_flag=True,
        current_usage=1000,
    )
    assert ok, reason
    assert "emergency-reserve" in reason
    # 借 600 (>reserve) → 拒
    ok, reason = check_permission(
        "did:alice",
        "llm_quota",
        600,
        perms_dir=tmp_perms,
        emergency_flag=True,
        current_usage=1000,
    )
    assert not ok
    assert "emergency_reserve_exceeded" in reason


# ── 场景 7: 模拟 daemon 一连串 request 真跑滑动窗口 ────────────────────────


def test_rate_limiter_sliding_window_realtime(tmp_perms: Path, tmp_path: Path) -> None:
    save_permissions(
        "did:alice",
        FriendPermission(
            friend_did="did:alice",
            llm_quota_share=LLMQuotaShare(
                enabled=True, mode="strong-tie-auto", rate_limit=5
            ),
        ),
        perms_dir=tmp_perms,
    )
    rl = RateLimiter()
    md_template = {
        "friend_did": "did:alice",
        "amount": 100,
        "resource_type": "llm_quota",
        "prompt_hash": "0x",
    }
    pass_count = 0
    block_count = 0
    for _ in range(10):
        md = dict(md_template, ts=time.time())
        allowed, _, _ = enforce_all_layers(
            "did:alice",
            md,
            perms_dir=tmp_perms,
            rate_limiter=rl,
            current_usage=0,
            scan_db=tmp_path / "s.db",
        )
        if allowed:
            pass_count += 1
        else:
            block_count += 1
    # rate=5 → 5 pass, 5 block
    assert pass_count == 5
    assert block_count == 5


# ── 场景 8: ledger usage provider 注入 → check_permission 自动用 ───────────


def test_usage_provider_integration(tmp_perms: Path) -> None:
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

    # 模拟 dev-D ledger 提供 usage
    def provider(did: str, rt: str) -> int:
        return 950 if did == "did:alice" else 0

    register_usage_provider(provider)
    # 借 100: 950+100=1050 > 1000 → 拒
    ok, reason = check_permission(
        "did:alice", "llm_quota", 100, perms_dir=tmp_perms
    )
    assert not ok
    assert "monthly_cap_exceeded" in reason
    # 借 30: 950+30=980 < 1000 → 通过
    ok, reason = check_permission(
        "did:alice", "llm_quota", 30, perms_dir=tmp_perms
    )
    assert ok
