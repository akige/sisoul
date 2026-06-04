"""Integrated v2.0 ask pipeline.

End-to-end flow:
  ask query
  → retrieve cases from CaseStore
  → call LLM (foundation: mock; full impl: call provider adapter)
  → build ProvenanceChain with citations
  → EAS attest
  → write new case + update TfIdfIndex
  → update reputation (if attestations)

Foundation: stubs full LLM call. Full impl (v2.0 ship T+8-12m) swaps in real provider.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .case_graph import Case, CaseRetrieval, CaseStore, derive_case_id
from .provenance import Citation, ProvenanceChain, ProvenanceAttester, EASClient, build_chain
from .reputation import ReputationRouter


@dataclass
class AskRequest:
    query: str
    did_asker: str
    topic: str = ""  # extracted or specified
    top_k_cases: int = 3
    attest_network: str = "mock"


@dataclass
class AskResponse:
    query: str
    answer: str
    cited_cases: list[Case]
    provenance: ProvenanceChain
    attestation_uid: str
    new_case_id: str  # case auto-written from this response
    confidence: float = 0.0


class V2AskPipeline:
    """End-to-end ask pipeline integrating Case + Provenance + EAS + Reputation."""

    def __init__(
        self,
        vault_dir: Path,
        reputation_router: Optional[ReputationRouter] = None,
        network: str = "mock",
    ):
        self.case_store = CaseStore(vault_dir)
        self.reputation = reputation_router or ReputationRouter()
        self.eas = EASClient(network=network)
        self.network = network

    def _llm_call(self, query: str, cited_cases: list[Case]) -> str:
        """Foundation: deterministic mock. Full impl: provider adapter chat."""
        if cited_cases:
            return (
                f"[v2 mock answer for '{query}' using {len(cited_cases)} cases. "
                f"Top: {cited_cases[0].id}]"
            )
        return f"[v2 mock answer for '{query}' (no cases hit)]"

    def ask(self, req: AskRequest) -> AskResponse:
        # 1. retrieve cases
        retrieval: CaseRetrieval = self.case_store.search(req.query, top_k=req.top_k_cases)
        cited = retrieval.cases

        # 2. LLM call (mock or real)
        answer = self._llm_call(req.query, cited)

        # 3. build provenance chain
        chain = build_chain(
            response_id=f"resp-{derive_case_id(req.query, req.did_asker)}",
            query=req.query,
            answer=answer,
            did_answerer=req.did_asker,
            cited_cases=[(c.id, c.did_author) for c in cited],
        )

        # 4. EAS attest
        attestation_uid = self.eas.attest(chain)
        chain.eas_attestation_uid = attestation_uid

        # 5. write new case
        new_case = Case(
            id=derive_case_id(req.query, req.did_asker),
            question=req.query,
            answer=answer,
            did_author=req.did_asker,
            sources=[{"source_id": c.id, "did_author": c.did_author} for c in cited],
            tags=[req.topic] if req.topic else [],
            eas_attestation_uid=attestation_uid,
        )
        self.case_store.add(new_case)

        # 6. update reputation for cited authors
        if req.topic:
            for c in cited:
                try:
                    self.reputation.update(c.did_author, req.topic, +0.05)
                except ValueError:
                    pass  # invalid did_author silently ignored at foundation level

        return AskResponse(
            query=req.query,
            answer=answer,
            cited_cases=cited,
            provenance=chain,
            attestation_uid=attestation_uid,
            new_case_id=new_case.id,
            confidence=min(1.0, 0.5 + 0.1 * len(cited)),  # 0.5 base + 0.1 per cite, cap 1.0
        )


__all__ = ["AskRequest", "AskResponse", "V2AskPipeline"]
