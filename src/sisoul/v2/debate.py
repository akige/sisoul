"""Multi-Agent Debate Protocol (§62 §1, v3.0 ship T+15m).

文献证据 (引 §62 §1.1):
- Du 2023 MMLU +7.4pp / Biographies +18pp / GSM8K +8pp / MATH +13pp
- Anthropic CAI 2023: hallucination -35%
- Stanford ChatEval 2024
- Google Tree of Thoughts +30%
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DebateAgent:
    """A debate participant (your friend's agent)."""

    did: str
    petname: Optional[str] = None
    topic_reputation: float = 0.5  # 0..1, derived from EAS attest history
    is_synthesizer: bool = False  # set True for the agent doing final synthesize


@dataclass
class DebateRound:
    """One round of debate (initial / critique / synthesize)."""

    round_num: int  # 1=initial, 2=critique, 3=synthesize
    agent_did: str
    output: str  # answer or critique text
    confidence: float = 0.5
    cites: list[str] = field(default_factory=list)


@dataclass
class DebateResult:
    """Result of full debate session."""

    query: str
    agents: list[DebateAgent]
    rounds: list[DebateRound]
    final_answer: str
    final_confidence: float
    sources: list[str]  # citation case IDs
    total_sis_paid: float = 0.0
    duration_seconds: float = 0.0


class MultiAgentDebate:
    """Skeleton orchestrator.

    Full impl (v3.0): recruits agents via Reputation Routing,
    fanout via GossipSub, runs 3 rounds, synthesizes.
    """

    def __init__(self, agents: list[DebateAgent], n_rounds: int = 3):
        if len(agents) < 2:
            raise ValueError(f"need ≥2 agents for debate, got {len(agents)}")
        self.agents = agents
        self.n_rounds = n_rounds

    def select_synthesizer(self) -> DebateAgent:
        """Pick highest-rep agent as synthesizer."""
        return max(self.agents, key=lambda a: a.topic_reputation)

    def debate(self, query: str) -> DebateResult:
        """Skeleton debate: returns mock result.

        Full impl: 3-round protocol via GossipSub + LLM provider adapter.
        """
        synthesizer = self.select_synthesizer()
        synthesizer.is_synthesizer = True

        rounds = []
        for r in range(1, self.n_rounds + 1):
            for a in self.agents:
                rounds.append(DebateRound(
                    round_num=r,
                    agent_did=a.did,
                    output=f"[stub round-{r} from {a.petname or a.did[:12]}]",
                    confidence=a.topic_reputation,
                ))

        return DebateResult(
            query=query,
            agents=self.agents,
            rounds=rounds,
            final_answer=f"[stub synthesized answer for: {query}]",
            final_confidence=synthesizer.topic_reputation,
            sources=[],
            total_sis_paid=0.0,
            duration_seconds=0.0,
        )


__all__ = ["DebateAgent", "DebateRound", "DebateResult", "MultiAgentDebate"]
