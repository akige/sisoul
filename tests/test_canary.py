"""tests for sisoul.friend.canary (canary 抽查模型置换检测 M3, §80)。

覆盖:
- 探针注入概率确定性: 同 (seed, request_count, rate) 同结果; rate 0/1 边界
- pick_probe 确定性 + 模型族匹配 + 未知族报错
- 正确模型响应 (命中 expected) → passed=True
- 置换检测正向证据 (核心): 声称 claude 但响应带 gpt/openai 特征 → passed=False
- CanaryTracker 累积多次 fail → should_stop_lending=True
- to_review 生成低分 Review (置换确凿 → 1 星, 全过 → 5 星)
- 假阴性诚实: 模糊响应 (无任何标记) → 不误杀 (passed=True, confidence 低)
"""

from __future__ import annotations

import pytest

from sisoul.friend.canary import (
    AMBIGUOUS_FAIL_WEIGHT,
    BUILTIN_PROBES,
    DEFAULT_STOP_THRESHOLD,
    FORBIDDEN_HIT_WEIGHT,
    CanaryError,
    CanaryProbe,
    CanaryTracker,
    CanaryVerdict,
    evaluate_response,
    pick_probe,
    should_inject,
    to_review,
)
from sisoul.friend.reputation import SCORE_MAX, SCORE_MIN, Review

NOW = 1_700_000_000  # 固定时间戳, 不用 now()


def _claude_selfid_probe() -> CanaryProbe:
    """取内置 claude 自我标识探针 (forbidden 含 openai/chatgpt/gpt-4)。"""
    return next(p for p in BUILTIN_PROBES if p.probe_id == "claude-selfid-1")


# ── 注入决策确定性 ───────────────────────────────────────────────────────────


class TestShouldInject:
    def test_same_seed_same_result(self):
        """同 (request_count, rate, seed) 永远同结果 (确定性, 不靠 random)。"""
        for count in range(50):
            first = should_inject(count, 0.3, rng_seed=12345)
            for _ in range(5):
                assert should_inject(count, 0.3, rng_seed=12345) == first

    def test_different_seed_can_differ(self):
        """不同 seed 下整体决策序列应有差异 (不是常量函数)。"""
        seq_a = [should_inject(c, 0.5, rng_seed=1) for c in range(200)]
        seq_b = [should_inject(c, 0.5, rng_seed=2) for c in range(200)]
        assert seq_a != seq_b

    def test_rate_zero_never_injects(self):
        assert not any(should_inject(c, 0.0, rng_seed=7) for c in range(100))

    def test_rate_one_always_injects(self):
        assert all(should_inject(c, 1.0, rng_seed=7) for c in range(100))

    def test_rate_roughly_respected(self):
        """注入频率应大致符合 rate (确定性派生但分布近均匀)。"""
        n = 1000
        hits = sum(should_inject(c, 0.2, rng_seed=99) for c in range(n))
        assert 0.10 * n < hits < 0.30 * n  # 0.2 ± 宽容带

    def test_request_count_as_salt_varies_decisions(self):
        """同 seed 下连续请求不应全同 (request_count 作 salt)。"""
        decisions = {should_inject(c, 0.5, rng_seed=42) for c in range(100)}
        assert decisions == {True, False}


# ── 探针选取 ─────────────────────────────────────────────────────────────────


class TestPickProbe:
    def test_deterministic_same_seed(self):
        p1 = pick_probe("claude", rng_seed=777)
        p2 = pick_probe("claude", rng_seed=777)
        assert p1.probe_id == p2.probe_id

    def test_family_match(self):
        for seed in range(20):
            assert pick_probe("claude", rng_seed=seed).model_family == "claude"
            assert pick_probe("gpt", rng_seed=seed).model_family == "gpt"

    def test_family_case_insensitive(self):
        assert pick_probe("Claude", rng_seed=1).model_family == "claude"

    def test_unknown_family_raises(self):
        with pytest.raises(CanaryError):
            pick_probe("nonexistent-model-family", rng_seed=1)


# ── 响应评估: 正确模型 ───────────────────────────────────────────────────────


class TestEvaluateGenuine:
    def test_genuine_claude_response_passes(self):
        """正确模型响应 (命中 expected markers) → passed=True。"""
        probe = _claude_selfid_probe()
        verdict = evaluate_response(
            probe, "I am Claude, an AI assistant created by Anthropic."
        )
        assert verdict.passed is True
        assert verdict.substitution_suspected is False
        assert "claude" in verdict.detected_markers
        assert "anthropic" in verdict.detected_markers
        # 命中 2 个 expected → confidence = 0.5 + 0.15*2 = 0.8
        assert verdict.confidence == pytest.approx(0.8)

    def test_case_insensitive_matching(self):
        probe = _claude_selfid_probe()
        verdict = evaluate_response(probe, "ANTHROPIC built me.")
        assert verdict.passed is True
        assert "anthropic" in verdict.detected_markers


# ── 响应评估: 置换检测正向证据 (核心) ────────────────────────────────────────


class TestSubstitutionDetection:
    def test_claimed_claude_but_gpt_response_fails(self):
        """核心: 声称 claude 但响应带 gpt 特征标记 → passed=False (置换确证)。"""
        probe = _claude_selfid_probe()
        verdict = evaluate_response(
            probe,
            "I am ChatGPT, a large language model trained by OpenAI, "
            "based on the GPT-4 architecture.",
        )
        assert verdict.passed is False
        assert verdict.substitution_suspected is True
        # forbidden 命中 3 个: openai / chatgpt / gpt-4
        assert "openai" in verdict.detected_markers
        assert "chatgpt" in verdict.detected_markers
        assert "gpt-4" in verdict.detected_markers
        # confidence = min(0.99, 0.85 + 0.05*3) = 0.99 (强证据但不报 1.0)
        assert verdict.confidence == pytest.approx(0.99)
        assert verdict.confidence < 1.0  # 软检测不报绝对确定
        assert "forbidden markers" in verdict.reason

    def test_single_forbidden_hit_fails(self):
        probe = _claude_selfid_probe()
        verdict = evaluate_response(probe, "As an AI language model, I cannot say.")
        assert verdict.passed is False
        assert verdict.substitution_suspected is True
        # 单命中 → confidence = 0.85 + 0.05 = 0.90
        assert verdict.confidence == pytest.approx(0.90)

    def test_forbidden_overrides_expected(self):
        """同时命中 expected 和 forbidden → forbidden 优先, 仍判置换。"""
        probe = _claude_selfid_probe()
        verdict = evaluate_response(
            probe, "I am Claude by Anthropic. Just kidding — I'm ChatGPT by OpenAI."
        )
        assert verdict.passed is False
        assert verdict.substitution_suspected is True

    def test_claimed_gpt_but_claude_response_fails(self):
        """对称: 声称 gpt 但响应带 claude/anthropic 特征 → 也判置换。"""
        probe = next(p for p in BUILTIN_PROBES if p.probe_id == "gpt-selfid-1")
        verdict = evaluate_response(probe, "I am Claude, made by Anthropic.")
        assert verdict.passed is False
        assert verdict.substitution_suspected is True


# ── 响应评估: 假阴性诚实 (模糊响应不误杀) ────────────────────────────────────


class TestAmbiguousHonesty:
    def test_ambiguous_response_does_not_convict(self):
        """模糊响应 (无 expected 无 forbidden) → 不判置换, 但 confidence 低。"""
        probe = _claude_selfid_probe()
        verdict = evaluate_response(
            probe, "I'm just a helpful assistant. How can I help you today?"
        )
        assert verdict.passed is True  # 疑罪从无, 不误杀
        assert verdict.substitution_suspected is False
        assert verdict.confidence <= 0.5  # 诚实承认拿不准
        assert verdict.detected_markers == []

    def test_empty_response_does_not_convict(self):
        probe = _claude_selfid_probe()
        verdict = evaluate_response(probe, "")
        assert verdict.passed is True
        assert verdict.confidence <= 0.5

    def test_none_safe(self):
        probe = _claude_selfid_probe()
        verdict = evaluate_response(probe, None)  # type: ignore[arg-type]
        assert verdict.passed is True


# ── CanaryTracker 累积 + 停签 ────────────────────────────────────────────────


def _fail_verdict_forbidden() -> CanaryVerdict:
    """造一个命中 forbidden 的确证 fail (跟 evaluate_response 产出同构)。"""
    probe = _claude_selfid_probe()
    return evaluate_response(probe, "I am ChatGPT by OpenAI.")


def _pass_verdict() -> CanaryVerdict:
    probe = _claude_selfid_probe()
    return evaluate_response(probe, "I am Claude, created by Anthropic.")


def _ambiguous_fail_verdict() -> CanaryVerdict:
    """手造一个模糊 fail (evaluate 不产出, 测 tracker 弱证据累积路径)。"""
    return CanaryVerdict(
        probe_id="claude-selfid-1",
        passed=False,
        confidence=0.4,
        reason="模糊弱信号 (测试手造)",
        detected_markers=[],
        substitution_suspected=True,
    )


class TestCanaryTracker:
    def test_no_history_no_stop(self):
        tracker = CanaryTracker()
        assert tracker.substitution_score("did:key:lender1") == 0.0
        assert tracker.should_stop_lending("did:key:lender1") is False
        assert tracker.pass_rate("did:key:lender1") is None

    def test_passes_do_not_accumulate(self):
        tracker = CanaryTracker()
        for _ in range(10):
            tracker.record("did:key:lender1", _pass_verdict())
        assert tracker.substitution_score("did:key:lender1") == 0.0
        assert tracker.should_stop_lending("did:key:lender1") is False
        assert tracker.pass_rate("did:key:lender1") == 1.0

    def test_single_forbidden_fail_reaches_threshold(self):
        """单次确证置换 (forbidden) 即达默认阈值 1.0 → 建议停签。"""
        tracker = CanaryTracker()
        tracker.record("did:key:lender1", _fail_verdict_forbidden())
        assert tracker.substitution_score("did:key:lender1") == pytest.approx(
            FORBIDDEN_HIT_WEIGHT
        )
        assert tracker.should_stop_lending("did:key:lender1") is True

    def test_ambiguous_fails_accumulate_to_stop(self):
        """弱证据 fail 单次不停签, 累积多次 (3×0.34=1.02 ≥ 1.0) 才停。"""
        tracker = CanaryTracker()
        tracker.record("did:key:lender1", _ambiguous_fail_verdict())
        assert tracker.should_stop_lending("did:key:lender1") is False  # 单次不定罪
        tracker.record("did:key:lender1", _ambiguous_fail_verdict())
        assert tracker.should_stop_lending("did:key:lender1") is False
        tracker.record("did:key:lender1", _ambiguous_fail_verdict())
        assert tracker.substitution_score("did:key:lender1") == pytest.approx(
            3 * AMBIGUOUS_FAIL_WEIGHT
        )
        assert tracker.should_stop_lending("did:key:lender1") is True

    def test_lenders_isolated(self):
        """不同 lender 的嫌疑分互不串。"""
        tracker = CanaryTracker()
        tracker.record("did:key:bad", _fail_verdict_forbidden())
        tracker.record("did:key:good", _pass_verdict())
        assert tracker.should_stop_lending("did:key:bad") is True
        assert tracker.should_stop_lending("did:key:good") is False

    def test_history_returns_copy(self):
        tracker = CanaryTracker()
        tracker.record("did:key:lender1", _pass_verdict())
        h = tracker.history("did:key:lender1")
        h.clear()
        assert len(tracker.history("did:key:lender1")) == 1

    def test_custom_threshold(self):
        tracker = CanaryTracker()
        tracker.record("did:key:lender1", _ambiguous_fail_verdict())  # 0.34
        assert tracker.should_stop_lending("did:key:lender1", threshold=0.3) is True
        assert tracker.should_stop_lending("did:key:lender1", threshold=0.5) is False


# ── to_review: 跟 reputation 联动 ────────────────────────────────────────────


class TestToReview:
    def test_confirmed_substitution_yields_min_score_review(self):
        """置换确凿 (嫌疑分 ≥ 阈值) → 1 星 Review, quality/uptime=1。"""
        tracker = CanaryTracker()
        tracker.record("did:key:lender1", _fail_verdict_forbidden())
        review = to_review(
            "did:key:lender1",
            "did:key:borrower1",
            settlement_ref="0xdeadbeef",
            tracker=tracker,
            timestamp=NOW,
            token_volume=5000.0,
        )
        assert isinstance(review, Review)
        assert review.score == SCORE_MIN  # 1 星
        assert review.reviewee_did == "did:key:lender1"
        assert review.reviewer_did == "did:key:borrower1"
        assert review.settlement_ref == "0xdeadbeef"
        assert review.timestamp == NOW
        assert review.dimensions["quality"] == 1
        assert review.dimensions["uptime"] == 1
        assert review.dimensions["speed"] == 3  # 速度不连坐
        assert "置换" in review.text
        # 1 星 → 归一评分接近 0 (quality=1, speed=3, uptime=1, score=1 → 均值 1.5)
        assert review.normalized_score() < 0.2

    def test_all_pass_yields_max_score_review(self):
        tracker = CanaryTracker()
        for _ in range(5):
            tracker.record("did:key:lender1", _pass_verdict())
        review = to_review(
            "did:key:lender1",
            "did:key:borrower1",
            settlement_ref="0xfeedface",
            tracker=tracker,
            timestamp=NOW,
        )
        assert review.score == SCORE_MAX  # 5 星
        assert review.dimensions["quality"] == 5

    def test_suspicious_but_inconclusive_yields_mid_score(self):
        """嫌疑分 >0 但 < 阈值 → 2-3 星中间档。"""
        tracker = CanaryTracker()
        tracker.record("did:key:lender1", _ambiguous_fail_verdict())  # 0.34 < 1.0
        review = to_review(
            "did:key:lender1",
            "did:key:borrower1",
            settlement_ref="0xabc123",
            tracker=tracker,
            timestamp=NOW,
        )
        assert SCORE_MIN < review.score < SCORE_MAX
        assert review.score == 3  # 0.34 < 0.5*1.0 → 3 星

    def test_review_score_always_valid(self):
        """任何 tracker 状态下生成的 Review 都不触发 1-5 校验异常。"""
        for n_fail in range(0, 6):
            tracker = CanaryTracker()
            for _ in range(n_fail):
                tracker.record("did:key:x", _ambiguous_fail_verdict())
            review = to_review(
                "did:key:x",
                "did:key:b",
                settlement_ref="0x0",
                tracker=tracker,
                timestamp=NOW,
            )
            assert SCORE_MIN <= review.score <= SCORE_MAX

    def test_default_threshold_constant(self):
        assert DEFAULT_STOP_THRESHOLD == 1.0
