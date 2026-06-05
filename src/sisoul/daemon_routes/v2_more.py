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


# ── /v2/growth (Self-Improvement Logging) ──────────────────────────────────

from sisoul.v2.growth import GrowthLogger, DailyGrowthSnapshot


def _growth_logger() -> GrowthLogger:
    vault = Path(os.environ.get("SISOUL_VAULT", "~/.sisoul")).expanduser()
    return GrowthLogger(vault)


class GrowthWriteRequest(BaseModel):
    date: str
    cases_added: int = 0
    skills_installed: int = 0
    skills_used: int = 0
    chats_sent: int = 0
    borrowed_llm_calls: int = 0
    new_friends: int = 0
    reputation_topics: dict = {}


@router.post("/growth/write")
def growth_write(req: GrowthWriteRequest) -> dict:
    snap = DailyGrowthSnapshot(
        date=req.date,
        cases_added=req.cases_added,
        skills_installed=req.skills_installed,
        skills_used=req.skills_used,
        chats_sent=req.chats_sent,
        borrowed_llm_calls=req.borrowed_llm_calls,
        new_friends=req.new_friends,
        reputation_topics=req.reputation_topics,
    )
    path = _growth_logger().write(snap)
    return {"date": snap.date, "path": str(path)}


@router.get("/growth/last")
def growth_last(n: int = 7) -> dict:
    from dataclasses import asdict
    trend = _growth_logger().last_n_days(n)
    return {
        "window_days": trend.window_days,
        "snapshots": [asdict(s) for s in trend.snapshots],
        "total_cases": trend.total_cases(),
        "total_skills_used": trend.total_skills_used(),
        "avg_chats_per_day": round(trend.avg_chats_per_day(), 2),
    }


# ── /v2/lesson (Memory Compaction) ─────────────────────────────────────────

from sisoul.v2.memory_compaction import MemoryCompactor, CompactionConfig, Lesson


class CompactRequest(BaseModel):
    did_owner: str
    source_case_ids: list[str]
    topic: str = ""


@router.post("/lesson/distill")
def lesson_distill(req: CompactRequest) -> dict:
    if not req.did_owner.startswith("did:key:"):
        raise HTTPException(status_code=400, detail="invalid did_owner")
    if len(req.source_case_ids) < 2:
        raise HTTPException(status_code=400, detail="need ≥2 cases to distill")
    try:
        mc = MemoryCompactor(CompactionConfig(), did_owner=req.did_owner)
        lesson = mc.distill(req.source_case_ids, topic=req.topic)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    from dataclasses import asdict
    return asdict(lesson)


# ── /v2/lora (Personal LoRA + Federated) ──────────────────────────────────

from sisoul.v2.personal_lora import (
    LoRAAdapter, TrainingConfig, PersonalLoRATrainer,
    FederatedLoRAConfig, FederatedLoRAAggregator,
)


class LoRATrainRequest(BaseModel):
    did_owner: str
    rank: int = 16
    epochs: int = 3
    base_model: str = "meta-llama/Llama-3.1-8B"


@router.post("/lora/train")
def lora_train(req: LoRATrainRequest) -> dict:
    """v2.0 personal LoRA train (foundation: stub)."""
    if not req.did_owner.startswith("did:key:"):
        raise HTTPException(status_code=400, detail="invalid did_owner")
    cfg = TrainingConfig(rank=req.rank, epochs=req.epochs, base_model=req.base_model)
    trainer = PersonalLoRATrainer(cfg, did_owner=req.did_owner)
    out_path = f"/tmp/sisoul-lora-{req.did_owner[-8:]}.safetensors"
    result = trainer.train(out_path)
    return {
        "adapter_name": result.adapter.name,
        "rank": result.adapter.rank,
        "size_bytes": result.adapter.size_bytes,
        "file_path": result.adapter.file_path,
        "epoch_count": result.epoch_count,
        "train_loss_final": result.train_loss_final,
    }


class FederatedRequest(BaseModel):
    aggregator_did: str
    base_adapter_name: str = "personal-base"
    base_model: str = "meta-llama/Llama-3.1-8B"
    rank: int = 16
    peer_deltas: list[dict]  # [{"did": "did:key:z6Mk...", "delta": "<stub>"}]
    round_id: int = 1
    min_participants: int = 2


@router.post("/lora/federate")
def lora_federate(req: FederatedRequest) -> dict:
    """v3.0 federated LoRA aggregation (foundation: FedAvg stub)."""
    if not req.aggregator_did.startswith("did:key:"):
        raise HTTPException(status_code=400, detail="invalid aggregator_did")

    cfg = FederatedLoRAConfig(min_participants=req.min_participants)
    agg = FederatedLoRAAggregator(cfg, aggregator_did=req.aggregator_did)

    base = LoRAAdapter(
        name=req.base_adapter_name,
        version="0.1",
        base_model=req.base_model,
        rank=req.rank,
        file_path=f"/tmp/sisoul-base-{req.base_adapter_name}.safetensors",
        size_bytes=20_000_000,
        did_owner=req.aggregator_did,
        trained_at="2026-06-04",
    )

    try:
        new_adapter, fed_round = agg.federate(base, req.peer_deltas, round_id=req.round_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "new_adapter_name": new_adapter.name,
        "rank": new_adapter.rank,
        "round_id": fed_round.round_id,
        "delta_count": fed_round.delta_count,
        "participants": fed_round.participants,
        "aggregator_did": fed_round.aggregator_did,
        "aggregated_at": fed_round.aggregated_at,
    }
