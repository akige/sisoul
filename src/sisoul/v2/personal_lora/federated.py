"""Federated LoRA — 朋友圈 FedAvg 聚合 (§62 §2.2 作用 2, v3.0 ship T+15-16m).

实证 (Google Gboard 2017): 100 用户联邦学习, accuracy +5-10pp.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from .schema import LoRAAdapter


@dataclass
class FederatedLoRAConfig:
    """FedAvg aggregation config."""

    aggregation_rounds: int = 10
    min_participants: int = 3
    secure_aggregation: bool = True  # HE / SecAgg
    differential_privacy_epsilon: float = 1.0  # DP noise budget
    weighted_by_data_size: bool = True


@dataclass
class FederatedRound:
    """One round of FedAvg aggregation."""

    round_id: int
    participants: list[str]  # list of did:key
    base_adapter_name: str
    aggregator_did: str  # rotating aggregator (sisoul has 0 servers)
    delta_count: int = 0
    aggregated_at: Optional[str] = None


class FederatedLoRAAggregator:
    """Aggregator stub (full impl v3.0).

    Full impl: receives encrypted LoRA delta from peers via GossipSub,
    runs FedAvg + DP noise, broadcasts aggregated adapter back.
    """

    def __init__(self, config: FederatedLoRAConfig, aggregator_did: str):
        if not aggregator_did.startswith("did:key:"):
            raise ValueError(f"invalid aggregator_did: {aggregator_did}")
        self.config = config
        self.aggregator_did = aggregator_did

    def can_aggregate(self, deltas: list[dict]) -> bool:
        return len(deltas) >= self.config.min_participants

    def federate(
        self, base: LoRAAdapter, peer_deltas: list[dict], round_id: int = 1
    ) -> tuple[LoRAAdapter, FederatedRound]:
        """Skeleton: validates inputs + returns base + new round metadata.

        Full impl: HE/SecAgg sum + DP noise + weighted by data size.
        """
        if not self.can_aggregate(peer_deltas):
            raise ValueError(
                f"need ≥{self.config.min_participants} participants, got {len(peer_deltas)}"
            )
        new_adapter = LoRAAdapter(
            name=f"{base.name}-fed-r{round_id}",
            version=f"{base.version}.fed.{round_id}",
            base_model=base.base_model,
            rank=base.rank,
            file_path=base.file_path,  # full impl: new path after aggregation
            size_bytes=base.size_bytes,
            did_owner=base.did_owner,
            trained_at=f"federated-round-{round_id}",
            eval_metrics={
                "stub": True,
                "federated": True,
                "participants": len(peer_deltas),
            },
        )
        fed_round = FederatedRound(
            round_id=round_id,
            participants=[d.get("did") for d in peer_deltas if d.get("did")],
            base_adapter_name=base.name,
            aggregator_did=self.aggregator_did,
            delta_count=len(peer_deltas),
            aggregated_at="2026-06-04T00:00:00Z",
        )
        return new_adapter, fed_round


__all__ = ["FederatedLoRAConfig", "FederatedRound", "FederatedLoRAAggregator"]
