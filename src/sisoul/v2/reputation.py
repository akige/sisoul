"""Reputation-Weighted Routing (§59 §4.3, v3.0 ship T+13m).

按 topic rep 路由 sisoul ask --debate 找 top-K 朋友 agent.
20% exploration 跨朋友圈防 echo chamber.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TopicReputation:
    """Per-friend per-topic reputation score."""

    did: str
    topic: str
    score: float = 0.5  # 0..1
    n_attestations: int = 0  # # of EAS attests this friend received for this topic
    n_citations: int = 0  # # of times their case was cited
    last_updated_at: str = ""

    def clamp(self) -> float:
        return max(0.0, min(1.0, self.score))


@dataclass
class RoutingRequest:
    """Request to route a query to top-K agents."""

    query: str
    topic: str  # extracted from query (e.g. "rust", "ml", "deploy")
    top_k: int = 3
    exploration_ratio: float = 0.2  # 20% chance to include random non-top
    min_reputation: float = 0.3


class ReputationRouter:
    """Reputation-weighted agent routing.

    Foundation impl: pure in-memory + naive max selection.
    Full impl (v3.0): GossipSub topic discovery + EAS attest weighting.
    """

    def __init__(self):
        # did → topic → TopicReputation
        self._scores: dict[str, dict[str, TopicReputation]] = {}

    def update(self, did: str, topic: str, score_delta: float) -> None:
        """Adjust reputation (e.g. after EAS attest)."""
        if not did.startswith("did:key:"):
            raise ValueError(f"invalid did: {did}")
        scores = self._scores.setdefault(did, {})
        rep = scores.setdefault(topic, TopicReputation(did=did, topic=topic))
        rep.score = max(0.0, min(1.0, rep.score + score_delta))
        rep.n_attestations += 1

    def select_top_k(self, req: RoutingRequest, candidates: list[str]) -> list[str]:
        """Pick top_k candidates by topic reputation, with exploration.

        Foundation: returns deterministic top-k by score. Full impl adds DP noise + 20% exploration.
        """
        scored = []
        for did in candidates:
            rep = self._scores.get(did, {}).get(req.topic)
            score = rep.clamp() if rep else 0.5  # default 0.5 (no history)
            scored.append((did, score))
        # filter by min reputation
        scored = [s for s in scored if s[1] >= req.min_reputation]
        # sort desc
        scored.sort(key=lambda x: -x[1])
        return [did for did, _ in scored[: req.top_k]]

    def get_score(self, did: str, topic: str) -> float:
        rep = self._scores.get(did, {}).get(topic)
        return rep.clamp() if rep else 0.5

    def list_topics(self, did: str) -> list[str]:
        return list(self._scores.get(did, {}).keys())


__all__ = ["TopicReputation", "RoutingRequest", "ReputationRouter"]
