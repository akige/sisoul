"""Unit + integration tests for the incentive layer (kudos / wallet / borrow integration)."""
from __future__ import annotations
import os
import tempfile
from pathlib import Path

import pytest

# All tests use a temporary vault to avoid touching the user's real ~/.sisoul.
@pytest.fixture
def tmp_vault(monkeypatch, tmp_path):
    monkeypatch.setenv("SISOUL_VAULT", str(tmp_path))
    (tmp_path / "friends").mkdir()
    return tmp_path


# ────────────────────────── kudos ──────────────────────────


def test_kudos_store_earn_spend(tmp_vault):
    from sisoul.friend.kudos import KudosStore
    ks = KudosStore()
    ks.earn("did:key:bob", 100.0, "lent 100k")
    ks.spend("did:key:bob", 30.0, "borrowed 30k")
    assert abs(ks.balance("did:key:bob") - 70.0) < 1e-6


def test_kudos_insufficient_floor(tmp_vault):
    from sisoul.friend.kudos import KudosStore, KudosInsufficient
    ks = KudosStore()
    # current = 0; spend 1500 would push to -1500 < floor -1000 → raise
    with pytest.raises(KudosInsufficient):
        ks.spend("did:key:alice", 1500.0, "huge borrow")


def test_kudos_history(tmp_vault):
    from sisoul.friend.kudos import KudosStore
    ks = KudosStore()
    ks.earn("did:key:bob", 50.0, "first")
    ks.spend("did:key:bob", 10.0, "second")
    entries = ks.history("did:key:bob")
    assert len(entries) == 2
    # most recent first
    assert entries[0].reason == "second"
    assert entries[0].delta == -10.0
    assert entries[1].reason == "first"
    assert entries[1].delta == 50.0


def test_kudos_decay_positive_only(tmp_vault):
    """5%/month decay applies only to positive balances."""
    from sisoul.friend.kudos import KudosStore
    ks = KudosStore()
    ks.earn("did:key:bob", 100.0, "seed")
    ks.spend("did:key:carol", 50.0, "no inverse")
    # Force decay by passing future time
    import time
    one_month_later = time.time() + 30 * 86400
    changes = ks.apply_decay(now=one_month_later)
    # bob: 100 * 0.95 = 95
    assert "did:key:bob" in changes
    old, new, factor = changes["did:key:bob"]
    assert abs(new - 95.0) < 1.0  # rough due to float precision
    # carol: stays at -50 (negative, no decay)
    assert "did:key:carol" not in changes
    assert ks.balance("did:key:carol") == -50.0


def test_kudos_decay_idempotent(tmp_vault):
    from sisoul.friend.kudos import KudosStore
    ks = KudosStore()
    ks.earn("did:key:bob", 100.0, "seed")
    import time
    t1 = time.time() + 30 * 86400
    ks.apply_decay(now=t1)
    bal1 = ks.balance("did:key:bob")
    # immediately apply again — no time has passed → no further decay
    ks.apply_decay(now=t1)
    bal2 = ks.balance("did:key:bob")
    assert abs(bal1 - bal2) < 1e-6


# ────────────────────────── wallet ──────────────────────────


def test_wallet_set_trc20(tmp_vault):
    from sisoul.wallet import WalletStore
    ws = WalletStore()
    ws.set_usdt_trc20("TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn")
    assert ws.get().usdt_trc20 == "TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn"


def test_wallet_reject_non_trc20(tmp_vault):
    from sisoul.wallet import WalletStore, WalletError
    ws = WalletStore()
    with pytest.raises(WalletError):
        ws.set_usdt_trc20("0xNotATronAddress")
    with pytest.raises(WalletError):
        ws.set_usdt_trc20("Tshort")
    with pytest.raises(WalletError):
        ws.set_usdt_trc20("NotStartingWithT_aaaaaaaaaaaaaaaaaaaaaaaaaa")


def test_wallet_persist_across_instances(tmp_vault):
    from sisoul.wallet import WalletStore
    a = WalletStore()
    a.set_usdt_trc20("TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn")
    b = WalletStore()
    assert b.get().usdt_trc20 == "TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn"


# ────────────────────────── LLMQuotaShare incentive fields ──────────────────


def test_llm_quota_share_default_gift():
    from sisoul.friend.permissions import LLMQuotaShare
    q = LLMQuotaShare()
    assert q.incentive_mode == "gift"
    q.validate_incentive()


def test_llm_quota_share_kudos():
    from sisoul.friend.permissions import LLMQuotaShare
    q = LLMQuotaShare(incentive_mode="kudos", kudos_required_per_1k_tokens=1.5)
    q.validate_incentive()


def test_llm_quota_share_micropay_validates_address():
    from sisoul.friend.permissions import LLMQuotaShare, InvalidPermissionConfigError
    # Valid TRC20
    q = LLMQuotaShare(
        incentive_mode="micropay",
        usdt_per_1k_tokens=0.01,
        usdt_payout_address="TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn",
    )
    q.validate_incentive()
    # Missing address
    with pytest.raises(InvalidPermissionConfigError):
        LLMQuotaShare(incentive_mode="micropay", usdt_per_1k_tokens=0.01).validate_incentive()
    # ERC20 address rejected for TRC20 mode
    with pytest.raises(InvalidPermissionConfigError):
        LLMQuotaShare(
            incentive_mode="micropay",
            usdt_per_1k_tokens=0.01,
            usdt_payout_address="0xabcdef1234567890abcdef1234567890abcdef12",
        ).validate_incentive()


def test_llm_quota_share_bogus_mode_rejected():
    from sisoul.friend.permissions import LLMQuotaShare, InvalidPermissionConfigError
    with pytest.raises(InvalidPermissionConfigError):
        LLMQuotaShare(incentive_mode="airdrop").validate_incentive()


def test_compute_kudos_required():
    from sisoul.friend.permissions import LLMQuotaShare
    from sisoul.friend.kudos import compute_kudos_required
    q = LLMQuotaShare(incentive_mode="kudos", kudos_required_per_1k_tokens=2.0)
    assert compute_kudos_required(q, 5000) == 10.0
    # gift mode → 0
    q2 = LLMQuotaShare(incentive_mode="gift")
    assert compute_kudos_required(q2, 5000) == 0.0


def test_compute_usdt_required():
    from sisoul.friend.permissions import LLMQuotaShare
    from sisoul.friend.kudos import compute_usdt_required
    q = LLMQuotaShare(
        incentive_mode="micropay",
        usdt_per_1k_tokens=0.01,
        usdt_payout_address="TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn",
    )
    assert abs(compute_usdt_required(q, 5000) - 0.05) < 1e-9


# ────────────────────────── borrow integration ──────────────────────────


def _write_perm(perms_dir, friend_did, **q_fields):
    """Helper: write a YAML perm file with given LLMQuotaShare fields."""
    import re, yaml
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", friend_did)
    quota = {
        "enabled": True,
        "mode": "per-request",
        "monthly_token_cap": 100000,
        "rate_limit": 10,
        **q_fields,
    }
    perm = {
        "friend_did": friend_did,
        "llm_quota_share": quota,
    }
    (perms_dir / f"{safe}-permissions.yaml").write_text(yaml.dump(perm))


def test_borrow_gift_mode_dry_run(tmp_vault):
    from sisoul.friend.borrow import borrow_resource
    perms_dir = tmp_vault / "friends"
    _write_perm(perms_dir, "did:key:gift_friend", incentive_mode="gift")
    s = borrow_resource(
        "me", "did:key:gift_friend", "llm_quota", 5000, "claude-opus",
        dry_run=True, perms_dir=perms_dir,
    )
    assert s.incentive_mode == "gift"
    assert s.kudos_cost == 0
    assert s.usdt_cost == 0
    assert s.status == "completed"
    assert s.lend_request_id is None


def test_borrow_kudos_mode_dry_run_quote(tmp_vault):
    from sisoul.friend.borrow import borrow_resource
    perms_dir = tmp_vault / "friends"
    _write_perm(perms_dir, "did:key:kudos_friend",
                incentive_mode="kudos", kudos_required_per_1k_tokens=2.0)
    s = borrow_resource(
        "me", "did:key:kudos_friend", "llm_quota", 5000, "claude-opus",
        dry_run=True, perms_dir=perms_dir,
    )
    assert s.incentive_mode == "kudos"
    assert s.kudos_cost == 10.0
    # dry-run doesn't actually spend, balance stays 0
    from sisoul.friend.kudos import KudosStore
    assert KudosStore().balance("did:key:kudos_friend") == 0.0


def test_borrow_kudos_mode_real_spend(tmp_vault):
    from sisoul.friend.borrow import borrow_resource
    from sisoul.friend.kudos import KudosStore
    perms_dir = tmp_vault / "friends"
    _write_perm(perms_dir, "did:key:kudos_friend",
                incentive_mode="kudos", kudos_required_per_1k_tokens=2.0)
    s = borrow_resource(
        "me", "did:key:kudos_friend", "llm_quota", 5000, "claude-opus",
        force_mode="strong-tie-auto", perms_dir=perms_dir,
    )
    # Real spend: balance now -10
    assert KudosStore().balance("did:key:kudos_friend") == -10.0


def test_borrow_micropay_mode_quote(tmp_vault):
    from sisoul.friend.borrow import borrow_resource
    perms_dir = tmp_vault / "friends"
    _write_perm(
        perms_dir, "did:key:micropay_friend",
        incentive_mode="micropay",
        usdt_per_1k_tokens=0.01,
        usdt_payout_address="TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn",
    )
    s = borrow_resource(
        "me", "did:key:micropay_friend", "llm_quota", 5000, "claude-opus",
        dry_run=True, perms_dir=perms_dir,
    )
    assert s.incentive_mode == "micropay"
    assert abs(s.usdt_cost - 0.05) < 1e-9
    assert s.usdt_payout_address == "TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn"
    assert "tronscan" in s.incentive_receipt
    assert "instruction" in s.incentive_receipt
    assert "Pay 0.0500 USDT" in s.incentive_receipt["instruction"]
