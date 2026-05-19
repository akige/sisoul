"""tests for sisoul.friend.ledger (波 5 dev-D)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from sisoul.friend.ledger import (
    DEFAULT_IMBALANCE_THRESHOLD,
    FriendBalance,
    LedgerEntry,
    LedgerError,
    ReciprocityLedger,
    summarize_friend_ledger,
)


@pytest.fixture
def tmp_ledger(tmp_path: Path) -> Path:
    return tmp_path / "ledger.db"


# ── LedgerEntry ─────────────────────────────────────────────────────────────


class TestLedgerEntry:
    def test_dataclass_defaults(self) -> None:
        e = LedgerEntry(
            borrower_did="alice.eth",
            lender_did="bob.eth",
            resource_type="llm_quota",
            amount=100,
            model_or_skill_id="claude-opus-4-7",
            direction="borrow",
        )
        assert e.entry_id.startswith("")  # uuid4 hex 长度 36
        assert len(e.entry_id) >= 32
        assert e.ts > 0
        assert e.onchain_status == "pending"

    def test_to_dict_round_trip(self) -> None:
        e = LedgerEntry(
            borrower_did="a", lender_did="b", resource_type="llm_quota",
            amount=5, model_or_skill_id="x", direction="borrow",
        )
        d = e.to_dict()
        assert d["borrower_did"] == "a"
        assert d["amount"] == 5


# ── record_usage ────────────────────────────────────────────────────────────


class TestRecordUsage:
    def test_record_basic_borrow(self, tmp_ledger: Path) -> None:
        led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
        try:
            e = led.record_usage(
                "alice.eth", "bob.eth", "llm_quota", 1000, "claude-opus-4-7",
                enqueue_onchain=False,
            )
            assert e.amount == 1000
            assert e.direction == "borrow"
            assert e.onchain_status == "off-chain"
        finally:
            led.close()

    def test_record_with_onchain_enqueue(self, tmp_ledger: Path, tmp_path: Path) -> None:
        led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
        try:
            e = led.record_usage(
                "alice.eth", "bob.eth", "llm_quota", 500, "gpt-5",
                enqueue_onchain=True,
                attest_queue_db=tmp_path / "attest_queue.db",
            )
            # 上链 enqueue 走 dev-B AttestQueue, 默认应该成功; 失败 fallback off-chain
            assert e.onchain_status in ("queued", "off-chain")
            if e.onchain_status == "queued":
                assert e.attest_queue_id is not None
        finally:
            led.close()

    def test_record_negative_amount_raises(self, tmp_ledger: Path) -> None:
        led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
        try:
            with pytest.raises(LedgerError, match="amount"):
                led.record_usage(
                    "alice.eth", "bob.eth", "llm_quota", -1, "x",
                    enqueue_onchain=False,
                )
        finally:
            led.close()

    def test_record_self_lend_raises(self, tmp_ledger: Path) -> None:
        led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
        try:
            with pytest.raises(LedgerError, match="自借自"):
                led.record_usage(
                    "alice.eth", "alice.eth", "llm_quota", 1, "x",
                    enqueue_onchain=False,
                )
        finally:
            led.close()

    def test_record_invalid_direction(self, tmp_ledger: Path) -> None:
        led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
        try:
            with pytest.raises(LedgerError):
                led.record_usage(
                    "alice.eth", "bob.eth", "llm_quota", 1, "x",
                    direction="weird",  # type: ignore[arg-type]
                    enqueue_onchain=False,
                )
        finally:
            led.close()


# ── query_balance ───────────────────────────────────────────────────────────


class TestQueryBalance:
    def test_balance_borrower_heavy_triggers_warning(self, tmp_ledger: Path) -> None:
        led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
        try:
            # Alice 借入 30000, 借出 1000 → ratio 30 > 2 → borrower-heavy warning
            led.record_usage(
                "alice.eth", "bob.eth", "llm_quota", 30000, "claude-opus",
                enqueue_onchain=False,
            )
            led.record_usage(
                "bob.eth", "alice.eth", "llm_quota", 1000, "claude-sonnet",
                enqueue_onchain=False,
            )
            bal = led.query_balance("bob.eth")
            assert bal.borrowed_total == 30000
            assert bal.lent_total == 1000
            assert bal.ratio == pytest.approx(30.0)
            assert bal.direction_imbalance == "borrower-heavy"
            assert bal.imbalance_warning is True
            assert bal.threshold == DEFAULT_IMBALANCE_THRESHOLD
            assert bal.entry_count == 2
        finally:
            led.close()

    def test_balance_lender_heavy(self, tmp_ledger: Path) -> None:
        led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
        try:
            led.record_usage(
                "alice.eth", "bob.eth", "llm_quota", 1000, "x", enqueue_onchain=False,
            )
            led.record_usage(
                "bob.eth", "alice.eth", "llm_quota", 5000, "x", enqueue_onchain=False,
            )
            bal = led.query_balance("bob.eth")
            assert bal.direction_imbalance == "lender-heavy"
            assert bal.imbalance_warning is True
        finally:
            led.close()

    def test_balance_balanced(self, tmp_ledger: Path) -> None:
        led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
        try:
            led.record_usage(
                "alice.eth", "bob.eth", "llm_quota", 1000, "x", enqueue_onchain=False,
            )
            led.record_usage(
                "bob.eth", "alice.eth", "llm_quota", 800, "x", enqueue_onchain=False,
            )
            bal = led.query_balance("bob.eth")
            assert bal.direction_imbalance == "balanced"
            assert bal.imbalance_warning is False
        finally:
            led.close()

    def test_balance_empty(self, tmp_ledger: Path) -> None:
        led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
        try:
            bal = led.query_balance("bob.eth")
            assert bal.borrowed_total == 0
            assert bal.lent_total == 0
            assert bal.direction_imbalance == "balanced"
            assert bal.imbalance_warning is False
        finally:
            led.close()

    def test_balance_self_did_required(self, tmp_ledger: Path) -> None:
        led = ReciprocityLedger(db_path=tmp_ledger)  # 没 self_did
        try:
            with pytest.raises(LedgerError, match="self_did"):
                led.query_balance("bob.eth")
        finally:
            led.close()

    def test_balance_self_eq_friend_raises(self, tmp_ledger: Path) -> None:
        led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
        try:
            with pytest.raises(LedgerError):
                led.query_balance("alice.eth")
        finally:
            led.close()

    def test_balance_by_resource_breakdown(self, tmp_ledger: Path) -> None:
        led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
        try:
            led.record_usage("alice.eth", "bob.eth", "llm_quota", 100, "x",
                             enqueue_onchain=False)
            led.record_usage("alice.eth", "bob.eth", "llm_quota", 200, "x",
                             enqueue_onchain=False)
            led.record_usage("alice.eth", "bob.eth", "ai_skill", 1, "y",
                             enqueue_onchain=False)
            bal = led.query_balance("bob.eth")
            assert bal.borrowed_from_friend == {"llm_quota": 300, "ai_skill": 1}
            assert bal.borrowed_total == 301
        finally:
            led.close()


class TestImbalanceWarnings:
    def test_list_warnings(self, tmp_ledger: Path) -> None:
        led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
        try:
            # bob: 不平衡 (borrower-heavy)
            led.record_usage("alice.eth", "bob.eth", "llm_quota", 10000, "x",
                             enqueue_onchain=False)
            led.record_usage("bob.eth", "alice.eth", "llm_quota", 100, "x",
                             enqueue_onchain=False)
            # charlie: balanced
            led.record_usage("alice.eth", "charlie.eth", "llm_quota", 500, "x",
                             enqueue_onchain=False)
            led.record_usage("charlie.eth", "alice.eth", "llm_quota", 500, "x",
                             enqueue_onchain=False)

            warnings = led.list_imbalance_warnings()
            warn_dids = [w.friend_did for w in warnings]
            assert "bob.eth" in warn_dids
            assert "charlie.eth" not in warn_dids
        finally:
            led.close()

    def test_list_friends(self, tmp_ledger: Path) -> None:
        led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
        try:
            led.record_usage("alice.eth", "bob.eth", "llm_quota", 1, "x",
                             enqueue_onchain=False)
            led.record_usage("charlie.eth", "alice.eth", "llm_quota", 1, "x",
                             enqueue_onchain=False)
            fs = led.list_friends()
            assert set(fs) == {"bob.eth", "charlie.eth"}
        finally:
            led.close()


# ── stats ───────────────────────────────────────────────────────────────────


def test_stats_buckets(tmp_ledger: Path) -> None:
    led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
    try:
        led.record_usage("alice.eth", "bob.eth", "llm_quota", 1, "x",
                         enqueue_onchain=False)
        s = led.stats()
        assert s["off-chain"] >= 1
        assert s["total"] >= 1
    finally:
        led.close()


# ── summarize_friend_ledger (dev-A 集成 API) ─────────────────────────────────


def test_summarize_friend_ledger(tmp_ledger: Path) -> None:
    led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
    try:
        led.record_usage("alice.eth", "bob.eth", "llm_quota", 1000, "x",
                         enqueue_onchain=False)
    finally:
        led.close()
    summary = summarize_friend_ledger(
        own_did="alice.eth", friend_did="bob.eth", ledger_db=tmp_ledger
    )
    assert summary["available"] is True
    assert summary["borrowed_total"] == 1000
    assert summary["lent_total"] == 0
    assert summary["entry_count"] == 1


# ── confirm 接口 ────────────────────────────────────────────────────────────


def test_mark_confirmed_updates_status(tmp_ledger: Path) -> None:
    led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
    try:
        e = led.record_usage("alice.eth", "bob.eth", "llm_quota", 1, "x",
                             enqueue_onchain=False)
        assert e.onchain_status == "off-chain"
        led.mark_confirmed(e.entry_id, "0xabc", "0xtxhash")
        rows = led.list_entries(borrower_did="alice.eth")
        assert any(
            r.entry_id == e.entry_id and r.onchain_status == "confirmed"
            and r.attestation_uid == "0xabc"
            for r in rows
        )
    finally:
        led.close()


def test_query_with_since_ts(tmp_ledger: Path) -> None:
    led = ReciprocityLedger(db_path=tmp_ledger, self_did="alice.eth")
    try:
        old_ts = int(time.time()) - 10000
        led.record_usage("alice.eth", "bob.eth", "llm_quota", 999, "x",
                         ts=old_ts, enqueue_onchain=False)
        led.record_usage("alice.eth", "bob.eth", "llm_quota", 50, "x",
                         enqueue_onchain=False)
        bal_all = led.query_balance("bob.eth")
        bal_recent = led.query_balance("bob.eth", since_ts=int(time.time()) - 100)
        assert bal_all.borrowed_total == 1049
        assert bal_recent.borrowed_total == 50
    finally:
        led.close()
