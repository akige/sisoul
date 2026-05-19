"""tests for sisoul.friend.anti_abuse · 5 层防御 (波 5 dev-C).

L1 月度配额 / L2 rate limit / L3 revoke (+ 链上 attestation) /
L4 reputation 算法 / L5 daemon 扫描 + scan log.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from sisoul.friend import permissions as P
from sisoul.friend.anti_abuse import (
    BALANCED_BONUS,
    IMBALANCE_PENALTY,
    RateLimiter,
    RecentRequest,
    ReputationScore,
    ScanThresholds,
    clear_scan_log,
    compute_reputation,
    enforce_monthly_cap,
    enforce_rate_limit,
    list_scan_log,
    publish_reputation_attestation,
    revoke_friend_permission,
    scan_request_pattern,
)
from sisoul.friend.permissions import (
    AISkillShare,
    FriendPermission,
    LLMQuotaShare,
    load_permissions,
)


# ── L1 月度配额 ─────────────────────────────────────────────────────────────


class TestL1MonthlyCap:
    def _perm(self, cap: int) -> FriendPermission:
        return FriendPermission(
            friend_did="did:x",
            llm_quota_share=LLMQuotaShare(
                enabled=True, mode="strong-tie-auto", monthly_token_cap=cap
            ),
        )

    def test_cap_zero_means_unlimited(self) -> None:
        assert enforce_monthly_cap(self._perm(0), 1_000_000_000, 9999) is True

    def test_under_cap(self) -> None:
        assert enforce_monthly_cap(self._perm(1000), 500, 400) is True

    def test_exact_cap(self) -> None:
        assert enforce_monthly_cap(self._perm(1000), 500, 500) is True

    def test_over_cap(self) -> None:
        assert enforce_monthly_cap(self._perm(1000), 500, 501) is False

    def test_non_llm_resource_skipped(self) -> None:
        # ai_skill / compute 无月度 cap (当前)
        assert enforce_monthly_cap(self._perm(100), 99999, 99999, "ai_skill") is True


# ── L2 rate limit ───────────────────────────────────────────────────────────


class TestL2RateLimit:
    def _perm(self, rate: int) -> FriendPermission:
        return FriendPermission(
            friend_did="did:x",
            llm_quota_share=LLMQuotaShare(
                enabled=True, mode="strong-tie-auto", rate_limit=rate
            ),
        )

    def test_rate_zero_means_unlimited(self) -> None:
        recent = [RecentRequest(ts=time.time()) for _ in range(1000)]
        assert enforce_rate_limit(self._perm(0), recent) is True

    def test_under_limit(self) -> None:
        recent = [RecentRequest(ts=time.time()) for _ in range(3)]
        assert enforce_rate_limit(self._perm(10), recent) is True

    def test_at_limit_next_request_denied(self) -> None:
        recent = [RecentRequest(ts=time.time()) for _ in range(10)]
        assert enforce_rate_limit(self._perm(10), recent) is False

    def test_old_requests_dropped_by_window(self) -> None:
        now = time.time()
        recent = [RecentRequest(ts=now - 120) for _ in range(20)]
        # 120s ago, window=60s → 全过期, +1 new → 1 <= 10
        assert enforce_rate_limit(self._perm(10), recent, window_sec=60, now=now) is True

    def test_rate_limiter_class(self) -> None:
        rl = RateLimiter()
        perm = self._perm(3)
        for _ in range(3):
            assert rl.check(perm, "did:x")
            rl.record("did:x", amount=100)
        # 4th should fail
        assert rl.check(perm, "did:x") is False

    def test_rate_limiter_per_friend_isolated(self) -> None:
        rl = RateLimiter()
        perm = self._perm(2)
        for _ in range(2):
            rl.record("did:a", amount=100)
        # a 撞顶, b 不受影响
        assert rl.check(perm, "did:a") is False
        assert rl.check(perm, "did:b") is True


# ── L3 revoke ───────────────────────────────────────────────────────────────


class TestL3Revoke:
    def test_revoke_marks_perm_and_returns_attestation_id(self, tmp_path: Path) -> None:
        # save initial perm
        from sisoul.friend.permissions import save_permissions

        save_permissions(
            "did:alice",
            FriendPermission(
                friend_did="did:alice",
                llm_quota_share=LLMQuotaShare(enabled=True, mode="strong-tie-auto"),
            ),
            perms_dir=tmp_path,
        )
        out = revoke_friend_permission(
            "did:alice",
            reason="abuse-detected",
            perms_dir=tmp_path,
            onchain_publisher=lambda did, r: f"queue:{did}",
        )
        assert out["revoked"] is True
        assert out["friend_did"] == "did:alice"
        assert out["attestation_queue_id"] == "queue:did:alice"
        # load 验证即时生效
        p = load_permissions("did:alice", perms_dir=tmp_path)
        assert p.revoked is True
        assert p.revoked_reason == "abuse-detected"

    def test_revoke_immediate_effect_on_check_permission(
        self, tmp_path: Path
    ) -> None:
        from sisoul.friend.permissions import check_permission, save_permissions

        save_permissions(
            "did:bob",
            FriendPermission(
                friend_did="did:bob",
                llm_quota_share=LLMQuotaShare(enabled=True, mode="strong-tie-auto"),
            ),
            perms_dir=tmp_path,
        )
        # 前: allowed
        ok, _ = check_permission("did:bob", "llm_quota", 100, perms_dir=tmp_path)
        assert ok
        # revoke
        revoke_friend_permission(
            "did:bob",
            reason="x",
            perms_dir=tmp_path,
            onchain_publisher=lambda d, r: "q1",
        )
        # 后: 拒
        ok, reason = check_permission(
            "did:bob", "llm_quota", 100, perms_dir=tmp_path
        )
        assert not ok
        assert reason.startswith("revoked:")

    def test_revoke_publisher_exception_does_not_block_revoke(
        self, tmp_path: Path
    ) -> None:
        def boom(did: str, reason: str) -> str:
            raise RuntimeError("EAS down")

        out = revoke_friend_permission(
            "did:eve",
            reason="x",
            perms_dir=tmp_path,
            onchain_publisher=boom,
        )
        assert out["revoked"] is True
        assert out["attestation_queue_id"] is None  # fail-open
        # local revoke 仍生效
        p = load_permissions("did:eve", perms_dir=tmp_path)
        assert p.revoked

    def test_revoke_creates_perm_when_missing(self, tmp_path: Path) -> None:
        out = revoke_friend_permission(
            "did:newcomer",
            reason="先 ban",
            perms_dir=tmp_path,
            onchain_publisher=lambda d, r: "q",
        )
        assert out["revoked"]


# ── L4 reputation ───────────────────────────────────────────────────────────


class TestL4Reputation:
    def test_default_base(self) -> None:
        r = compute_reputation("did:x")
        assert r.score == 100
        assert r.grade == "B"

    def test_abuse_penalty(self) -> None:
        r = compute_reputation("did:x", abuse_incidents=3)
        # 100 - 3*20 = 40
        assert r.score == 40
        assert r.grade == "D"

    def test_spam_penalty(self) -> None:
        r = compute_reputation("did:x", spam_complaints=2)
        # 100 - 2*10 = 80
        assert r.score == 80
        assert r.grade == "C"

    def test_balanced_bonus(self) -> None:
        r = compute_reputation("did:x", borrows=50, lends=50)
        # 100 + 20 = 120
        assert r.score == 120
        assert r.grade == "B"
        assert abs(r.balance_ratio - 1.0) < 1e-6

    def test_imbalanced_penalty_high(self) -> None:
        r = compute_reputation("did:x", borrows=100, lends=10)
        # 100 - 15 = 85 (ratio 10 > 2)
        assert r.score == 85
        assert r.grade == "C"

    def test_imbalanced_penalty_low(self) -> None:
        r = compute_reputation("did:x", borrows=10, lends=100)
        # ratio 0.1 < 0.5 → -15
        assert r.score == 85

    def test_low_interactions_no_bonus_no_penalty(self) -> None:
        # 总交互 < 10 → 不算平衡/不平衡
        r = compute_reputation("did:x", borrows=2, lends=1)
        assert r.score == 100

    def test_clamp_min(self) -> None:
        r = compute_reputation("did:x", abuse_incidents=10, spam_complaints=10)
        assert r.score == 0
        assert r.grade == "D"

    def test_clamp_max(self) -> None:
        # 没办法直接 >200, 但 base + bonus ≤ 120 < 200, clamp 行为通过 abuse=负值不允许
        r = compute_reputation("did:x", borrows=50, lends=50)
        assert r.score <= 200

    def test_grade_thresholds(self) -> None:
        assert compute_reputation("d", borrows=50, lends=50).grade == "B"  # 120
        # 弄个 A: 需 score >= 150. compute_reputation 公式: base 100 + bonus 20 = 120 max
        # 改 abuse=负值不允许; 单纯函数无法到 A. 验证边界规则不破即可.
        assert compute_reputation("d", abuse_incidents=2).grade == "C"  # 60
        assert compute_reputation("d", abuse_incidents=4).grade == "D"  # 20

    def test_zero_lends_with_borrows_uses_inf_ratio(self) -> None:
        r = compute_reputation("did:x", borrows=20, lends=0)
        # total=20 >= 10, ratio=inf → > 2 → 不平衡 penalty
        assert r.score == 85
        # safe_ratio 替换 inf 为 999
        assert r.balance_ratio == 999.0

    def test_publish_reputation_with_custom_publisher(self) -> None:
        rep = compute_reputation("did:x", borrows=10, lends=10)
        # total=20, ratio 1.0 → bonus
        seen: dict[str, ReputationScore] = {}

        def publisher(r: ReputationScore) -> str:
            seen["got"] = r
            return "queue-abc"

        qid = publish_reputation_attestation(rep, onchain_publisher=publisher)
        assert qid == "queue-abc"
        assert seen["got"].did == "did:x"

    def test_publish_publisher_exception_returns_none(self) -> None:
        rep = compute_reputation("did:x")

        def boom(r: ReputationScore) -> str:
            raise RuntimeError("nope")

        qid = publish_reputation_attestation(rep, onchain_publisher=boom)
        assert qid is None


# ── L5 daemon scan ──────────────────────────────────────────────────────────


class TestL5Scan:
    def _md(self, **kwargs: Any) -> dict[str, Any]:
        base = {
            "friend_did": "did:alice",
            "amount": 1000,
            "model": "claude-opus-4-7",
            "prompt_hash": "0xabc",
            "ts": time.time(),
        }
        base.update(kwargs)
        return base

    def test_ok_pass(self, tmp_path: Path) -> None:
        ok, reason = scan_request_pattern(self._md(), persist_db=tmp_path / "s.db")
        assert ok
        assert reason == "scan:ok"

    def test_missing_friend_blocked(self, tmp_path: Path) -> None:
        ok, reason = scan_request_pattern(
            {"amount": 100}, persist_db=tmp_path / "s.db"
        )
        assert not ok
        assert "missing_friend_did" in reason
        rows = list_scan_log(db_path=tmp_path / "s.db")
        assert any("missing_friend_did" in r["reason"] for r in rows)

    def test_invalid_amount_blocked(self, tmp_path: Path) -> None:
        ok, reason = scan_request_pattern(
            self._md(amount=-1), persist_db=tmp_path / "s.db"
        )
        assert not ok
        assert "invalid_amount" in reason

    def test_invalid_amount_none(self, tmp_path: Path) -> None:
        md = self._md()
        md["amount"] = None
        ok, reason = scan_request_pattern(md, persist_db=tmp_path / "s.db")
        assert not ok
        assert "invalid_amount" in reason

    def test_token_burst_blocked(self, tmp_path: Path) -> None:
        ok, reason = scan_request_pattern(
            self._md(amount=10_000_000),
            thresholds=ScanThresholds(token_burst=200_000),
            persist_db=tmp_path / "s.db",
        )
        assert not ok
        assert "token_burst" in reason
        rows = list_scan_log(db_path=tmp_path / "s.db")
        assert len(rows) >= 1

    def test_rate_burst_blocked(self, tmp_path: Path) -> None:
        now = time.time()
        history = [
            {"friend_did": "did:alice", "ts": now - 1, "prompt_hash": f"h{i}"}
            for i in range(21)
        ]
        ok, reason = scan_request_pattern(
            self._md(ts=now),
            recent_history=history,
            thresholds=ScanThresholds(rate_burst_per_10s=20),
            persist_db=tmp_path / "s.db",
        )
        assert not ok
        assert "rate_burst_10s" in reason

    def test_rate_burst_window_drops_old(self, tmp_path: Path) -> None:
        now = time.time()
        history = [
            {"friend_did": "did:alice", "ts": now - 120, "prompt_hash": f"h{i}"}
            for i in range(50)
        ]
        # 旧 history 全过期 → 不应 block
        ok, _ = scan_request_pattern(
            self._md(ts=now),
            recent_history=history,
            thresholds=ScanThresholds(rate_burst_per_10s=20),
            persist_db=tmp_path / "s.db",
        )
        assert ok

    def test_repeat_hash_blocked(self, tmp_path: Path) -> None:
        history = [
            {
                "friend_did": "did:alice",
                "ts": time.time() - 30,
                "prompt_hash": "0xspam",
            }
            for _ in range(12)
        ]
        ok, reason = scan_request_pattern(
            self._md(prompt_hash="0xspam"),
            recent_history=history,
            thresholds=ScanThresholds(repeat_hash_threshold=10),
            persist_db=tmp_path / "s.db",
        )
        assert not ok
        assert "repeat_hash" in reason

    def test_scan_log_filter_by_friend(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        scan_request_pattern(
            self._md(amount=999_999_999), persist_db=db
        )  # blocked
        scan_request_pattern(
            self._md(friend_did="did:other", amount=999_999_999), persist_db=db
        )
        only_a = list_scan_log(friend_did="did:alice", db_path=db)
        assert all(r["friend_did"] == "did:alice" for r in only_a)
        assert len(only_a) >= 1

    def test_clear_scan_log(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        scan_request_pattern(self._md(amount=999_999_999), persist_db=db)
        scan_request_pattern(self._md(amount=999_999_999), persist_db=db)
        n = clear_scan_log(db_path=db)
        assert n >= 2
        assert list_scan_log(db_path=db) == []

    def test_scan_only_blocked_filter(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        # 触发 1 block
        scan_request_pattern(self._md(amount=999_999_999), persist_db=db)
        # blocked-only 应只列 1
        rows = list_scan_log(db_path=db, only_blocked=True)
        assert all(r["allowed"] is False for r in rows)
