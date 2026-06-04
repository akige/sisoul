"""Provenance EAS client — EAS attest on Optimism L2 (v2.0 ship T+10m).

Foundation impl: deterministic mock + interface. Full impl uses
existing src/sisoul/onchain/ EAS adapter (Wave J).
"""
from __future__ import annotations
import hashlib
import json
from dataclasses import asdict
from typing import Optional

from .schema import Citation, ProvenanceChain


class EASClient:
    """Skeleton EAS client.

    Full impl: signs + submits attestation to Optimism Sepolia (alpha)
    or Optimism Mainnet (v1.0 stable).
    """

    EAS_SCHEMA = "sisoul.provenance.v1"
    EAS_SCHEMA_FIELDS = "string response_id,bytes32 query_hash,address[] cited_authors,uint256 timestamp"

    def __init__(self, network: str = "optimism-sepolia"):
        if network not in ("optimism-sepolia", "optimism-mainnet", "mock"):
            raise ValueError(f"unsupported network: {network}")
        self.network = network

    def attest(self, chain: ProvenanceChain) -> str:
        """Submit attestation, returns UID (tx hash or mock).

        Foundation: deterministic mock. Full impl: real EAS contract call.
        """
        h = hashlib.sha256()
        h.update(self.network.encode())
        h.update(chain.response_id.encode())
        h.update(chain.did_answerer.encode())
        for c in chain.citations:
            h.update(c.source_id.encode())
            h.update(c.did_author.encode())
        if self.network == "mock":
            return f"mock:{h.hexdigest()[:16]}"
        return f"0x{h.hexdigest()}"

    def verify(self, attestation_uid: str) -> bool:
        """Foundation: format check. Full impl: query EAS contract."""
        if attestation_uid.startswith("mock:"):
            return len(attestation_uid) > 16
        return attestation_uid.startswith("0x") and len(attestation_uid) == 66

    def estimate_gas(self, chain: ProvenanceChain) -> dict:
        """Estimate gas for the attestation. Stub."""
        return {
            "estimated_gas": 80_000 + len(chain.citations) * 5_000,
            "gas_price_gwei": 0.001 if "sepolia" in self.network else 0.01,
            "estimated_cost_eth": 0.000001,
            "estimated_cost_usd": 0.001,
        }


__all__ = ["EASClient"]
