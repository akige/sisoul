"""tests for sisoul.friend.permissions (波 5 dev-C).

覆盖:
- LLMQuotaShare / AISkillShare / ComputeShare / FriendPermission dataclass roundtrip
- validate_permission 枚举/数值边界
- load/save permissions yaml roundtrip
- list_all_friends
- check_permission 全 3 档 + 全拒因边界
- mark_revoked / unmark_revoked
- count_monthly_usage + register_usage_provider
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sisoul.friend import permissions as P
from sisoul.friend.permissions import (
    AISkillShare,
    ComputeShare,
    FriendPermission,
    InvalidPermissionConfigError,
    LLMQuotaShare,
    PermissionNotFoundError,
    check_permission,
    count_monthly_usage,
    list_all_friends,
    load_permissions,
    mark_revoked,
    register_usage_provider,
    save_permissions,
    unmark_revoked,
    validate_permission,
)


# ── 数据结构 roundtrip ───────────────────────────────────────────────────────


class TestDataclasses:
    def test_llm_share_roundtrip(self) -> None:
        s = LLMQuotaShare(
            enabled=True,
            mode="strong-tie-auto",
            monthly_token_cap=500_000,
            rate_limit=10,
            models=["claude-opus-4-7"],
            emergency_reserve_tokens=100_000,
        )
        d = s.to_dict()
        s2 = LLMQuotaShare.from_dict(d)
        assert s == s2

    def test_friend_perm_roundtrip_nested(self) -> None:
        p = FriendPermission(
            friend_did="did:sisoul:alice",
            llm_quota_share=LLMQuotaShare(enabled=True, mode="strong-tie-auto"),
            ai_skill_share=AISkillShare(enabled=True, mode="per-request", skills=["solidity"]),
        )
        d = p.to_dict()
        assert d["friend"] == "did:sisoul:alice"
        assert d["permissions"]["llm_quota_share"]["mode"] == "strong-tie-auto"
        p2 = FriendPermission.from_dict(d)
        assert p2.friend_did == "did:sisoul:alice"
        assert p2.llm_quota_share.mode == "strong-tie-auto"
        assert p2.ai_skill_share.skills == ["solidity"]

    def test_from_dict_flat_layout(self) -> None:
        p = FriendPermission.from_dict(
            {
                "friend_did": "did:sisoul:bob",
                "llm_quota_share": {"enabled": True, "mode": "per-request"},
            }
        )
        assert p.friend_did == "did:sisoul:bob"
        assert p.llm_quota_share.enabled is True

    def test_unknown_fields_tolerated(self) -> None:
        s = LLMQuotaShare.from_dict({"enabled": True, "unknown_field": "x"})
        assert s.enabled is True


# ── validate ────────────────────────────────────────────────────────────────


class TestValidate:
    def test_valid(self) -> None:
        p = FriendPermission(friend_did="did:x")
        validate_permission(p)  # no raise

    def test_missing_did_raises(self) -> None:
        p = FriendPermission(friend_did="")
        with pytest.raises(InvalidPermissionConfigError):
            validate_permission(p)

    def test_bad_mode_raises(self) -> None:
        p = FriendPermission(
            friend_did="did:x", llm_quota_share=LLMQuotaShare(mode="WAT")  # type: ignore[arg-type]
        )
        with pytest.raises(InvalidPermissionConfigError):
            validate_permission(p)

    def test_negative_cap_raises(self) -> None:
        p = FriendPermission(
            friend_did="did:x", llm_quota_share=LLMQuotaShare(monthly_token_cap=-1)
        )
        with pytest.raises(InvalidPermissionConfigError):
            validate_permission(p)

    def test_negative_rate_raises(self) -> None:
        p = FriendPermission(
            friend_did="did:x", llm_quota_share=LLMQuotaShare(rate_limit=-5)
        )
        with pytest.raises(InvalidPermissionConfigError):
            validate_permission(p)

    def test_negative_reserve_raises(self) -> None:
        p = FriendPermission(
            friend_did="did:x",
            llm_quota_share=LLMQuotaShare(emergency_reserve_tokens=-100),
        )
        with pytest.raises(InvalidPermissionConfigError):
            validate_permission(p)

    def test_negative_session_minutes_raises(self) -> None:
        p = FriendPermission(
            friend_did="did:x",
            ai_skill_share=AISkillShare(per_session_max_minutes=-1),
        )
        with pytest.raises(InvalidPermissionConfigError):
            validate_permission(p)


# ── yaml persistence ─────────────────────────────────────────────────────────


class TestPersistence:
    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        p = FriendPermission(
            friend_did="did:sisoul:alice",
            llm_quota_share=LLMQuotaShare(
                enabled=True,
                mode="strong-tie-auto",
                monthly_token_cap=500_000,
                rate_limit=10,
                models=["claude-opus-4-7"],
            ),
        )
        path = save_permissions("did:sisoul:alice", p, perms_dir=tmp_path)
        assert path.exists()
        loaded = load_permissions("did:sisoul:alice", perms_dir=tmp_path)
        assert loaded.friend_did == "did:sisoul:alice"
        assert loaded.llm_quota_share.monthly_token_cap == 500_000
        assert loaded.llm_quota_share.models == ["claude-opus-4-7"]

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PermissionNotFoundError):
            load_permissions("did:nobody", perms_dir=tmp_path)

    def test_save_with_did_mismatch_raises(self, tmp_path: Path) -> None:
        p = FriendPermission(friend_did="did:wrong")
        with pytest.raises(InvalidPermissionConfigError):
            save_permissions("did:right", p, perms_dir=tmp_path)

    def test_load_did_safely_overrides_empty(self, tmp_path: Path) -> None:
        # 手写 yaml 不含 friend 字段
        f = tmp_path / "did_sisoul_charlie-permissions.yaml"
        f.write_text(
            "permissions:\n  llm_quota_share:\n    enabled: true\n    mode: per-request\n",
            encoding="utf-8",
        )
        # filename sanitize 替换 ":" → "_", so look up by exact DID
        p = save_permissions(
            "did:sisoul:dany",
            FriendPermission(
                friend_did="did:sisoul:dany",
                llm_quota_share=LLMQuotaShare(enabled=True, mode="per-request"),
            ),
            perms_dir=tmp_path,
        )
        loaded = load_permissions("did:sisoul:dany", perms_dir=tmp_path)
        assert loaded.friend_did == "did:sisoul:dany"

    def test_load_invalid_yaml_raises(self, tmp_path: Path) -> None:
        # 写入非法 yaml (顶层 list, 不 mapping)
        from sisoul.friend.permissions import _perm_path

        p = _perm_path("did:bad", tmp_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("- foo\n- bar\n", encoding="utf-8")
        with pytest.raises(InvalidPermissionConfigError):
            load_permissions("did:bad", perms_dir=tmp_path)

    def test_list_all_friends(self, tmp_path: Path) -> None:
        for did in ("did:a", "did:b", "did:c"):
            save_permissions(did, FriendPermission(friend_did=did), perms_dir=tmp_path)
        out = list_all_friends(perms_dir=tmp_path)
        assert set(out) == {"did:a", "did:b", "did:c"}

    def test_list_friends_empty_dir(self, tmp_path: Path) -> None:
        assert list_all_friends(perms_dir=tmp_path / "nope") == []

    def test_list_friends_skips_broken(self, tmp_path: Path) -> None:
        save_permissions(
            "did:ok", FriendPermission(friend_did="did:ok"), perms_dir=tmp_path
        )
        (tmp_path / "bad-permissions.yaml").write_text(":::not yaml", encoding="utf-8")
        out = list_all_friends(perms_dir=tmp_path)
        assert "did:ok" in out


# ── check_permission core ───────────────────────────────────────────────────


class TestCheckPermission:
    def _alice(self, **llm_kwargs: object) -> FriendPermission:
        defaults = dict(enabled=True, mode="strong-tie-auto", monthly_token_cap=0)
        defaults.update(llm_kwargs)
        return FriendPermission(
            friend_did="did:sisoul:alice",
            llm_quota_share=LLMQuotaShare(**defaults),  # type: ignore[arg-type]
        )

    def test_unknown_resource(self) -> None:
        ok, reason = check_permission(
            "did:x", "bogus", 10, perm=self._alice()  # type: ignore[arg-type]
        )
        assert not ok
        assert "unknown_resource" in reason

    def test_negative_amount(self) -> None:
        ok, reason = check_permission(
            "did:x", "llm_quota", -1, model="claude-opus-4-7", perm=self._alice()
        )
        assert not ok
        assert "negative" in reason

    def test_no_config(self, tmp_path: Path) -> None:
        ok, reason = check_permission(
            "did:nobody", "llm_quota", 100, perms_dir=tmp_path
        )
        assert not ok
        assert reason == "no_permission_config"

    def test_revoked(self) -> None:
        perm = self._alice()
        perm.revoked = True
        perm.revoked_reason = "abuse"
        ok, reason = check_permission(
            "did:x", "llm_quota", 100, perm=perm
        )
        assert not ok
        assert "revoked:abuse" in reason

    def test_resource_disabled(self) -> None:
        perm = self._alice(enabled=False)
        ok, reason = check_permission("did:x", "llm_quota", 100, perm=perm)
        assert not ok
        assert "resource_disabled" in reason

    def test_strong_tie_auto_approves(self) -> None:
        perm = self._alice()
        ok, reason = check_permission("did:x", "llm_quota", 100, perm=perm)
        assert ok
        assert "strong-tie-auto" in reason

    def test_per_request_pending(self) -> None:
        perm = self._alice(mode="per-request")
        ok, reason = check_permission("did:x", "llm_quota", 100, perm=perm)
        assert not ok
        assert "per_request_pending" in reason

    def test_per_request_approved(self) -> None:
        perm = self._alice(mode="per-request")
        ok, reason = check_permission(
            "did:x", "llm_quota", 100, perm=perm, per_request_approved=True
        )
        assert ok
        assert "per-request" in reason

    def test_emergency_only_no_flag(self) -> None:
        perm = self._alice(mode="emergency-only")
        ok, reason = check_permission("did:x", "llm_quota", 100, perm=perm)
        assert not ok
        assert "emergency_only_no_flag" in reason

    def test_emergency_only_with_flag(self) -> None:
        perm = self._alice(mode="emergency-only", emergency_reserve_tokens=50_000)
        ok, reason = check_permission(
            "did:x", "llm_quota", 1000, perm=perm, emergency_flag=True
        )
        assert ok
        assert "emergency-only" in reason

    def test_emergency_reserve_exceeded(self) -> None:
        perm = self._alice(
            mode="emergency-only",
            monthly_token_cap=100,
            emergency_reserve_tokens=500,
        )
        # current_usage=100 → cap reached, amount=1000 > reserve 500
        ok, reason = check_permission(
            "did:x",
            "llm_quota",
            1000,
            perm=perm,
            emergency_flag=True,
            current_usage=100,
        )
        assert not ok
        assert "emergency_reserve_exceeded" in reason

    def test_monthly_cap_exceeded(self) -> None:
        perm = self._alice(monthly_token_cap=1000)
        ok, reason = check_permission(
            "did:x", "llm_quota", 500, perm=perm, current_usage=900
        )
        assert not ok
        assert "monthly_cap_exceeded" in reason

    def test_monthly_cap_ok(self) -> None:
        perm = self._alice(monthly_token_cap=1000)
        ok, reason = check_permission(
            "did:x", "llm_quota", 100, perm=perm, current_usage=200
        )
        assert ok

    def test_model_not_allowed(self) -> None:
        perm = self._alice(models=["claude-opus-4-7"])
        ok, reason = check_permission(
            "did:x", "llm_quota", 100, model="gpt-5", perm=perm
        )
        assert not ok
        assert "model_not_allowed" in reason

    def test_model_required(self) -> None:
        perm = self._alice(models=["claude-opus-4-7"])
        ok, reason = check_permission("did:x", "llm_quota", 100, perm=perm)
        assert not ok
        assert reason == "model_required"

    def test_model_empty_allowlist_allows_any(self) -> None:
        perm = self._alice(models=[])
        ok, _ = check_permission("did:x", "llm_quota", 100, model="anything", perm=perm)
        assert ok

    def test_ai_skill_not_allowed(self) -> None:
        perm = FriendPermission(
            friend_did="did:x",
            ai_skill_share=AISkillShare(
                enabled=True, mode="strong-tie-auto", skills=["solidity-expert"]
            ),
        )
        ok, reason = check_permission(
            "did:x", "ai_skill", 10, model="unknown-skill", perm=perm
        )
        assert not ok
        assert "skill_not_allowed" in reason

    def test_ai_skill_session_too_long(self) -> None:
        perm = FriendPermission(
            friend_did="did:x",
            ai_skill_share=AISkillShare(
                enabled=True,
                mode="strong-tie-auto",
                skills=["solidity-expert"],
                per_session_max_minutes=30,
            ),
        )
        ok, reason = check_permission(
            "did:x", "ai_skill", 60, model="solidity-expert", perm=perm
        )
        assert not ok
        assert "session_too_long" in reason

    def test_ai_skill_ok(self) -> None:
        perm = FriendPermission(
            friend_did="did:x",
            ai_skill_share=AISkillShare(
                enabled=True,
                mode="strong-tie-auto",
                skills=["solidity-expert"],
                per_session_max_minutes=30,
            ),
        )
        ok, _ = check_permission(
            "did:x", "ai_skill", 20, model="solidity-expert", perm=perm
        )
        assert ok


# ── revoke helper ───────────────────────────────────────────────────────────


class TestRevoke:
    def test_mark_revoked_creates_when_missing(self, tmp_path: Path) -> None:
        p = mark_revoked("did:newbie", reason="suspicious", perms_dir=tmp_path)
        assert p.revoked
        assert p.revoked_reason == "suspicious"
        loaded = load_permissions("did:newbie", perms_dir=tmp_path)
        assert loaded.revoked

    def test_mark_revoked_existing(self, tmp_path: Path) -> None:
        save_permissions(
            "did:x",
            FriendPermission(
                friend_did="did:x",
                llm_quota_share=LLMQuotaShare(enabled=True, mode="strong-tie-auto"),
            ),
            perms_dir=tmp_path,
        )
        p = mark_revoked("did:x", reason="abuse", perms_dir=tmp_path)
        assert p.revoked
        assert p.llm_quota_share.enabled is True  # 不动其他字段

    def test_unmark_revoked(self, tmp_path: Path) -> None:
        mark_revoked("did:x", reason="abuse", perms_dir=tmp_path)
        p = unmark_revoked("did:x", perms_dir=tmp_path)
        assert not p.revoked
        assert p.revoked_at is None


# ── usage provider ──────────────────────────────────────────────────────────


class TestUsageProvider:
    def teardown_method(self) -> None:
        register_usage_provider(None)  # type: ignore[arg-type]

    def test_default_stub_zero(self) -> None:
        register_usage_provider(None)  # type: ignore[arg-type]
        assert count_monthly_usage("did:x", "llm_quota") == 0

    def test_provider_injection(self) -> None:
        def provider(did: str, rt: str) -> int:
            return 12345

        register_usage_provider(provider)
        assert count_monthly_usage("did:x", "llm_quota") == 12345

    def test_provider_exception_fail_open_zero(self) -> None:
        def provider(did: str, rt: str) -> int:
            raise RuntimeError("ledger db down")

        register_usage_provider(provider)
        assert count_monthly_usage("did:x", "llm_quota") == 0
