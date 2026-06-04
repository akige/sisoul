"""Demo: 3-peer Federated LoRA aggregation end-to-end.

Foundation impl: stub aggregator. This test demonstrates the full intended flow
for v3.0 ship (T+15-16m): 3 peers compute local LoRA delta, aggregator sums + DP noise.
"""
from __future__ import annotations
import pytest


def test_federated_lora_3_peer_aggregation_demo():
    from sisoul.v2.personal_lora import (
        FederatedLoRAAggregator,
        FederatedLoRAConfig,
        LoRAAdapter,
        TrainingConfig,
        PersonalLoRATrainer,
    )

    # 3 peers each train a local LoRA
    peers = ["did:key:z6MkAlice", "did:key:z6MkBob", "did:key:z6MkCharlie"]
    cfg = TrainingConfig(rank=8, epochs=1)
    peer_adapters = {}
    for did in peers:
        trainer = PersonalLoRATrainer(cfg, did_owner=did)
        result = trainer.train(f"/tmp/lora-{did[-8:]}.safetensors")
        peer_adapters[did] = result.adapter

    # base = first peer's adapter (in reality, all peers agree on base model)
    base = peer_adapters[peers[0]]

    # mock deltas from other peers
    deltas = [
        {"did": peers[1], "delta": "<stub-delta-bob>"},
        {"did": peers[2], "delta": "<stub-delta-carol>"},
    ]

    # aggregator runs FedAvg (foundation: stub)
    agg = FederatedLoRAAggregator(
        FederatedLoRAConfig(min_participants=2, aggregation_rounds=3),
        aggregator_did=peers[0],
    )
    federated_adapter, fed_round = agg.federate(base, deltas, round_id=1)

    # verify
    assert federated_adapter.name.endswith("-fed-r1")
    assert fed_round.delta_count == 2
    assert fed_round.aggregator_did == peers[0]
    assert peers[1] in fed_round.participants
    assert peers[2] in fed_round.participants
    assert federated_adapter.eval_metrics["federated"] is True


def test_federated_lora_round_chain():
    """3 rounds of federated aggregation — each round builds on the last."""
    from sisoul.v2.personal_lora import (
        FederatedLoRAAggregator, FederatedLoRAConfig, LoRAAdapter,
    )

    agg = FederatedLoRAAggregator(
        FederatedLoRAConfig(min_participants=2, aggregation_rounds=3),
        aggregator_did="did:key:z6MkA",
    )

    base = LoRAAdapter(
        name="personal-v0",
        version="0.1",
        base_model="Llama-3.1-8B",
        rank=16,
        file_path="/tmp/lora.safetensors",
        size_bytes=20_000_000,
        did_owner="did:key:z6MkA",
        trained_at="2026-06-04",
    )

    rounds_completed = []
    current_adapter = base
    for r in range(1, 4):
        deltas = [
            {"did": f"did:key:z6MkPeer{i}", "delta": "<stub>"}
            for i in range(3)
        ]
        current_adapter, fed_round = agg.federate(current_adapter, deltas, round_id=r)
        rounds_completed.append(fed_round)

    assert len(rounds_completed) == 3
    # Each round's adapter should chain: personal-v0 → ...-fed-r1 → ...-fed-r2 → ...-fed-r3
    assert "fed-r3" in current_adapter.name
    assert current_adapter.eval_metrics["participants"] == 3


def test_federated_lora_aggregation_with_reputation_filter():
    """Federated agg + Reputation: only high-rep peers' deltas accepted."""
    from sisoul.v2.personal_lora import (
        FederatedLoRAAggregator, FederatedLoRAConfig, LoRAAdapter,
    )
    from sisoul.v2.reputation import ReputationRouter

    rep = ReputationRouter()
    rep.update("did:key:z6MkHighRep", "ml", +0.4)  # 0.9
    rep.update("did:key:z6MkLowRep", "ml", -0.4)  # 0.1 (will be filtered)
    rep.update("did:key:z6MkMidRep", "ml", +0.1)  # 0.6

    all_deltas = [
        {"did": "did:key:z6MkHighRep", "delta": "<stub>"},
        {"did": "did:key:z6MkLowRep", "delta": "<stub>"},
        {"did": "did:key:z6MkMidRep", "delta": "<stub>"},
    ]
    # filter by rep ≥ 0.5
    accepted = [d for d in all_deltas if rep.get_score(d["did"], "ml") >= 0.5]
    assert len(accepted) == 2  # High + Mid
    assert "did:key:z6MkLowRep" not in [d["did"] for d in accepted]

    agg = FederatedLoRAAggregator(
        FederatedLoRAConfig(min_participants=2),
        aggregator_did="did:key:z6MkAgg",
    )
    base = LoRAAdapter(
        name="base", version="0.1", base_model="Llama-3.1-8B", rank=16,
        file_path="/tmp/base.safetensors", size_bytes=20_000_000,
        did_owner="did:key:z6MkAgg", trained_at="2026-06-04",
    )
    federated, fed_round = agg.federate(base, accepted, round_id=1)
    assert fed_round.delta_count == 2


def test_federated_combined_with_debate():
    """Federated LoRA + Multi-Agent Debate: agents w/ federated LoRA debate."""
    from sisoul.v2.debate import DebateAgent, MultiAgentDebate

    # 3 agents with federated LoRA enabled (high rep on rust)
    agents = [
        DebateAgent(did="did:key:z6MkA", petname="Alice", topic_reputation=0.85),
        DebateAgent(did="did:key:z6MkB", petname="Bob", topic_reputation=0.78),
        DebateAgent(did="did:key:z6MkC", petname="Carol", topic_reputation=0.91),
    ]
    d = MultiAgentDebate(agents, n_rounds=3)
    result = d.debate("How to handle async deadlock in Rust?")

    # Carol has highest rep → synthesizer
    assert result.final_confidence == 0.91
    # 3 rounds × 3 agents = 9 rounds total
    assert len(result.rounds) == 9


def test_v3_full_workflow_demo():
    """v3.0 end-to-end: ask via pipeline → debate → federated LoRA agg → provenance."""
    import tempfile
    from pathlib import Path

    from sisoul.v2.pipeline import V2AskPipeline, AskRequest
    from sisoul.v2.debate import DebateAgent, MultiAgentDebate
    from sisoul.v2.personal_lora import (
        FederatedLoRAAggregator, FederatedLoRAConfig, LoRAAdapter,
    )
    from sisoul.v2.reputation import ReputationRouter

    with tempfile.TemporaryDirectory() as td:
        # 1. Seed cases via pipeline
        rep = ReputationRouter()
        pipeline = V2AskPipeline(vault_dir=Path(td) / "vault", reputation_router=rep)

        # Alice answers a Rust question first
        pipeline.ask(AskRequest(
            query="How to use tokio::select in Rust",
            did_asker="did:key:z6MkAlice",
            topic="rust",
        ))

        # Bob asks similar question → should cite Alice
        bob_resp = pipeline.ask(AskRequest(
            query="Rust async tokio deadlock how to fix",
            did_asker="did:key:z6MkBob",
            topic="rust",
        ))
        assert len(bob_resp.cited_cases) >= 1  # Alice's case cited
        assert bob_resp.attestation_uid.startswith("mock:")  # EAS attested

        # Alice's rep on rust should increase
        assert rep.get_score("did:key:z6MkAlice", "rust") > 0.5

        # 2. Now run multi-agent debate
        debate_agents = [
            DebateAgent(
                did="did:key:z6MkAlice",
                petname="Alice",
                topic_reputation=rep.get_score("did:key:z6MkAlice", "rust"),
            ),
            DebateAgent(did="did:key:z6MkBob", petname="Bob", topic_reputation=0.5),
            DebateAgent(did="did:key:z6MkCharlie", petname="Charlie", topic_reputation=0.6),
        ]
        debate = MultiAgentDebate(debate_agents)
        debate_result = debate.debate("Rust async tokio deadlock?")
        assert debate_result.final_confidence >= 0.5

        # 3. Federated LoRA aggregation (peer 0 acts as aggregator)
        agg = FederatedLoRAAggregator(
            FederatedLoRAConfig(min_participants=2),
            aggregator_did="did:key:z6MkAlice",
        )
        base = LoRAAdapter(
            name="rust-circle-base", version="0.1", base_model="Llama-3.1-8B",
            rank=16, file_path="/tmp/base.safetensors", size_bytes=20_000_000,
            did_owner="did:key:z6MkAlice", trained_at="2026-06-04",
        )
        deltas = [
            {"did": "did:key:z6MkBob", "delta": "<stub-bob-lora-delta>"},
            {"did": "did:key:z6MkCharlie", "delta": "<stub-charlie-lora-delta>"},
        ]
        fed_adapter, fed_round = agg.federate(base, deltas, round_id=1)
        assert fed_round.delta_count == 2
        assert "rust-circle-base-fed-r1" == fed_adapter.name

    # v3.0 full workflow demo PASS: case retrieval + EAS attest + reputation update +
    # multi-agent debate + federated LoRA aggregation 全在 foundation skeleton 上跑通
