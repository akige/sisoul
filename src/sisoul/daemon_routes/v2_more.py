"""Additional v2/v3 daemon HTTP routes (provenance + debate + reputation)."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from sisoul.v2.provenance import (
    Citation, ProvenanceChain, ProvenanceAttester, build_chain, EASClient,
)
from sisoul.v2.debate import DebateAgent, MultiAgentDebate
from sisoul.v2.reputation import ReputationRouter, RoutingRequest


router = APIRouter(prefix="/v2", tags=["v2-extras"])


# In-memory state (foundation only; full impl persists)
_reputation_router = ReputationRouter()


# ── /v2/provenance/attest ──────────────────────────────────────────────────


class AttestRequest(BaseModel):
    response_id: str
    query: str
    answer: str
    did_answerer: str
    cited_cases: list[dict]  # [{"source_id": "case-x", "did_author": "did:key:z6Mk..."}]
    network: str = "mock"


@router.post("/provenance/attest")
def attest(req: AttestRequest) -> dict:
    if not req.did_answerer.startswith("did:key:"):
        raise HTTPException(status_code=400, detail="invalid did_answerer")
    chain = build_chain(
        req.response_id, req.query, req.answer, req.did_answerer,
        cited_cases=[(c["source_id"], c["did_author"]) for c in req.cited_cases],
    )
    eas = EASClient(network=req.network)
    uid = eas.attest(chain)
    return {
        "attestation_uid": uid,
        "network": req.network,
        "total_micropay_sis": chain.total_micropayment(),
        "citation_count": len(chain.citations),
    }


# ── /v2/debate/run ─────────────────────────────────────────────────────────


class DebateAgentSpec(BaseModel):
    did: str
    petname: Optional[str] = None
    topic_reputation: float = 0.5


class DebateRequest(BaseModel):
    query: str
    agents: list[DebateAgentSpec]
    n_rounds: int = 3


@router.post("/debate/run")
def run_debate(req: DebateRequest) -> dict:
    if len(req.agents) < 2:
        raise HTTPException(status_code=400, detail="need ≥2 agents")
    agents = [
        DebateAgent(did=a.did, petname=a.petname, topic_reputation=a.topic_reputation)
        for a in req.agents
    ]
    d = MultiAgentDebate(agents, n_rounds=req.n_rounds)
    result = d.debate(req.query)
    return {
        "query": result.query,
        "final_answer": result.final_answer,
        "final_confidence": result.final_confidence,
        "n_rounds": len(result.rounds),
        "agents": [{"did": a.did, "petname": a.petname, "is_synthesizer": a.is_synthesizer} for a in result.agents],
        "sources": result.sources,
    }


# ── /v2/reputation/update + /v2/reputation/top-k ───────────────────────────


class ReputationUpdateRequest(BaseModel):
    did: str
    topic: str
    score_delta: float


@router.post("/reputation/update")
def reputation_update(req: ReputationUpdateRequest) -> dict:
    try:
        _reputation_router.update(req.did, req.topic, req.score_delta)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "did": req.did,
        "topic": req.topic,
        "new_score": _reputation_router.get_score(req.did, req.topic),
    }


class TopKRequest(BaseModel):
    query: str
    topic: str
    candidates: list[str]
    top_k: int = 3
    min_reputation: float = 0.3


@router.post("/reputation/top-k")
def reputation_top_k(req: TopKRequest) -> dict:
    routing = RoutingRequest(
        query=req.query, topic=req.topic, top_k=req.top_k, min_reputation=req.min_reputation
    )
    picked = _reputation_router.select_top_k(routing, req.candidates)
    return {
        "topic": req.topic,
        "picked": picked,
        "scores": [{"did": d, "score": _reputation_router.get_score(d, req.topic)} for d in picked],
    }
