"""sisoul friend · 去中心化信誉系统 M1 (§80 去中心化信誉系统).

第一块 (M1): 纯本地数据结构 + EigenTrust 加权信誉传播 + 防刷检测。
本块**不接链**: 只做本地 Review 数据模型与信誉计算, 但 Review 已按 EAS
attestation (Optimism / EAS) 序列化需求设计 (to_dict / from_dict), 后续 M2
直接映射成 attestation schema 上链。

核心思想 (EigenTrust, Kamvar et al. 2003 的信誉传播变体):
- 信誉不是"谁给的分高谁就高", 而是"高信誉者给的分才算数"。
- reputation(被评者) = Σ( reputation(评价者) × 归一评分 × 交易量权重 × 时间衰减 )
- 通过幂迭代传播到收敛 (stationary distribution)。
- 新号 (无人给信任 / 评价者自身信誉 ≈0) 互刷, 信誉传不出去 → ≈0。
  这是去中心化信誉抗女巫 (sybil) 攻击的根: 没有种子信任锚的孤立刷分集群
  无法凭空制造信誉。

防刷的双重根:
1. **交易凭证根** (settlement_ref): 一笔评价必须引用真实支付凭证 hash, 无真实
   交易无评价资格 (调用方在写入前校验, 本模块只承载字段)。
2. **信誉加权传播**: 即使伪造了评价, 评价者自身信誉 ≈0 → 评价权重 ≈0 → 刷不动。
3. **共谋检测** (detect_collusion): 高频互评对 (A↔B 互刷次数远超正常) 被识别,
   其评价权重在传播时进一步衰减。

时间相关: 本模块**不调用 datetime.now()** (保证测试可复现)。所有"当前时间"
通过 now_ts 参数传入, 缺省取评价集中最新时间戳。

M1 边界:
- 不接链 (不 import sisoul.onchain), 不写 SQLite (纯内存计算)。
- 不动 friend/ 其他模块, 本文件独立 (不依赖 __init__ 顶层 export)。

测试见 tests/test_reputation.py。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# ── 公开常量 ─────────────────────────────────────────────────────────────────

# 评分合法区间 (1-5 星), dimensions 子维度同区间。
SCORE_MIN = 1
SCORE_MAX = 5

# 时间衰减默认半衰期 (天): 老评价权重指数衰减, 90 天减半。
DEFAULT_HALF_LIFE_DAYS = 90.0
_SECONDS_PER_DAY = 86400.0

# 幂迭代默认参数。
DEFAULT_MAX_ITER = 100
DEFAULT_EPSILON = 1e-6
# restart / teleport 概率 (EigenTrust 的 pre-trusted 重启项 a)。
# 每步有 restart 概率把信任质量重新注入种子集 (None 时均匀), 保证收敛且
# 让种子信任真正成为信誉之锚 (孤立刷分集群拿不到 teleport 质量)。
DEFAULT_RESTART = 0.15

# 共谋检测: 互评次数超过节点对其他伙伴平均次数的多少倍判为可疑。
DEFAULT_COLLUSION_FACTOR = 3.0
# 共谋检测: 一对节点双向各至少这么多次互评才纳入判定 (滤掉偶发)。
DEFAULT_COLLUSION_MIN_COUNT = 3
# 被判共谋的评价对, 其评价权重乘这个惩罚系数 (传播时近乎清零)。
DEFAULT_COLLUSION_PENALTY = 0.1


# ── 异常 ─────────────────────────────────────────────────────────────────────


class ReputationError(Exception):
    """reputation 通用异常。"""


# ── 数据结构 ─────────────────────────────────────────────────────────────────


@dataclass
class Review:
    """一笔评价 (reviewer 对 reviewee 的一次交易后评价)。

    字段按未来映射 EAS attestation 设计 (to_dict 的 key 即 attestation 字段名)。

    Attributes:
        reviewer_did:   评价者 DID。
        reviewee_did:   被评者 DID。
        score:          总体评分 1-5。
        dimensions:     子维度评分 dict, 约定 key: quality / speed / uptime, 各 1-5。
                        缺省空 dict; 若提供则与 score 一起参与"归一评分"计算。
        settlement_ref: 引用的支付凭证 hash (防刷根: 无真实交易无评价资格)。
                        本模块不校验其真伪, 仅承载; 写入侧负责校验。
        token_volume:   这笔交易的 token 量 (≥0), 用 log(1+volume) 做交易量加权。
        timestamp:      评价时间 unix epoch 秒。
        text:           可选评价文本。
    """

    reviewer_did: str
    reviewee_did: str
    score: int
    settlement_ref: str
    token_volume: float = 0.0
    timestamp: int = 0
    dimensions: dict[str, int] = field(default_factory=dict)
    text: Optional[str] = None

    def __post_init__(self) -> None:
        if not (SCORE_MIN <= self.score <= SCORE_MAX):
            raise ReputationError(
                f"score 必须在 [{SCORE_MIN}, {SCORE_MAX}], got {self.score}"
            )
        for dim, val in self.dimensions.items():
            if not (SCORE_MIN <= val <= SCORE_MAX):
                raise ReputationError(
                    f"dimension {dim!r} 必须在 [{SCORE_MIN}, {SCORE_MAX}], got {val}"
                )
        if self.token_volume < 0:
            raise ReputationError(f"token_volume 不能为负, got {self.token_volume}")

    # ── EAS attestation 序列化 (M2 上链复用) ────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict (将来作为 EAS attestation data 编码的中间表示)。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Review":
        """从 dict 反序列化 (容忍多余字段, 只取已知字段)。"""
        known = {
            "reviewer_did",
            "reviewee_did",
            "score",
            "settlement_ref",
            "token_volume",
            "timestamp",
            "dimensions",
            "text",
        }
        return cls(**{k: v for k, v in data.items() if k in known})

    # ── 内部辅助 ───────────────────────────────────────────────────────────

    def _mean_rating(self) -> float:
        """总体评分与子维度的均值 (1-5 量纲)。

        子维度让评价更立体: 若提供 dimensions, 把 score 与各维度一起取均值,
        使 quality/speed/uptime 真正影响传播权重 (而非仅作元数据)。
        """
        ratings = [float(self.score)]
        ratings.extend(float(v) for v in self.dimensions.values())
        return sum(ratings) / len(ratings)

    def normalized_score(self) -> float:
        """归一评分 ∈ [0,1]: (rating-1)/4。

        1 星 → 0 (零信任), 5 星 → 1 (满信任)。
        """
        return (self._mean_rating() - SCORE_MIN) / (SCORE_MAX - SCORE_MIN)


# ── 防刷: 共谋 (互刷) 检测 ───────────────────────────────────────────────────


def detect_collusion(
    reviews: list[Review],
    *,
    factor: float = DEFAULT_COLLUSION_FACTOR,
    min_count: int = DEFAULT_COLLUSION_MIN_COUNT,
) -> set[tuple[str, str]]:
    """检测高频互评对 (A↔B 互刷次数远超与其他人的正常次数)。

    判定 (一对节点 a,b 须双向都满足才算共谋):
    - a→b 次数与 b→a 次数都 ≥ min_count (滤掉偶发互评)。
    - a→b 次数 > factor × (a 对其"其他"伙伴的平均评价次数, 下限 1), 且 b→a 同理。

    "对其他伙伴的平均" 排除对方本身, 这样"只跟你疯狂互刷、几乎不评别人"的
    女巫对会被显著拉开比值而被抓出; 而广泛活跃、对很多人都评很多次的正常重度
    用户不会被误伤。

    Returns:
        可疑节点对集合, 每个元素是按字典序排好的 (did1, did2) tuple (无序对)。
    """
    # 自评不计入 (它本就会在 compute 里被丢弃)。
    counts: dict[str, dict[str, int]] = {}
    for r in reviews:
        if r.reviewer_did == r.reviewee_did:
            continue
        counts.setdefault(r.reviewer_did, {})
        counts[r.reviewer_did][r.reviewee_did] = (
            counts[r.reviewer_did].get(r.reviewee_did, 0) + 1
        )

    def avg_excluding(src: str, exclude: str) -> float:
        """src 对除 exclude 外其他伙伴的平均评价次数 (无其他伙伴 → 0)。"""
        partners = counts.get(src, {})
        others = [c for tgt, c in partners.items() if tgt != exclude]
        return (sum(others) / len(others)) if others else 0.0

    suspicious: set[tuple[str, str]] = set()
    seen_pairs: set[tuple[str, str]] = set()
    for a, targets in counts.items():
        for b in targets:
            pair = (a, b) if a < b else (b, a)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            c_ab = counts.get(a, {}).get(b, 0)
            c_ba = counts.get(b, {}).get(a, 0)
            if c_ab < min_count or c_ba < min_count:
                continue  # 非双向高频, 不算互刷

            thr_a = factor * max(avg_excluding(a, b), 1.0)
            thr_b = factor * max(avg_excluding(b, a), 1.0)
            if c_ab > thr_a and c_ba > thr_b:
                suspicious.add(pair)

    return suspicious


# ── EigenTrust 信誉计算 ──────────────────────────────────────────────────────


def _time_decay(now_ts: float, ts: float, half_life_seconds: float) -> float:
    """时间衰减权重 ∈ (0,1]: 0.5 ** (age / half_life)。

    未来时间戳 (age<0) 钳到 age=0 (权重 1), 避免"未来评价"被放大。
    """
    age = max(0.0, now_ts - ts)
    return 0.5 ** (age / half_life_seconds)


def compute_reputation(
    reviews: list[Review],
    seed_dids: Optional[set[str]] = None,
    *,
    now_ts: Optional[float] = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    max_iter: int = DEFAULT_MAX_ITER,
    epsilon: float = DEFAULT_EPSILON,
    restart: float = DEFAULT_RESTART,
    penalize_collusion: bool = True,
    collusion_penalty: float = DEFAULT_COLLUSION_PENALTY,
) -> dict[str, float]:
    """EigenTrust 加权信誉传播, 返回归一化 {did: score ∈ [0,1]} (总和=1)。

    传播模型 (幂迭代):
        t_{k+1}(j) = restart · p(j) + (1-restart) · Σ_i C[i][j] · t_k(i)
    其中:
        - C[i][j]: i 对 j 的行归一化局部信任 (i 的所有出向评价权重占比)。
        - 单条评价权重 = 归一评分 × log(1+token_volume) × 时间衰减 (× 共谋惩罚)。
        - p(j): 种子信任分布 (seed_dids 给定时只注入种子, 否则全员均匀)。
        - restart: teleport 概率, 让种子信任成为信誉之锚。

    抗女巫的关键: 当 seed_dids 指定可信锚时, teleport 质量只进种子; 孤立刷分
    集群 (新号互刷) 既拿不到 teleport, 其内部评价者自身信誉又 ≈0 (权重 ≈0),
    信誉无法凭空产生 → 新号 ≈ 0。

    边界处理:
        - 空输入 → 返回 {}。
        - 自评 (reviewer == reviewee) → 丢弃。
        - 单节点 / 单评价 → 正常返回。
        - 出向权重为 0 的节点 (dangling) → 按 p 分布派发 (标准 EigenTrust 处理)。
        - now_ts 缺省 = 评价集中最新 timestamp (可复现, 不用 datetime.now)。

    Args:
        reviews:            Review 列表。
        seed_dids:          预信任种子 DID 集合; None = 全员均匀起步。
        now_ts:             计算基准时间 (unix 秒); None = max(timestamp)。
        half_life_days:     时间衰减半衰期 (天)。
        max_iter:           幂迭代最大轮数。
        epsilon:            收敛阈值 (相邻两轮 L1 范数差)。
        restart:            teleport 概率 ∈ [0,1)。
        penalize_collusion: 是否对检测到的互刷对降权。
        collusion_penalty:  互刷对评价权重的惩罚乘子 ∈ [0,1]。

    Returns:
        {did: reputation ∈ [0,1]}, 所有值之和 = 1 (空输入除外)。
    """
    if not reviews:
        return {}

    half_life_seconds = max(1e-9, half_life_days * _SECONDS_PER_DAY)

    # 过滤自评, 收集有效评价。
    valid = [r for r in reviews if r.reviewer_did != r.reviewee_did]
    if not valid:
        return {}

    if now_ts is None:
        now_ts = float(max(r.timestamp for r in valid))

    colluding: set[tuple[str, str]] = (
        detect_collusion(valid) if penalize_collusion else set()
    )

    # 节点集 = 所有出现过的 did (评价者 + 被评者)。
    nodes: list[str] = sorted(
        {r.reviewer_did for r in valid} | {r.reviewee_did for r in valid}
    )
    idx = {did: i for i, did in enumerate(nodes)}
    n = len(nodes)

    # 累积出向权重 W[i][j]。
    out_w: list[dict[int, float]] = [dict() for _ in range(n)]
    for r in valid:
        i = idx[r.reviewer_did]
        j = idx[r.reviewee_did]
        w = (
            r.normalized_score()
            * math.log1p(max(0.0, r.token_volume))
            * _time_decay(now_ts, float(r.timestamp), half_life_seconds)
        )
        if penalize_collusion:
            pair = (
                (r.reviewer_did, r.reviewee_did)
                if r.reviewer_did < r.reviewee_did
                else (r.reviewee_did, r.reviewer_did)
            )
            if pair in colluding:
                w *= collusion_penalty
        if w <= 0.0:
            continue  # 1 星 / 零交易量 / 极老评价 → 不传播信任
        out_w[i][j] = out_w[i].get(j, 0.0) + w

    # 种子信任分布 p。
    p = [0.0] * n
    if seed_dids:
        present = [idx[d] for d in seed_dids if d in idx]
        if present:
            for i in present:
                p[i] = 1.0 / len(present)
        else:
            # 种子都不在评价图里 → 退化为均匀。
            p = [1.0 / n] * n
    else:
        p = [1.0 / n] * n

    # 行归一化得 C; 记录 dangling (无出向权重) 节点。
    row_sum = [sum(d.values()) for d in out_w]
    dangling = [i for i in range(n) if row_sum[i] <= 0.0]

    # 幂迭代。
    t = list(p)
    for _ in range(max_iter):
        nxt = [restart * p[j] for j in range(n)]

        # dangling 节点把其全部质量按 p 派发 (避免信任泄漏)。
        dangling_mass = sum(t[i] for i in dangling)
        if dangling_mass > 0.0:
            for j in range(n):
                nxt[j] += (1.0 - restart) * dangling_mass * p[j]

        # 正常节点沿行归一化的 C 传播。
        for i in range(n):
            if row_sum[i] <= 0.0 or t[i] == 0.0:
                continue
            ti_norm = (1.0 - restart) * t[i] / row_sum[i]
            for j, w in out_w[i].items():
                nxt[j] += ti_norm * w

        # 收敛判定 (L1)。
        diff = sum(abs(nxt[j] - t[j]) for j in range(n))
        t = nxt
        if diff < epsilon:
            break

    # 归一化到总和 1 (理论上已 ≈1, 这里抹平浮点误差)。
    total = sum(t)
    if total <= 0.0:
        # 极端: 所有权重为 0 (全 1 星 / 全零量) → 退化均匀。
        return {did: 1.0 / n for did in nodes}
    return {nodes[i]: t[i] / total for i in range(n)}


# ── 本地评价存储 (vault jsonl) ───────────────────────────────────────────────
#
# M1 是纯计算; M2 需要本地落地的评价集合喂给 compute_reputation。append-only
# jsonl (每行一条 Review.to_dict)。将来每条同时上 EAS attestation, 链上是真相源,
# 本地 jsonl 只是缓存/离线可算。


def _reviews_path(vault_dir: Optional[str] = None):
    import os
    from pathlib import Path
    vault = Path(
        vault_dir or os.environ.get("SISOUL_VAULT", str(Path.home() / ".sisoul"))
    ).expanduser()
    return vault / "reviews.jsonl"


def load_reviews(vault_dir: Optional[str] = None) -> list["Review"]:
    """读 vault 本地评价集 (无文件返空, 坏行跳过)。喂给 compute_reputation。"""
    import json
    path = _reviews_path(vault_dir)
    if not path.exists():
        return []
    out: list[Review] = []
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Review.from_dict(json.loads(line)))
        except Exception:  # noqa: BLE001 — 坏行不阻塞全量计算
            continue
    return out


def save_review(review: "Review", vault_dir: Optional[str] = None) -> None:
    """追加一条评价到本地 jsonl (append-only)。"""
    import json
    path = _reviews_path(vault_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(review.to_dict(), ensure_ascii=False) + "\n")
