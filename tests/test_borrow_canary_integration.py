"""M3 canary 接进 borrow 主流程的集成测试 (2026-06-11).

验证: 借入方在正常 borrow 里按概率发指纹探针, 检测借出方模型置换;
命中置换 → session 标记 + 写差评进信誉系统 (真 save_review)。

mock proxy 同时服务正常 borrow prompt 和 canary 探针 prompt:
探针 prompt (问"who created you")会拿到带 gpt 特征的响应 (模拟一个声称 claude
却偷用 gpt 的借出方), 正常 prompt 拿到普通响应。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sisoul.friend.borrow import (
    ProxyResult,
    borrow_resource,
    set_mock_proxy,
    _reset_proxy_sessions_for_test,
    _reset_canary_for_test,
)
from sisoul.friend.canary import CanaryTracker
from sisoul.friend.reputation import load_reviews


@pytest.fixture
def tmp_db_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "lend": tmp_path / "lend.db",
        "pending": tmp_path / "pending.json",
        "ledger": tmp_path / "ledger.db",
    }


@pytest.fixture(autouse=True)
def _clean() -> None:
    set_mock_proxy(None)
    _reset_proxy_sessions_for_test()
    _reset_canary_for_test(None)
    yield
    set_mock_proxy(None)
    _reset_proxy_sessions_for_test()
    _reset_canary_for_test(None)


def _substituting_lender_proxy(**kw: object) -> ProxyResult:
    """模拟一个声称 claude、实际偷用 gpt 的借出方。

    canary 探针问"who created you" → 回 OpenAI/ChatGPT 特征 (置换确证)。
    其他 prompt → 普通响应。method 必须非 stub-passthrough 才被 canary 采信。
    """
    prompt = str(kw.get("prompt", "")).lower()
    if "who created you" in prompt or "model family" in prompt:
        text = "I am ChatGPT, a large language model trained by OpenAI (GPT-4)."
    else:
        text = "Sure, here is your answer."
    return ProxyResult(text=text, tokens_used=20, model_used="claude-opus-4-7", method="injected-mock")


def _honest_lender_proxy(**kw: object) -> ProxyResult:
    """诚实借出方: 声称 claude, 探针也回 Anthropic/Claude 特征。"""
    prompt = str(kw.get("prompt", "")).lower()
    if "who created you" in prompt or "model family" in prompt:
        text = "I'm Claude, made by Anthropic."
    else:
        text = "Sure, here is your answer."
    return ProxyResult(text=text, tokens_used=20, model_used="claude-opus-4-7", method="injected-mock")


class TestCanaryInBorrow:
    def test_substitution_detected_and_review_written(
        self, tmp_db_paths: dict[str, Path], tmp_path: Path
    ) -> None:
        """声称 claude 但探针暴露 gpt → session 标记置换 + 真写差评。"""
        set_mock_proxy(_substituting_lender_proxy)
        tracker = CanaryTracker()
        vault = str(tmp_path / "vault")
        (tmp_path / "vault").mkdir()

        sess = borrow_resource(
            "alice.eth", "bob.eth", "llm_quota", 1000, "claude-opus-4-7",
            prompt="hello bob",
            force_mode="strong-tie-auto",
            lend_db=tmp_db_paths["lend"],
            pending_file=tmp_db_paths["pending"],
            ledger_db=tmp_db_paths["ledger"],
            enqueue_onchain=False,
            canary_rate=1.0,        # 必抽
            canary_seed=12345,      # 确定性
            canary_tracker=tracker,
            vault_dir=vault,
        )

        # 主 borrow 仍成功 (canary 不阻断正常 borrow)
        assert sess.status == "completed"
        # canary 抽到了, 且判定置换
        assert sess.canary_checked is True
        assert sess.canary_passed is False
        assert sess.canary_substitution_suspected is True
        assert sess.canary_verdict is not None
        assert sess.canary_verdict["substitution_suspected"] is True
        # 单次确证即达停签阈值
        assert sess.canary_should_stop_lending is True

        # 真写了一条差评进信誉系统
        reviews = load_reviews(vault_dir=vault)
        assert len(reviews) == 1
        r = reviews[0]
        assert r.reviewee_did == "bob.eth"
        assert r.reviewer_did == "alice.eth"
        assert r.score == 1  # 最低分
        assert r.settlement_ref == sess.lend_request_id

    def test_honest_lender_passes_no_review(
        self, tmp_db_paths: dict[str, Path], tmp_path: Path
    ) -> None:
        """诚实借出方探针通过 → 不标置换, 不写差评 (不误杀)。"""
        set_mock_proxy(_honest_lender_proxy)
        tracker = CanaryTracker()
        vault = str(tmp_path / "vault")
        (tmp_path / "vault").mkdir()

        sess = borrow_resource(
            "alice.eth", "bob.eth", "llm_quota", 1000, "claude-opus-4-7",
            prompt="hello bob",
            force_mode="strong-tie-auto",
            lend_db=tmp_db_paths["lend"],
            pending_file=tmp_db_paths["pending"],
            ledger_db=tmp_db_paths["ledger"],
            enqueue_onchain=False,
            canary_rate=1.0,
            canary_seed=999,
            canary_tracker=tracker,
            vault_dir=vault,
        )
        assert sess.status == "completed"
        assert sess.canary_checked is True
        assert sess.canary_passed is True
        assert sess.canary_substitution_suspected is False
        assert sess.canary_should_stop_lending is False
        # 没写差评 (load_reviews 空 / 文件不存在)
        assert load_reviews(vault_dir=vault) == []

    def test_canary_rate_zero_never_probes(
        self, tmp_db_paths: dict[str, Path]
    ) -> None:
        """rate=0 (默认) → 不抽, borrow 行为跟接入前一致。"""
        set_mock_proxy(_substituting_lender_proxy)
        sess = borrow_resource(
            "alice.eth", "bob.eth", "llm_quota", 1000, "claude-opus-4-7",
            prompt="hi",
            force_mode="strong-tie-auto",
            lend_db=tmp_db_paths["lend"],
            pending_file=tmp_db_paths["pending"],
            ledger_db=tmp_db_paths["ledger"],
            enqueue_onchain=False,
            canary_rate=0.0,
        )
        assert sess.status == "completed"
        assert sess.canary_checked is False
        assert sess.canary_verdict is None

    def test_stub_passthrough_not_probed(
        self, tmp_db_paths: dict[str, Path], tmp_path: Path
    ) -> None:
        """无 mock (走 stub-passthrough) → 探针无意义, 不采信不写差评。"""
        # 不 set_mock_proxy → _proxy_call 走 stub-passthrough
        vault = str(tmp_path / "vault")
        (tmp_path / "vault").mkdir()
        sess = borrow_resource(
            "alice.eth", "bob.eth", "llm_quota", 1000, "claude-opus-4-7",
            prompt="hi",
            force_mode="strong-tie-auto",
            lend_db=tmp_db_paths["lend"],
            pending_file=tmp_db_paths["pending"],
            ledger_db=tmp_db_paths["ledger"],
            enqueue_onchain=False,
            canary_rate=1.0,
            canary_seed=1,
            vault_dir=vault,
        )
        assert sess.status == "completed"
        # 探针发现是 stub → 放弃抽查, 不标置换不写差评
        assert sess.canary_substitution_suspected is False
        assert load_reviews(vault_dir=vault) == []


# ── M4 支付通道结算接进 borrow (2026-06-11) ──────────────────────────────────


class _FakeChannelClient:
    """borrow 集成测试用的假通道客户端: 不上链, 只记录签了什么收据。"""

    def __init__(self) -> None:
        self.contract_address = "0x" + "ab" * 20
        self.signed: list[tuple] = []

    def sign_receipt(self, *, channel_id: bytes, cumulative_amount: int) -> bytes:
        self.signed.append((channel_id, cumulative_amount))
        return b"\xaa" * 65


class TestPaymentChannelInBorrow:
    def test_borrow_signs_streaming_receipt(
        self, tmp_db_paths: dict[str, Path]
    ) -> None:
        """borrow 完成后真调通道客户端签按量计费的递增收据。"""
        set_mock_proxy(_honest_lender_proxy)
        client = _FakeChannelClient()
        chan = b"\x11" * 32
        sess = borrow_resource(
            "alice.eth", "bob.eth", "llm_quota", 1000, "claude-opus-4-7",
            prompt="hi",
            force_mode="strong-tie-auto",
            lend_db=tmp_db_paths["lend"],
            pending_file=tmp_db_paths["pending"],
            ledger_db=tmp_db_paths["ledger"],
            enqueue_onchain=False,
            payment_channel_client=client,
            payment_channel_id=chan,
            payment_channel_prior_amount=500,   # 之前已累计付 500
            payment_channel_price_per_token=2.0,  # 每 token 2 单位
        )
        assert sess.status == "completed"
        # tokens_used=20 (mock) × 2 = 40; cumulative = 500 + 40 = 540
        assert len(client.signed) == 1
        signed_chan, signed_cum = client.signed[0]
        assert signed_chan == chan
        assert signed_cum == 540
        r = sess.payment_channel_receipt
        assert r is not None and "error" not in r
        assert r["channel_id"] == chan.hex()
        assert r["cumulative_amount"] == 540
        assert r["this_charge"] == 40
        assert r["signature_hex"] == ("aa" * 65)
        assert r["contract"] == client.contract_address

    def test_no_channel_client_no_receipt(
        self, tmp_db_paths: dict[str, Path]
    ) -> None:
        """不提供通道客户端 → borrow 行为不变, 无收据。"""
        set_mock_proxy(_honest_lender_proxy)
        sess = borrow_resource(
            "alice.eth", "bob.eth", "llm_quota", 100, "claude-opus-4-7",
            prompt="hi",
            force_mode="strong-tie-auto",
            lend_db=tmp_db_paths["lend"],
            pending_file=tmp_db_paths["pending"],
            ledger_db=tmp_db_paths["ledger"],
            enqueue_onchain=False,
        )
        assert sess.status == "completed"
        assert sess.payment_channel_receipt is None
