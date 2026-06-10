"""tests for sisoul.friend.reputation (去中心化信誉系统 M1, §80)。

覆盖:
- 基本: 多节点真实互评 → 信誉分合理排序
- 防刷正向证据: 老节点(高信誉) + 100 个新号互刷 → 新号信誉 ≈0 (核心卖点)
- 交易量加权: 大单评价 > 小单评价影响
- 时间衰减: 老评价权重 < 新评价
- 自评被过滤
- 空输入不崩
- collusion 检测能抓出互刷对
- Review 序列化 round-trip / 字段校验
"""

from __future__ import annotations

import pytest

from sisoul.friend.reputation import (
    DEFAULT_HALF_LIFE_DAYS,
    Review,
    ReputationError,
    compute_reputation,
    detect_collusion,
)

DAY = 86400
NOW = 1_700_000_000  # 固定基准时间, 测试可复现


def _rev(reviewer, reviewee, score, *, vol=1000.0, ts=NOW, ref=None, dims=None):
    return Review(
        reviewer_did=reviewer,
        reviewee_did=reviewee,
        score=score,
        settlement_ref=ref or f"0xsettle-{reviewer}-{reviewee}",
        token_volume=vol,
        timestamp=ts,
        dimensions=dims or {},
    )


# ── 数据结构 ─────────────────────────────────────────────────────────────────


def test_review_roundtrip() -> None:
    r = _rev("alice", "bob", 5, vol=2000.0, dims={"quality": 5, "speed": 4, "uptime": 5})
    d = r.to_dict()
    r2 = Review.from_dict(d)
    assert r2 == r
    # 容忍多余字段 (将来 EAS 可能附带 uid 等)。
    d["attestation_uid"] = "0xdeadbeef"
    assert Review.from_dict(d) == r


def test_review_validates_score() -> None:
    with pytest.raises(ReputationError):
        _rev("a", "b", 6)
    with pytest.raises(ReputationError):
        _rev("a", "b", 0)
    with pytest.raises(ReputationError):
        _rev("a", "b", 5, dims={"quality": 7})
    with pytest.raises(ReputationError):
        _rev("a", "b", 5, vol=-1.0)


def test_normalized_score() -> None:
    assert _rev("a", "b", 1).normalized_score() == 0.0
    assert _rev("a", "b", 5).normalized_score() == 1.0
    assert _rev("a", "b", 3).normalized_score() == 0.5


# ── 基本传播与排序 ───────────────────────────────────────────────────────────


def test_basic_ranking() -> None:
    """carol 被两个高信誉者好评 → 信誉最高; dave 被差评 → 最低。"""
    reviews = [
        # alice 与 bob 互相好评 (建立彼此信誉)。
        _rev("alice", "bob", 5),
        _rev("bob", "alice", 5),
        # alice 与 bob 都极力好评 carol。
        _rev("alice", "carol", 5, vol=5000.0),
        _rev("bob", "carol", 5, vol=5000.0),
        # alice 差评 dave。
        _rev("alice", "dave", 2, vol=500.0),
    ]
    rep = compute_reputation(reviews, seed_dids={"alice", "bob"})
    assert set(rep) == {"alice", "bob", "carol", "dave"}
    # 所有分在 [0,1] 且总和 1。
    assert all(0.0 <= v <= 1.0 for v in rep.values())
    assert abs(sum(rep.values()) - 1.0) < 1e-9
    # carol 被高信誉者重金好评 → 高于被差评的 dave。
    assert rep["carol"] > rep["dave"]


# ── 防刷核心: 100 新号互刷拿不到信誉 ─────────────────────────────────────────


def test_sybil_cluster_cannot_farm_reputation() -> None:
    """核心卖点: 1 老节点(种子高信誉) + 100 新号疯狂互刷 → 新号信誉 ≈0。

    女巫集群内部彼此 5 星刷分, 但:
    - 没有种子 teleport 质量流入。
    - 集群内评价者自身信誉 ≈0 → 评价权重 ≈0。
    所以信誉无法凭空产生。
    """
    old = "founder.eth"
    legit = "honest.eth"
    sybils = [f"sybil{i}.eth" for i in range(100)]

    reviews: list[Review] = []
    # 老节点与一个诚实节点正常互评 (老节点是信任锚)。
    reviews.append(_rev(old, legit, 5, vol=3000.0))
    reviews.append(_rev(legit, old, 5, vol=3000.0))

    # 100 个新号互相疯狂 5 星刷分 (每个评价后面 5 个邻居, 形成致密刷分图)。
    for i, s in enumerate(sybils):
        for k in range(1, 6):
            target = sybils[(i + k) % len(sybils)]
            reviews.append(_rev(s, target, 5, vol=9999.0))
        # 新号还试图给老节点刷好评 (蹭信任) —— 这只会抬高老节点, 不抬高自己。
        reviews.append(_rev(s, old, 5, vol=9999.0))

    rep = compute_reputation(reviews, seed_dids={old})

    sybil_scores = [rep[s] for s in sybils]
    max_sybil = max(sybil_scores)
    total_sybil = sum(sybil_scores)

    # 断言 1: 单个女巫信誉远低于诚实节点 (至少 100 倍差距)。
    assert max_sybil < rep[legit] / 100, (
        f"max_sybil={max_sybil:.3e} 应 << legit={rep[legit]:.3e}"
    )
    # 断言 2: 整个 100 人女巫集群信誉之和仍 ≈0 (< 1% 总信誉)。
    assert total_sybil < 0.01, (
        f"100 女巫总信誉 {total_sybil:.3e} 应 < 0.01 (刷不出分)"
    )
    # 断言 3: 老节点 (种子) 牢牢占据绝对多数信誉。
    assert rep[old] > 0.5, f"种子 old={rep[old]:.3f} 应占绝对多数"


# ── 交易量加权 ───────────────────────────────────────────────────────────────


def test_token_volume_weighting() -> None:
    """同样 5 星, 大单评价对被评者信誉贡献 > 小单。"""
    # 对照组: big 被大单好评, small 被小单好评, 评价者相同且对称。
    seed = {"anchor"}
    big_reviews = [
        _rev("anchor", "helper", 5, vol=1.0),  # anchor 自身需有信誉来源
        _rev("helper", "anchor", 5, vol=1.0),
        _rev("anchor", "big", 5, vol=100000.0),
        _rev("anchor", "small", 5, vol=10.0),
    ]
    rep = compute_reputation(big_reviews, seed_dids=seed)
    assert rep["big"] > rep["small"], (
        f"大单 big={rep['big']:.4f} 应 > 小单 small={rep['small']:.4f}"
    )


# ── 时间衰减 ─────────────────────────────────────────────────────────────────


def test_time_decay() -> None:
    """同评价者同评分, 新评价对信誉贡献 > 老评价 (超过半衰期)。"""
    old_ts = NOW - int(DEFAULT_HALF_LIFE_DAYS * DAY * 3)  # 3 个半衰期前 (~270 天)
    reviews = [
        _rev("anchor", "helper", 5, vol=100.0),
        _rev("helper", "anchor", 5, vol=100.0),
        _rev("anchor", "fresh", 5, vol=1000.0, ts=NOW),       # 全新
        _rev("anchor", "stale", 5, vol=1000.0, ts=old_ts),    # 很老
    ]
    rep = compute_reputation(reviews, seed_dids={"anchor"}, now_ts=NOW)
    assert rep["fresh"] > rep["stale"], (
        f"新评价 fresh={rep['fresh']:.4f} 应 > 老评价 stale={rep['stale']:.4f}"
    )
    # 老评价超过 3 个半衰期, 贡献应被压到很小。
    assert rep["stale"] < rep["fresh"] / 5


# ── 自评过滤 ─────────────────────────────────────────────────────────────────


def test_self_review_filtered() -> None:
    """自评 (reviewer==reviewee) 被丢弃, 不能自抬信誉。"""
    reviews = [
        _rev("narcissist", "narcissist", 5, vol=999999.0),  # 自吹
        _rev("anchor", "narcissist", 2, vol=100.0),
        _rev("anchor", "good", 5, vol=100.0),
        _rev("good", "anchor", 5, vol=100.0),
    ]
    rep = compute_reputation(reviews, seed_dids={"anchor"})
    # 自评无效 → narcissist 只剩 anchor 的差评 → 低于被好评的 good。
    assert rep["narcissist"] < rep["good"]

    # 纯自评集合 → 视为无有效评价。
    only_self = [_rev("x", "x", 5)]
    assert compute_reputation(only_self) == {}


# ── 空 / 退化输入 ────────────────────────────────────────────────────────────


def test_empty_input() -> None:
    assert compute_reputation([]) == {}


def test_single_pair() -> None:
    rep = compute_reputation([_rev("a", "b", 5)])
    assert set(rep) == {"a", "b"}
    assert abs(sum(rep.values()) - 1.0) < 1e-9


def test_seed_not_in_graph_falls_back() -> None:
    """种子不在评价图里 → 退化均匀起步, 不崩。"""
    rep = compute_reputation([_rev("a", "b", 5)], seed_dids={"ghost"})
    assert set(rep) == {"a", "b"}
    assert abs(sum(rep.values()) - 1.0) < 1e-9


# ── 共谋检测 ─────────────────────────────────────────────────────────────────


def test_detect_collusion_catches_pair() -> None:
    """A↔B 疯狂互刷, 两人对其他人很少评 → 被抓为共谋对。"""
    reviews: list[Review] = []
    # A 与 B 互刷 10 次。
    for _ in range(10):
        reviews.append(_rev("colA", "colB", 5))
        reviews.append(_rev("colB", "colA", 5))
    # A、B 各对若干其他人零星评价 (正常密度)。
    for other in ["x", "y", "z"]:
        reviews.append(_rev("colA", other, 5))
        reviews.append(_rev("colB", other, 5))
    # 一对正常用户彼此评 1 次 (不该被抓)。
    reviews.append(_rev("normP", "normQ", 5))
    reviews.append(_rev("normQ", "normP", 5))

    pairs = detect_collusion(reviews)
    assert ("colA", "colB") in pairs
    assert ("normP", "normQ") not in pairs
    # 返回的是字典序无序对。
    for p in pairs:
        assert p[0] < p[1]


def test_collusion_penalty_reduces_reputation() -> None:
    """开启共谋惩罚后, 互刷对的信誉低于不惩罚时。"""
    reviews: list[Review] = []
    # 一个正常诚实子图给种子锚。
    reviews.append(_rev("anchor", "honest", 5, vol=1000.0))
    reviews.append(_rev("honest", "anchor", 5, vol=1000.0))
    # 互刷对: cA↔cB 各 8 次 (高频), 但各自也零星评别人 (使共谋边降权后
    # 信任份额真的会从对方流走, 否则行归一化会抵消单边惩罚)。
    for _ in range(8):
        reviews.append(_rev("cA", "cB", 5, vol=5000.0))
        reviews.append(_rev("cB", "cA", 5, vol=5000.0))
    for other in ["d1", "d2"]:
        reviews.append(_rev("cA", other, 5, vol=5000.0))
        reviews.append(_rev("cB", other, 5, vol=5000.0))
    # 让 anchor 给 cA 一点点初始信任, 否则惩罚前后都 ~0 看不出差别。
    reviews.append(_rev("anchor", "cA", 5, vol=1000.0))

    with_pen = compute_reputation(
        reviews, seed_dids={"anchor"}, penalize_collusion=True
    )
    without_pen = compute_reputation(
        reviews, seed_dids={"anchor"}, penalize_collusion=False
    )
    # cB 的信誉几乎全靠 cA 的互刷转发; 惩罚后应明显下降。
    assert with_pen["cB"] < without_pen["cB"], (
        f"惩罚后 cB={with_pen['cB']:.4e} 应 < 未惩罚 {without_pen['cB']:.4e}"
    )
