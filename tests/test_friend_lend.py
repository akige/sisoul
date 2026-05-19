"""tests for sisoul.friend.lend (波 5 dev-D)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from sisoul.friend.lend import (
    DEFAULT_REQUEST_TTL_SEC,
    LendError,
    LendStore,
    RequestNotFoundError,
    RequestStateError,
    approve_lend,
    deny_lend,
    list_pending_requests,
    request_lend,
)


@pytest.fixture
def tmp_lend(tmp_path: Path) -> Path:
    return tmp_path / "lend.db"


@pytest.fixture
def tmp_pending(tmp_path: Path) -> Path:
    return tmp_path / "pending_lends.json"


# ── request_lend ────────────────────────────────────────────────────────────


class TestRequestLend:
    def test_per_request_pending(self, tmp_lend: Path, tmp_pending: Path) -> None:
        req = request_lend(
            "alice.eth", "bob.eth", "llm_quota", 1000, "claude-opus-4-7",
            mode="per-request",
            db_path=tmp_lend, pending_file=tmp_pending,
        )
        assert req.status == "pending"
        assert req.mode == "per-request"
        assert req.id.startswith("lr_")
        assert tmp_pending.exists()
        data = json.loads(tmp_pending.read_text())
        assert data["count"] == 1
        assert data["pending"][0]["id"] == req.id

    def test_strong_tie_auto_approved(self, tmp_lend: Path, tmp_pending: Path) -> None:
        req = request_lend(
            "alice.eth", "bob.eth", "llm_quota", 100, "x",
            mode="strong-tie-auto",
            db_path=tmp_lend, pending_file=tmp_pending,
        )
        assert req.status == "approved"
        assert req.decided_at is not None

    def test_emergency_only_with_flag(self, tmp_lend: Path, tmp_pending: Path) -> None:
        req = request_lend(
            "alice.eth", "bob.eth", "llm_quota", 100, "x",
            mode="emergency-only", emergency_flag=True,
            db_path=tmp_lend, pending_file=tmp_pending,
        )
        assert req.status == "approved"

    def test_emergency_only_no_flag_denied(self, tmp_lend: Path, tmp_pending: Path) -> None:
        req = request_lend(
            "alice.eth", "bob.eth", "llm_quota", 100, "x",
            mode="emergency-only", emergency_flag=False,
            db_path=tmp_lend, pending_file=tmp_pending,
        )
        assert req.status == "denied"
        assert "emergency" in (req.denied_reason or "")

    def test_self_borrow_raises(self, tmp_lend: Path, tmp_pending: Path) -> None:
        with pytest.raises(LendError):
            request_lend(
                "alice.eth", "alice.eth", "llm_quota", 1, "x",
                db_path=tmp_lend, pending_file=tmp_pending,
            )

    def test_negative_amount_raises(self, tmp_lend: Path, tmp_pending: Path) -> None:
        with pytest.raises(LendError):
            request_lend(
                "alice.eth", "bob.eth", "llm_quota", -1, "x",
                db_path=tmp_lend, pending_file=tmp_pending,
            )


# ── approve / deny ──────────────────────────────────────────────────────────


class TestApproveDeny:
    def test_approve_pending(self, tmp_lend: Path, tmp_pending: Path) -> None:
        req = request_lend(
            "alice.eth", "bob.eth", "llm_quota", 100, "x",
            db_path=tmp_lend, pending_file=tmp_pending,
        )
        approved = approve_lend(req.id, db_path=tmp_lend, pending_file=tmp_pending)
        assert approved.status == "approved"
        assert approved.decided_at is not None

    def test_approve_idempotent(self, tmp_lend: Path, tmp_pending: Path) -> None:
        req = request_lend(
            "alice.eth", "bob.eth", "llm_quota", 100, "x",
            db_path=tmp_lend, pending_file=tmp_pending,
        )
        a1 = approve_lend(req.id, db_path=tmp_lend, pending_file=tmp_pending)
        a2 = approve_lend(req.id, db_path=tmp_lend, pending_file=tmp_pending)
        assert a1.status == a2.status == "approved"

    def test_approve_denied_raises(self, tmp_lend: Path, tmp_pending: Path) -> None:
        req = request_lend(
            "alice.eth", "bob.eth", "llm_quota", 100, "x",
            db_path=tmp_lend, pending_file=tmp_pending,
        )
        deny_lend(req.id, "no thx", db_path=tmp_lend, pending_file=tmp_pending)
        with pytest.raises(RequestStateError):
            approve_lend(req.id, db_path=tmp_lend, pending_file=tmp_pending)

    def test_approve_not_found(self, tmp_lend: Path, tmp_pending: Path) -> None:
        with pytest.raises(RequestNotFoundError):
            approve_lend("lr_nonexistent", db_path=tmp_lend, pending_file=tmp_pending)

    def test_deny_with_reason(self, tmp_lend: Path, tmp_pending: Path) -> None:
        req = request_lend(
            "alice.eth", "bob.eth", "llm_quota", 100, "x",
            db_path=tmp_lend, pending_file=tmp_pending,
        )
        denied = deny_lend(
            req.id, "abuse", db_path=tmp_lend, pending_file=tmp_pending
        )
        assert denied.status == "denied"
        assert denied.denied_reason == "abuse"


# ── expire ──────────────────────────────────────────────────────────────────


def test_expire_stale(tmp_lend: Path, tmp_pending: Path) -> None:
    req = request_lend(
        "alice.eth", "bob.eth", "llm_quota", 100, "x",
        mode="per-request", ttl_sec=1,
        db_path=tmp_lend, pending_file=tmp_pending,
    )
    assert req.status == "pending"
    time.sleep(1.1)
    store = LendStore(db_path=tmp_lend, pending_file=tmp_pending)
    try:
        n = store.expire_stale()
        assert n == 1
        got = store.get(req.id)
        assert got.status == "expired"
    finally:
        store.close()


# ── list_pending ────────────────────────────────────────────────────────────


def test_list_pending(tmp_lend: Path, tmp_pending: Path) -> None:
    request_lend("a.eth", "z.eth", "llm_quota", 1, "x",
                 mode="per-request",
                 db_path=tmp_lend, pending_file=tmp_pending)
    request_lend("b.eth", "z.eth", "llm_quota", 2, "x",
                 mode="per-request",
                 db_path=tmp_lend, pending_file=tmp_pending)
    request_lend("c.eth", "z.eth", "llm_quota", 3, "x",
                 mode="strong-tie-auto",
                 db_path=tmp_lend, pending_file=tmp_pending)  # 直接 approved
    pending = list_pending_requests(db_path=tmp_lend, pending_file=tmp_pending)
    assert len(pending) == 2
    assert all(r.status == "pending" for r in pending)


# ── pending_lends.json atomic write ────────────────────────────────────────


def test_pending_file_atomic_write(tmp_lend: Path, tmp_pending: Path) -> None:
    """每次 mutation 都重写 pending_file (PWA poll 看最新)."""
    req = request_lend("a.eth", "b.eth", "llm_quota", 100, "x",
                       mode="per-request",
                       db_path=tmp_lend, pending_file=tmp_pending)
    d1 = json.loads(tmp_pending.read_text())
    assert d1["count"] == 1
    approve_lend(req.id, db_path=tmp_lend, pending_file=tmp_pending)
    d2 = json.loads(tmp_pending.read_text())
    assert d2["count"] == 0


# ── mark_completed ─────────────────────────────────────────────────────────


def test_mark_completed(tmp_lend: Path, tmp_pending: Path) -> None:
    req = request_lend("a.eth", "b.eth", "llm_quota", 1, "x",
                       mode="strong-tie-auto",
                       db_path=tmp_lend, pending_file=tmp_pending)
    assert req.status == "approved"
    store = LendStore(db_path=tmp_lend, pending_file=tmp_pending)
    try:
        c = store.mark_completed(req.id)
        assert c.status == "completed"
        with pytest.raises(RequestStateError):
            store.mark_completed(req.id)  # 不能再 complete
    finally:
        store.close()
