"""完整 alice→bob borrow flow integration (波 5 dev-D).

同机模拟 alice + bob 共享同一 lend_db / ledger_db (跨设备 P2P 走 dev-B 加密 proxy 真路径).

流程:
    alice borrow → bob lend approve → 加密 proxy 调 mock LLM → ledger 写 EAS attestation
    → query balance 真累积 → 互惠 ledger 不平衡 warning 触发
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from sisoul.friend.borrow import (
    ProxyResult,
    _reset_proxy_sessions_for_test,
    borrow_resource,
    set_mock_proxy,
)
from sisoul.friend.ledger import ReciprocityLedger
from sisoul.friend.lend import LendStore


@pytest.fixture
def tmp_dbs(tmp_path: Path) -> dict[str, Path]:
    return {
        "lend": tmp_path / "lend.db",
        "pending": tmp_path / "pending.json",
        "ledger": tmp_path / "ledger.db",
        "attest": tmp_path / "attest.db",
    }


@pytest.fixture(autouse=True)
def _clean() -> None:
    set_mock_proxy(None)
    _reset_proxy_sessions_for_test()
    yield
    set_mock_proxy(None)
    _reset_proxy_sessions_for_test()


# ── E2E flow 1: per-request 模式 alice→bob 完整流程 ─────────────────────────


def test_per_request_complete_flow(tmp_dbs: dict[str, Path]) -> None:
    """
    1) Alice 发 borrow (per-request 模式) → LendStore 写 pending
    2) Bob (另线程) 看 pending → approve
    3) Alice 端 borrow 流程 resume → 加密 proxy mock → ledger 写
    4) Alice query balance → 真累积
    """
    proxy_calls: list[dict] = []

    def mock_proxy(**kw: object) -> ProxyResult:
        proxy_calls.append(kw)  # type: ignore[arg-type]
        return ProxyResult(
            text="OK from bob's claude",
            tokens_used=123,
            model_used="claude-opus-4-7",
            method="injected-mock",
        )

    set_mock_proxy(mock_proxy)

    def bob_approves() -> None:
        time.sleep(0.3)
        store = LendStore(db_path=tmp_dbs["lend"], pending_file=tmp_dbs["pending"])
        try:
            pendings = store.list_pending()
            assert len(pendings) == 1, f"expected 1 pending, got {len(pendings)}"
            store.approve_lend(pendings[0].id)
        finally:
            store.close()

    t0 = time.time()
    t = threading.Thread(target=bob_approves, daemon=True)
    t.start()

    sess = borrow_resource(
        borrower_did="alice.sisoul.eth",
        lender_did="bob.sisoul.eth",
        resource_type="llm_quota",
        amount=1000,
        model="claude-opus-4-7",
        prompt="please help write solidity",
        force_mode="per-request",
        per_request_timeout_sec=5.0,
        lend_db=tmp_dbs["lend"],
        pending_file=tmp_dbs["pending"],
        ledger_db=tmp_dbs["ledger"],
        enqueue_onchain=False,  # 测试用 off-chain (EAS 部分单独由 test_friend_ledger 覆盖)
    )
    t.join(timeout=10)
    wall_time = time.time() - t0

    # 验证整流程
    assert sess.status == "completed", f"sess={sess.to_dict()}"
    assert sess.proxy_method == "injected-mock"
    assert sess.proxy_text == "OK from bob's claude"
    assert sess.tokens_used == 123
    assert sess.ledger_entry_id is not None
    assert len(proxy_calls) == 1

    # ledger 真累积
    led = ReciprocityLedger(db_path=tmp_dbs["ledger"], self_did="alice.sisoul.eth")
    try:
        bal = led.query_balance("bob.sisoul.eth")
        assert bal.borrowed_total == 123
        assert bal.lent_total == 0
        # lend request 标 completed
        store = LendStore(db_path=tmp_dbs["lend"], pending_file=tmp_dbs["pending"])
        try:
            req = store.get(sess.lend_request_id)  # type: ignore[arg-type]
            assert req.status == "completed"
        finally:
            store.close()
    finally:
        led.close()

    print(f"\n[E2E per-request flow] wall_time = {wall_time:.2f}s")
    assert wall_time < 10, f"flow too slow: {wall_time}s"


# ── E2E flow 2: 互惠 ledger 不平衡 warning 触发 ─────────────────────────────


def test_imbalance_warning_after_repeated_borrow(tmp_dbs: dict[str, Path]) -> None:
    """Alice 连续借 Bob 10 次 (各 1000 tokens), Bob 借 Alice 0 → ratio inf → warning."""
    set_mock_proxy(lambda **kw: ProxyResult(
        text="ok", tokens_used=1000, model_used="m", method="injected-mock",
    ))

    for i in range(10):
        sess = borrow_resource(
            "alice.sisoul.eth", "bob.sisoul.eth", "llm_quota", 1000, "claude-opus-4-7",
            force_mode="strong-tie-auto",
            lend_db=tmp_dbs["lend"],
            pending_file=tmp_dbs["pending"],
            ledger_db=tmp_dbs["ledger"],
            enqueue_onchain=False,
        )
        assert sess.status == "completed", f"iter {i} failed: {sess.error}"

    led = ReciprocityLedger(db_path=tmp_dbs["ledger"], self_did="alice.sisoul.eth")
    try:
        bal = led.query_balance("bob.sisoul.eth")
        assert bal.borrowed_total == 10000
        assert bal.imbalance_warning is True
        assert bal.direction_imbalance == "borrower-heavy"

        warnings = led.list_imbalance_warnings()
        assert any(w.friend_did == "bob.sisoul.eth" for w in warnings)
    finally:
        led.close()


# ── E2E flow 3: 互惠 → 不再 warning ───────────────────────────────────────


def test_balanced_after_mutual_borrow(tmp_dbs: dict[str, Path]) -> None:
    """先 imbalance, 然后 Bob 也借 Alice → ratio → 1 → 不 warning."""
    set_mock_proxy(lambda **kw: ProxyResult(
        text="ok", tokens_used=1000, model_used="m", method="injected-mock",
    ))

    # Alice 借 Bob 5 次
    for _ in range(5):
        borrow_resource(
            "alice.sisoul.eth", "bob.sisoul.eth", "llm_quota", 1000, "claude-opus-4-7",
            force_mode="strong-tie-auto",
            lend_db=tmp_dbs["lend"],
            pending_file=tmp_dbs["pending"],
            ledger_db=tmp_dbs["ledger"],
            enqueue_onchain=False,
        )

    # Bob 借 Alice 5 次 (同机模拟; lend store 区分 borrower/lender 字段)
    for _ in range(5):
        borrow_resource(
            "bob.sisoul.eth", "alice.sisoul.eth", "llm_quota", 1000, "claude-sonnet",
            force_mode="strong-tie-auto",
            lend_db=tmp_dbs["lend"],
            pending_file=tmp_dbs["pending"],
            ledger_db=tmp_dbs["ledger"],
            enqueue_onchain=False,
        )

    led = ReciprocityLedger(db_path=tmp_dbs["ledger"], self_did="alice.sisoul.eth")
    try:
        bal = led.query_balance("bob.sisoul.eth")
        # Alice 借入 5000, 借出 5000
        assert bal.borrowed_total == 5000
        assert bal.lent_total == 5000
        assert bal.direction_imbalance == "balanced"
        assert bal.imbalance_warning is False
    finally:
        led.close()


# ── E2E flow 4: deny → 不写 ledger ─────────────────────────────────────────


def test_deny_does_not_write_ledger(tmp_dbs: dict[str, Path]) -> None:
    def bob_denies() -> None:
        time.sleep(0.2)
        store = LendStore(db_path=tmp_dbs["lend"], pending_file=tmp_dbs["pending"])
        try:
            ps = store.list_pending()
            if ps:
                store.deny_lend(ps[0].id, "no")
        finally:
            store.close()

    set_mock_proxy(lambda **kw: ProxyResult(text="x", tokens_used=1, model_used="m"))
    t = threading.Thread(target=bob_denies, daemon=True)
    t.start()
    sess = borrow_resource(
        "alice.sisoul.eth", "bob.sisoul.eth", "llm_quota", 100, "x",
        force_mode="per-request",
        per_request_timeout_sec=3.0,
        lend_db=tmp_dbs["lend"],
        pending_file=tmp_dbs["pending"],
        ledger_db=tmp_dbs["ledger"],
        enqueue_onchain=False,
    )
    t.join(timeout=5)
    assert sess.status == "lender-denied"
    assert sess.ledger_entry_id is None

    led = ReciprocityLedger(db_path=tmp_dbs["ledger"], self_did="alice.sisoul.eth")
    try:
        bal = led.query_balance("bob.sisoul.eth")
        assert bal.borrowed_total == 0
    finally:
        led.close()


# ── E2E flow 5: proxy 失败 → 不写 ledger (反向 sanity) ────────────────────


def test_proxy_failure_does_not_write_ledger(tmp_dbs: dict[str, Path]) -> None:
    def bad_proxy(**kw: object) -> ProxyResult:
        raise RuntimeError("anthropic 503")

    set_mock_proxy(bad_proxy)
    sess = borrow_resource(
        "alice.sisoul.eth", "bob.sisoul.eth", "llm_quota", 100, "x",
        force_mode="strong-tie-auto",
        lend_db=tmp_dbs["lend"],
        pending_file=tmp_dbs["pending"],
        ledger_db=tmp_dbs["ledger"],
        enqueue_onchain=False,
    )
    assert sess.status == "proxy-failed"
    assert sess.ledger_entry_id is None

    led = ReciprocityLedger(db_path=tmp_dbs["ledger"], self_did="alice.sisoul.eth")
    try:
        bal = led.query_balance("bob.sisoul.eth")
        assert bal.borrowed_total == 0
    finally:
        led.close()


# ── E2E flow 6: 上链 enqueue 真路径 (EAS AttestQueue) ─────────────────────


def test_onchain_enqueue_writes_attest_queue(tmp_dbs: dict[str, Path],
                                                  monkeypatch: pytest.MonkeyPatch,
                                                  tmp_path: Path) -> None:
    """default attest_queue.db 路径在 ~/.sisoul/; 用 monkeypatch HOME 隔离."""
    monkeypatch.setenv("HOME", str(tmp_path))
    set_mock_proxy(lambda **kw: ProxyResult(
        text="ok", tokens_used=42, model_used="m", method="injected-mock",
    ))
    sess = borrow_resource(
        "alice.sisoul.eth", "bob.sisoul.eth", "llm_quota", 100, "x",
        force_mode="strong-tie-auto",
        lend_db=tmp_dbs["lend"],
        pending_file=tmp_dbs["pending"],
        ledger_db=tmp_dbs["ledger"],
        enqueue_onchain=True,  # 真走 AttestQueue
    )
    assert sess.status == "completed"

    # 看 attest_queue 真写入
    from sisoul.onchain.eas import AttestQueue
    q = AttestQueue()  # 默认 ~/.sisoul/attest_queue.db (HOME 已被 monkeypatch)
    try:
        items = q.all_items(limit=10)
        # 至少 1 条 action_type='resource-usage'
        ru = [it for it in items if it.action_type == "resource-usage"]
        assert len(ru) >= 1, f"expected ≥1 resource-usage attest, got {[it.action_type for it in items]}"
    finally:
        q.close()
