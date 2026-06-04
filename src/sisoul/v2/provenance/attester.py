"""Provenance attester — EAS attest + SIS micropay accumulator.

Foundation skeleton — full EAS impl 在 v2.0 (T+10m), SIS micropay 在 v3.0 (T+15m).
"""
from __future__ import annotations
import hashlib
from typing import Optional

from .schema import Citation, ProvenanceChain


class ProvenanceAttester:
    """Skeleton attester.

    Full impl: EAS schema register + attest on Optimism L2 + Lightning micropay.
    """

    EAS_SCHEMA_NAME = "sisoul.provenance.v1"
    EAS_SCHEMA_FIELDS = "string response_id,address[] cited_authors,uint256 timestamp"

    def __init__(self, did_attester: str):
        if not did_attester.startswith("did:key:"):
            raise ValueError(f"invalid did_attester: {did_attester}")
        self.did_attester = did_attester

    def attest(self, chain: ProvenanceChain) -> str:
        """Generate attestation UID.

        Foundation impl: sha256 deterministic mock UID. Full: real EAS tx hash.
        """
        h = hashlib.sha256()
        h.update(chain.response_id.encode())
        h.update(chain.did_answerer.encode())
        for c in chain.citations:
            h.update(c.source_id.encode())
            h.update(c.did_author.encode())
        return f"0x{h.hexdigest()}"

    def estimate_micropay(self, chain: ProvenanceChain, rate_per_citation: float = 0.01) -> float:
        """Estimate SIS micropayment total."""
        return sum(rate_per_citation for _ in chain.citations)


def build_chain(
    response_id: str,
    query: str,
    answer: str,
    did_answerer: str,
    cited_cases: list[tuple[str, str]],  # [(source_id, did_author), ...]
    confidence: float = 1.0,
    micropayment_per: float = 0.01,
) -> ProvenanceChain:
    """Build a ProvenanceChain from query + answer + cited cases."""
    citations = [
        Citation(
            source_type="case",
            source_id=sid,
            did_author=did,
            confidence=confidence,
            micropayment_sis=micropayment_per,
        )
        for sid, did in cited_cases
    ]
    return ProvenanceChain(
        response_id=response_id,
        query=query,
        answer=answer,
        did_answerer=did_answerer,
        citations=citations,
        sis_total_paid=sum(c.micropayment_sis for c in citations),
        created_at="2026-06-04T00:00:00Z",
    )


__all__ = ["ProvenanceAttester", "build_chain"]
