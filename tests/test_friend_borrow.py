"""tests for sisoul.friend.borrow (波 5 dev-D)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from sisoul.friend import borrow as borrow_mod
from sisoul.friend.borrow import (
    BorrowSession,
    ProxyResult,
    borrow_resource,
    get_proxy_session,
    list_proxy_sessions,
    set_mock_proxy,
    start_proxy_session,
    stop_proxy_session,
    _reset_proxy_sessions_for_test,
)
from sisoul.friend.lend import LendStore


@pytest.fixture
def tmp_db_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "lend": tmp_path / "lend.db",
        "pending": tmp_path / "pending.json",
        "ledger": tmp_path / "ledger.db",
        "attest": tmp_path / "attest.db",
    }


@pytest.fixture(autouse=True)
def _clean_mock_proxy() -> None:
    """每个 test 前后清 injected mock + proxy sessions registry."""
    set_mock_proxy(None)
    _reset_proxy_sessions_for_test()
    yield
    set_mock_proxy(None)
    _reset_proxy_sessions_for_test()


# ── force_mode 快路径 ───────────────────────────────────────────────────────


class TestForceModeBorrow:
    def test_strong_tie_auto_completes_with_stub_proxy(
        self, tmp_db_paths: dict[str, Path]
    ) -> None:
        sess = borrow_resource(
            "alice.eth", "bob.eth", "llm_quota", 1000, "claude-opus-4-7",
            prompt="hello bob",
            force_mode="strong-tie-auto",
            lend_db=tmp_db_paths["lend"],
            pending_file=tmp_db_paths["pending"],
            ledger_db=tmp_db_paths["ledger"],
            enqueue_onchain=False,
        )
        assert sess.status == "completed"
        assert sess.mode == "strong-tie-auto"
        assert sess.proxy_method == "stub-passthrough"  # dev-B 未 ship
        assert sess.proxy_text is not None
        assert sess.tokens_used > 0
        assert sess.ledger_entry_id is not None
        assert sess.lend_request_id is not None

    def test_emergency_only_no_flag_denied(self, tmp_db_paths: dict[str, Path]) -> None:
        sess = borrow_resource(
            "alice.eth", "bob.eth", "llm_quota", 100, "x",
            force_mode="emergency-only",
            emergency_flag=False,
            lend_db=tmp_db_paths["lend"],
            pending_file=tmp_db_paths["pending"],
            ledger_db=tmp_db_paths["ledger"],
            enqueue_onchain=False,
        )
        assert sess.status == "lender-denied"
        assert "emergency" in (sess.error or "")

    def test_emergency_only_with_flag_succeeds(
        self, tmp_db_paths: dict[str, Path]
    ) -> None:
        sess = borrow_resource(
            "alice.eth", "bob.eth", "llm_quota", 100, "x",
            force_mode="emergency-only",
            emergency_flag=True,
            lend_db=tmp_db_paths["lend"],
            pending_file=tmp_db_paths["pending"],
            ledger_db=tmp_db_paths["ledger"],
            enqueue_onchain=False,
        )
        assert sess.status == "completed"


# ── per-request 模式 ────────────────────────────────────────────────────────


class TestPerRequestMode:
    def test_timeout_when_no_approve(self, tmp_db_paths: dict[str, Path]) -> None:
        sess = borrow_resource(
            "alice.eth", "bob.eth", "llm_quota", 100, "x",
            force_mode="per-request",
            per_request_timeout_sec=0.5,
            lend_db=tmp_db_paths["lend"],
            pending_file=tmp_db_paths["pending"],
            ledger_db=tmp_db_paths["ledger"],
            enqueue_onchain=False,
        )
        assert sess.status == "lender-timeout"
        assert "timeout" in (sess.error or "").lower() or "无 lender" in (sess.error or "")

    def test_approve_during_wait(self, tmp_db_paths: dict[str, Path]) -> None:
        """模拟 Bob 在 borrower poll 期间 approve."""
        import threading

        def approve_later() -> None:
            time.sleep(0.2)
            store = LendStore(
                db_path=tmp_db_paths["lend"], pending_file=tmp_db_paths["pending"]
            )
            try:
                pendings = store.list_pending()
                if pendings:
                    store.approve_lend(pendings[0].id)
            finally:
                store.close()

        t = threading.Thread(target=approve_later, daemon=True)
        t.start()

        sess = borrow_resource(
            "alice.eth", "bob.eth", "llm_quota", 50, "claude-opus-4-7",
            force_mode="per-request",
            per_request_timeout_sec=3.0,
            lend_db=tmp_db_paths["lend"],
            pending_file=tmp_db_paths["pending"],
            ledger_db=tmp_db_paths["ledger"],
            enqueue_onchain=False,
        )
        t.join(timeout=5)
        assert sess.status == "completed"
        assert sess.ledger_entry_id is not None

    def test_deny_during_wait(self, tmp_db_paths: dict[str, Path]) -> None:
        import threading

        def deny_later() -> None:
            time.sleep(0.2)
            store = LendStore(
                db_path=tmp_db_paths["lend"], pending_file=tmp_db_paths["pending"]
            )
            try:
                pendings = store.list_pending()
                if pendings:
                    store.deny_lend(pendings[0].id, "no thx")
            finally:
                store.close()

        t = threading.Thread(target=deny_later, daemon=True)
        t.start()

        sess = borrow_resource(
            "alice.eth", "bob.eth", "llm_quota", 50, "x",
            force_mode="per-request",
            per_request_timeout_sec=2.0,
            lend_db=tmp_db_paths["lend"],
            pending_file=tmp_db_paths["pending"],
            ledger_db=tmp_db_paths["ledger"],
            enqueue_onchain=False,
        )
        t.join(timeout=5)
        assert sess.status == "lender-denied"
        assert sess.error and "no thx" in sess.error


# ── 注入 mock proxy ─────────────────────────────────────────────────────────


class TestInjectedMockProxy:
    def test_mock_proxy_called(self, tmp_db_paths: dict[str, Path]) -> None:
        calls: list[dict] = []

        def fake_proxy(**kw: object) -> ProxyResult:
            calls.append(kw)  # type: ignore[arg-type]
            return ProxyResult(
                text="MOCKED response from bob",
                tokens_used=42,
                model_used="claude-opus-4-7",
                method="injected-mock",
            )

        set_mock_proxy(fake_proxy)
        sess = borrow_resource(
            "alice.eth", "bob.eth", "llm_quota", 999, "claude-opus-4-7",
            prompt="test",
            force_mode="strong-tie-auto",
            lend_db=tmp_db_paths["lend"],
            pending_file=tmp_db_paths["pending"],
            ledger_db=tmp_db_paths["ledger"],
            enqueue_onchain=False,
        )
        assert sess.status == "completed"
        assert sess.proxy_method == "injected-mock"
        assert sess.proxy_text == "MOCKED response from bob"
        assert sess.tokens_used == 42
        assert len(calls) == 1

    def test_mock_proxy_raises_proxy_failed(
        self, tmp_db_paths: dict[str, Path]
    ) -> None:
        def bad_proxy(**kw: object) -> ProxyResult:
            raise RuntimeError("upstream API 500")

        set_mock_proxy(bad_proxy)
        sess = borrow_resource(
            "alice.eth", "bob.eth", "llm_quota", 1, "x",
            force_mode="strong-tie-auto",
            lend_db=tmp_db_paths["lend"],
            pending_file=tmp_db_paths["pending"],
            ledger_db=tmp_db_paths["ledger"],
            enqueue_onchain=False,
        )
        assert sess.status == "proxy-failed"
        assert "upstream" in (sess.error or "")
        # ledger 不写 (proxy 失败)
        assert sess.ledger_entry_id is None


# ── proxy session registry ──────────────────────────────────────────────────


class TestProxySession:
    def test_start_get_stop(self) -> None:
        sess = start_proxy_session("alice.eth", "bob.eth", "claude-opus-4-7")
        assert sess.session_id.startswith("ps_")
        assert sess.endpoint.endswith(sess.session_id)
        got = get_proxy_session(sess.session_id)
        assert got is not None and got.session_id == sess.session_id
        stopped = stop_proxy_session(sess.session_id)
        assert stopped is not None
        assert stopped.status == "stopped"

    def test_stop_nonexistent(self) -> None:
        assert stop_proxy_session("ps_does_not_exist") is None

    def test_list_proxy_sessions(self) -> None:
        s1 = start_proxy_session("a.eth", "b.eth", "m1")
        s2 = start_proxy_session("a.eth", "c.eth", "m2")
        sessions = list_proxy_sessions()
        sids = {s.session_id for s in sessions}
        assert s1.session_id in sids and s2.session_id in sids
