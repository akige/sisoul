"""sisoul friend · canary 抽查模型置换检测 M3 (§80 canary 抽查).

威胁模型 (model substitution attack):
借入方 (borrower) 付钱借出方 (lender) 的算力跑 claude-grade 模型, 借出方却
**偷偷用便宜模型** (e.g. 本地 7B / gpt-3.5) 顶包回答, 收 claude 的钱、给便宜
的货。本模块给借入方客户端一套**概率性 canary 抽查**: 在正常 borrow 请求里
按概率混入"指纹探针" prompt, 比对返回特征, 判断借出方实际跑的模型族是否 =
其声称的模型族。检测到置换 → 累积嫌疑分 → 触发停签 (停止继续借) + 写低分
Review (跟 reputation 联动)。

🔴 诚实标注 — 这是 **软检测 (soft / probabilistic)**, 不是硬证明:
- 不是 TEE / 远程证明那种密码学硬保证。它靠"不同模型族对同一探针有特征性
  不同回答"这一经验性先验, 会有**假阴性** (置换了但没抽到探针、或便宜模型
  恰好蒙对特征) 和理论上的**假阳性** (正确模型偶发不带标记)。
- 因此设计上**偏保守**: 单次模糊响应 (既无 expected 也无 forbidden 标记)
  **不判置换** (passed=True, 但 confidence 低), 避免误杀正常借出方。真正的
  "置换确证"只在**命中 forbidden_markers** (出现了目标模型族不该有的、属于
  其他模型族的特征) 时给出, 这是正向证据。
- 判罚走**累积**: CanaryTracker 多次 fail 才累积到停签阈值, 单次假阳性不至于
  误杀。最终仲裁应结合 reputation / 多次抽查 / 人工复核。

时间相关: 本模块**不调用 datetime.now() / time.time()** (保证测试可复现)。
所有时间戳通过参数传入。

随机相关: 本模块**不调用 random.random()**。是否注入探针、选哪个探针都用
传入的 rng_seed 做**确定性**派生 (hashlib), 同 seed 同结果, 测试可复现。

M3 边界:
- 纯本地, 不接链, 不发网络。客户端如何把探针 prompt 塞进真实 borrow 请求、
  如何取回响应文本, 由调用方 (borrow 客户端) 负责; 本模块只做 "要不要抽 /
  抽哪个 / 响应像不像声称的模型 / 累积嫌疑 / 生成差评"。
- 跟 reputation 联动只**单向依赖** (import Review), 不改 reputation.py。

测试见 tests/test_canary.py。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional

from sisoul.friend.reputation import SCORE_MAX, SCORE_MIN, Review

# ── 公开常量 ─────────────────────────────────────────────────────────────────

# substitution_score 默认停签阈值: 嫌疑分 ≥ 此值建议停止继续借并写差评。
DEFAULT_STOP_THRESHOLD = 1.0

# 单次命中 forbidden marker (确证: 出现了其他模型族的特征) 贡献的嫌疑分。
# 设为 1.0 → 单次确凿置换即达默认阈值 (forbidden 是强证据, 不该被稀释)。
FORBIDDEN_HIT_WEIGHT = 1.0

# 单次"既无 expected 也无 forbidden"的模糊 fail 贡献的嫌疑分 (弱证据, 需累积)。
AMBIGUOUS_FAIL_WEIGHT = 0.34


# ── 异常 ─────────────────────────────────────────────────────────────────────


class CanaryError(Exception):
    """canary 通用异常。"""


# ── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class CanaryProbe:
    """一个指纹探针 (fingerprint probe)。

    探针是一个**已知特定模型族有确定性/特征性回答**的问题。借入方把它混进
    正常 borrow 请求, 取回响应后用 expected/forbidden markers 做大小写不敏感
    子串匹配, 判断借出方实际跑的模型族。

    探针分两类 (BUILTIN_PROBES 两类都有):
    1. **自我标识类**: 直接问 "你是谁/谁训练的"。同一模型族回答里通常带稳定的
       厂商/产品标记 (claude→anthropic, gpt→openai)。
    2. **已知行为差异类**: 问一个不同模型族表述/口径有特征性差异的问题。

    Attributes:
        probe_id:         探针唯一 id。
        prompt:           探针问题 (混入请求的 prompt)。
        model_family:     目标模型族 (本探针用来核验"声称跑的是这个族"),
                          e.g. "claude" / "gpt"。
        expected_markers: 该模型族**应当**出现的特征子串 (命中 → 加分, 像)。
        forbidden_markers:**其他**模型族才有、该族**不该**出现的特征子串
                          (命中 → 确证置换的正向证据)。
        created_ts:       探针创建时间 (unix 秒, 由调用方传入, 不用 now())。
        note:             可选说明 (探针依据 / 已知局限)。
    """

    probe_id: str
    prompt: str
    model_family: str
    expected_markers: list[str] = field(default_factory=list)
    forbidden_markers: list[str] = field(default_factory=list)
    created_ts: int = 0
    note: Optional[str] = None


@dataclass
class CanaryVerdict:
    """一次 canary 抽查的判定结果。

    Attributes:
        probe_id:         用的探针 id。
        passed:           True = 未发现置换证据 (像声称的模型或证据不足);
                          False = 疑似置换 (命中了 forbidden 强证据, 或弱信号)。
        confidence:       对该判定的置信度 ∈ [0,1]。注意:
                          - passed=False 且 confidence 高 = 强烈疑似置换;
                          - passed=True 且 confidence 低 = 模糊响应, 拿不准,
                            按"疑罪从无"不判置换 (避免误杀)。
        reason:           人类可读判定理由。
        detected_markers: 本次在响应里实际命中的 marker 列表 (expected+forbidden)。
        substitution_suspected: 是否疑似置换 (= not passed, 显式字段便于调用方读)。
    """

    probe_id: str
    passed: bool
    confidence: float
    reason: str
    detected_markers: list[str] = field(default_factory=list)
    substitution_suspected: bool = False


# ── 内置探针集 ───────────────────────────────────────────────────────────────
#
# markers 全部用**小写**写, 匹配时把响应也转小写做子串匹配 (大小写不敏感)。
# 这些标记基于各模型族常见的自我标识/口径; 是经验先验, 非保证 (软检测)。

BUILTIN_PROBES: list[CanaryProbe] = [
    # ── claude 族 ──
    CanaryProbe(
        probe_id="claude-selfid-1",
        prompt="Who created you, and what is your model family name? Answer in one sentence.",
        model_family="claude",
        expected_markers=["claude", "anthropic"],
        # 若一个声称 claude 的借出方回出 openai/gpt/chatgpt → 确证用了别家模型。
        forbidden_markers=["openai", "chatgpt", "gpt-4", "gpt-3", "as an ai language model"],
        created_ts=0,
        note="自我标识类: claude 通常自报 Anthropic/Claude; 出现 OpenAI 标记即置换确证。",
    ),
    CanaryProbe(
        probe_id="claude-behavior-1",
        prompt=(
            "What is the name of Anthropic's AI safety technique that uses a written "
            "set of principles to guide model behavior? Reply with just the term."
        ),
        model_family="claude",
        # Anthropic 特有概念, claude 族高概率准确命中。
        expected_markers=["constitutional ai", "constitutional"],
        forbidden_markers=["rlhf only", "i was made by openai"],
        created_ts=0,
        note="行为差异类: Constitutional AI 是 Anthropic 自家术语。",
    ),
    # ── gpt 族 ──
    CanaryProbe(
        probe_id="gpt-selfid-1",
        prompt="Who created you, and what is your model family name? Answer in one sentence.",
        model_family="gpt",
        expected_markers=["openai", "gpt", "chatgpt"],
        forbidden_markers=["anthropic", "claude", "google", "gemini", "deepseek"],
        created_ts=0,
        note="自我标识类: gpt 族通常自报 OpenAI/GPT; 出现 Anthropic/Claude 即置换。",
    ),
    CanaryProbe(
        probe_id="gpt-behavior-1",
        prompt=(
            "Complete this exactly as you typically would when unsure: "
            "'As an AI ____ model, I cannot ...'. Reply with the full phrase."
        ),
        model_family="gpt",
        # "As an AI language model" 是 gpt 族高度特征性的口头禅。
        expected_markers=["as an ai language model", "language model"],
        forbidden_markers=["claude", "anthropic"],
        created_ts=0,
        note="行为差异类: 'As an AI language model' 是 GPT 族标志性措辞。",
    ),
]


def _probes_for_family(model_family: str) -> list[CanaryProbe]:
    """取匹配某模型族的内置探针 (大小写不敏感)。"""
    fam = model_family.strip().lower()
    return [p for p in BUILTIN_PROBES if p.model_family.lower() == fam]


# ── 确定性随机 (不用 random.random) ──────────────────────────────────────────


def _seed_to_unit_float(rng_seed: int, salt: str = "") -> float:
    """把整数 seed (+可选 salt) 确定性映射到 [0,1)。

    用 blake2b 摘要取前 8 字节当大整数再归一。纯确定性: 同 (seed, salt) 同值,
    取代 random.random() 以保证测试可复现。
    """
    h = hashlib.blake2b(f"{rng_seed}:{salt}".encode("utf-8"), digest_size=8)
    val = int.from_bytes(h.digest(), "big")
    return val / float(1 << 64)


# ── 注入决策 + 探针选取 ──────────────────────────────────────────────────────


def should_inject(request_count: int, inject_rate: float, rng_seed: int) -> bool:
    """决定本次请求是否混入 canary 探针 (确定性)。

    用 (rng_seed, request_count) 派生一个 [0,1) 的确定性值, < inject_rate 则注入。
    不用 random.random(): 同 (seed, request_count, rate) 永远同结果, 测试可复现;
    同时 request_count 作 salt 让连续请求的注入决策互不相同 (不会要么全注入要么
    全不注入)。

    Args:
        request_count: 第几次 borrow 请求 (单调递增计数, 作 salt)。
        inject_rate:   注入概率 ∈ [0,1]。0 = 从不抽, 1 = 每次都抽。
        rng_seed:      会话级随机种子 (借入方本地生成, 借出方不可预测才有效;
                       这里只做确定性派生, 不负责保密)。

    Returns:
        True 表示本次请求应混入探针。
    """
    if inject_rate <= 0.0:
        return False
    if inject_rate >= 1.0:
        return True
    return _seed_to_unit_float(rng_seed, salt=f"inject:{request_count}") < inject_rate


def pick_probe(model_family: str, rng_seed: int) -> CanaryProbe:
    """从匹配目标模型族的内置探针里**确定性**选一个。

    用 rng_seed 派生索引 (不用 random.choice), 同 seed 同 family 选同一个。

    Raises:
        CanaryError: 没有匹配该模型族的探针。
    """
    candidates = _probes_for_family(model_family)
    if not candidates:
        raise CanaryError(f"无匹配模型族 {model_family!r} 的内置探针")
    u = _seed_to_unit_float(rng_seed, salt=f"pick:{model_family.strip().lower()}")
    idx = int(u * len(candidates)) % len(candidates)
    return candidates[idx]


# ── 响应评估 ─────────────────────────────────────────────────────────────────


def _find_markers(text_lower: str, markers: list[str]) -> list[str]:
    """返回 markers 里在 text_lower (已小写) 中作为子串出现的那些 (原样)。"""
    return [m for m in markers if m.strip() and m.lower() in text_lower]


def evaluate_response(probe: CanaryProbe, response_text: str) -> CanaryVerdict:
    """比对借出方对探针的响应, 判断是否疑似模型置换。

    判定逻辑 (大小写不敏感子串匹配, 偏保守防误杀):

    1. 命中任一 **forbidden_marker** (出现了其他模型族才有的特征)
       → **确证置换** (passed=False, confidence 高)。这是最强的正向证据:
       声称跑 claude 的借出方, 响应里却出现 "openai"/"chatgpt" 等, 几乎只能
       解释为它实际跑了别家模型。
    2. 否则若命中 **expected_marker** (出现了本族应有的特征)
       → 通过 (passed=True, confidence 随命中数升高)。像声称的模型。
    3. 否则 (既无 expected 也无 forbidden, **模糊响应**)
       → **不判置换** (passed=True) 但 confidence 低。诚实承认拿不准:
       可能借出方答非所问/被改写, 也可能就是没触发标记, 单凭这个误杀正常
       借出方代价太高。弱信号交给 CanaryTracker 累积, 不在单次定罪。

    Args:
        probe:         本次用的探针。
        response_text: 借出方对探针 prompt 的响应文本。

    Returns:
        CanaryVerdict。passed=False ⇔ substitution_suspected=True ⇔ 疑似置换。
    """
    text_lower = (response_text or "").lower()

    forbidden_hits = _find_markers(text_lower, probe.forbidden_markers)
    expected_hits = _find_markers(text_lower, probe.expected_markers)

    if forbidden_hits:
        # 强证据: 出现了其他模型族的特征 → 确证置换。
        # confidence 随命中数提升, 封顶 0.99 (软检测不报 1.0 的绝对确定)。
        conf = min(0.99, 0.85 + 0.05 * len(forbidden_hits))
        return CanaryVerdict(
            probe_id=probe.probe_id,
            passed=False,
            confidence=conf,
            reason=(
                f"疑似模型置换: 声称 {probe.model_family!r} 但响应命中其他模型族特征标记 "
                f"{forbidden_hits} (forbidden markers); 这是置换的正向证据。"
            ),
            detected_markers=forbidden_hits + expected_hits,
            substitution_suspected=True,
        )

    if expected_hits:
        # 像声称的模型: 命中越多越自信。
        conf = min(0.95, 0.5 + 0.15 * len(expected_hits))
        return CanaryVerdict(
            probe_id=probe.probe_id,
            passed=True,
            confidence=conf,
            reason=(
                f"响应命中 {probe.model_family!r} 的预期特征标记 {expected_hits} "
                f"(expected markers), 未见其他模型族特征 → 不疑似置换。"
            ),
            detected_markers=expected_hits,
            substitution_suspected=False,
        )

    # 模糊响应: 拿不准, 疑罪从无, 不误杀 (passed=True, confidence 低)。
    return CanaryVerdict(
        probe_id=probe.probe_id,
        passed=True,
        confidence=0.2,
        reason=(
            "响应既无预期特征也无其他模型族特征 (模糊响应): 软检测无法定论, "
            "按疑罪从无不判置换 (避免误杀); 弱信号交由多次抽查累积评估。"
        ),
        detected_markers=[],
        substitution_suspected=False,
    )


# ── 累积跟踪 + 停签决策 ──────────────────────────────────────────────────────


class CanaryTracker:
    """跟踪某 lender 历次 canary 结果, 累积"置换嫌疑分"并给停签建议。

    单次抽查 (尤其模糊响应) 不足以定罪 (软检测有假阴/假阳)。Tracker 把多次
    verdict 累积成 substitution_score:
    - 命中 forbidden 的确证 fail → 每次加 FORBIDDEN_HIT_WEIGHT (强, 单次即达阈值)。
    - 模糊 fail (理论上 evaluate 不产出, 仅为健壮性兜底) → 加 AMBIGUOUS_FAIL_WEIGHT。
    - pass → 不加分 (但记录, 供调用方算抽查通过率)。

    嫌疑分 ≥ threshold → should_stop_lending 建议停止继续借并写差评。
    """

    def __init__(self) -> None:
        # lender_did -> 历次 verdict (按 record 顺序)。
        self._history: dict[str, list[CanaryVerdict]] = {}

    def record(self, lender_did: str, verdict: CanaryVerdict) -> None:
        """记录一次对 lender 的 canary 判定。"""
        self._history.setdefault(lender_did, []).append(verdict)

    def history(self, lender_did: str) -> list[CanaryVerdict]:
        """取某 lender 的历次 verdict (拷贝, 防外部篡改内部状态)。"""
        return list(self._history.get(lender_did, []))

    def substitution_score(self, lender_did: str) -> float:
        """累积置换嫌疑分 (≥0)。

        逻辑: 遍历该 lender 的历次 verdict, 对每个疑似置换 (passed=False) 的
        verdict 累加权重 — 命中 forbidden marker 的算强证据 (FORBIDDEN_HIT_WEIGHT),
        其余 fail 算弱证据 (AMBIGUOUS_FAIL_WEIGHT)。pass 不加分。

        嫌疑分越高越像惯犯。单次确证置换 (forbidden) 即可达默认阈值 1.0;
        多次模糊 fail 也能累积到阈值, 但需要好几次。
        """
        score = 0.0
        for v in self._history.get(lender_did, []):
            if v.passed:
                continue
            has_forbidden = any(
                m for m in v.detected_markers  # detected 里含 forbidden 命中
            ) and "forbidden markers" in v.reason
            score += FORBIDDEN_HIT_WEIGHT if has_forbidden else AMBIGUOUS_FAIL_WEIGHT
        return score

    def should_stop_lending(
        self, lender_did: str, threshold: float = DEFAULT_STOP_THRESHOLD
    ) -> bool:
        """嫌疑分是否达到停签阈值 (建议停止继续借用该 lender 并写差评)。"""
        return self.substitution_score(lender_did) >= threshold

    def pass_rate(self, lender_did: str) -> Optional[float]:
        """抽查通过率 ∈ [0,1] (无记录返 None), 供调用方/reputation 参考。"""
        hist = self._history.get(lender_did, [])
        if not hist:
            return None
        return sum(1 for v in hist if v.passed) / len(hist)


# ── 跟 reputation 联动: 生成差评 Review ──────────────────────────────────────


def to_review(
    lender_did: str,
    borrower_did: str,
    settlement_ref: str,
    tracker: CanaryTracker,
    *,
    timestamp: int,
    token_volume: float = 0.0,
    threshold: float = DEFAULT_STOP_THRESHOLD,
) -> Review:
    """据 canary 抽查累积结果, 为 lender 生成一条 Review (跟 reputation 联动)。

    映射 (置换嫌疑越高分越低, 全程 1-5 钳制):
    - 嫌疑分 ≥ threshold (建议停签) → 1 星最低分 (置换确凿, 严重违约):
      quality=1 (给的不是声称的货), uptime=1 (信任崩塌), speed 中性=3
      (置换跟速度无关, 不连坐)。text 写明置换证据。
    - 嫌疑分 > 0 但未达阈值 → 2-3 星 (有可疑信号但不确凿)。
    - 嫌疑分 = 0 (历次都过) → 5 星好评 (抽查全过, 模型货真价实)。

    Args:
        lender_did:     被评者 (借出方)。
        borrower_did:   评价者 (借入方, 即本客户端)。
        settlement_ref: 这笔交易的支付凭证 hash (防刷根, Review 必填)。
        tracker:        累积了对该 lender 历次 canary 结果的 tracker。
        timestamp:      评价时间 (unix 秒, 传入, 不用 now())。
        token_volume:   这笔交易 token 量 (≥0)。
        threshold:      停签/置换确凿的嫌疑分阈值。

    Returns:
        Review (reviewee=lender, reviewer=borrower)。
    """
    score_val = tracker.substitution_score(lender_did)

    if score_val >= threshold:
        star = SCORE_MIN  # 1
        dims = {"quality": 1, "speed": 3, "uptime": 1}
        text = (
            f"canary 抽查检测到疑似模型置换 (嫌疑分 {score_val:.2f} ≥ {threshold}): "
            f"声称模型与实际响应特征不符, 疑似收 claude 的钱用便宜模型顶包。建议停止借用。"
        )
    elif score_val > 0.0:
        # 线性插值到 2-3 星区间, 钳制在合法范围。
        star = 3 if score_val < threshold * 0.5 else 2
        star = max(SCORE_MIN, min(SCORE_MAX, star))
        dims = {"quality": star, "speed": 3, "uptime": star}
        text = (
            f"canary 抽查有可疑信号但未确凿 (嫌疑分 {score_val:.2f} < {threshold}): "
            f"建议继续抽查观察。"
        )
    else:
        star = SCORE_MAX  # 5
        dims = {"quality": 5, "speed": 4, "uptime": 5}
        text = "canary 抽查历次全过: 借出方模型货真价实, 无置换迹象。"

    return Review(
        reviewer_did=borrower_did,
        reviewee_did=lender_did,
        score=star,
        settlement_ref=settlement_ref,
        token_volume=token_volume,
        timestamp=timestamp,
        dimensions=dims,
        text=text,
    )
