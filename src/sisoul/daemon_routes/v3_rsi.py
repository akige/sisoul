"""daemon HTTP routes for v3 RSI (Recursive Self-Improvement) framework.

Endpoints (skeleton):
- GET  /v3/rsi/status            framework status + components loaded
- POST /v3/rsi/iterate           run one RSI iteration (godel/alpha_evolve/dspy)
- GET  /v3/rsi/history           past iterations
- POST /v3/rsi/gossip             broadcast a mutation to federated peers
- GET  /v3/rsi/peers             list received peer mutations
"""
from __future__ import annotations
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v3/rsi", tags=["v3-rsi"])


class RSIStatusResponse(BaseModel):
    framework: str = "sisoul-v3-rsi"
    version: str = "0.1.0-alpha-skeleton"
    components: dict
    safety_boundary_active: bool = True


class RSIIterateRequest(BaseModel):
    mode: str = Field("godel", description="godel | alpha_evolve | dspy")
    target_module: Optional[str] = None
    dry_run: bool = True


class RSIIterateResponse(BaseModel):
    iteration_id: str
    mode: str
    started_at: str
    accepted: bool
    fitness: Optional[float] = None
    candidate_count: int = 0
    reason: str = ""


class RSIHistoryEntry(BaseModel):
    iteration_id: str
    mode: str
    started_at: str
    accepted: bool
    fitness: Optional[float]


class RSIHistoryResponse(BaseModel):
    iterations: list[RSIHistoryEntry]
    count: int


class RSIGossipRequest(BaseModel):
    mutation: dict


class RSIGossipResponse(BaseModel):
    broadcast: bool
    envelope: Optional[dict] = None
    error: str = ""


class RSIPeersResponse(BaseModel):
    peer_mutations: list[dict]
    count: int


@router.get("/status", response_model=RSIStatusResponse)
async def rsi_status() -> RSIStatusResponse:
    """RSI framework status — which components are importable."""
    components = {}
    for name in ("godel_agent", "alpha_evolve", "dspy_optimize", "evaluator", "federated_rsi"):
        try:
            __import__(f"sisoul.v3.rsi.{name}")
            components[name] = "loaded"
        except ImportError as e:
            components[name] = f"unavailable: {e}"
    return RSIStatusResponse(components=components)


@router.post("/iterate", response_model=RSIIterateResponse)
async def rsi_iterate(req: RSIIterateRequest) -> RSIIterateResponse:
    """Run one RSI iteration.

    Skeleton: does NOT call real LLM. Returns deterministic 'no-op' iteration
    so PWA can demo the wire. Full impl needs LLM adapter + Evaluator wired.
    """
    iter_id = f"rsi-{int(time.time() * 1000)}"
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if req.mode not in {"godel", "alpha_evolve", "dspy"}:
        raise HTTPException(status_code=400, detail=f"unknown mode: {req.mode}")
    # Safety: dry_run default = True. Real iterate needs LLM adapter wired.
    return RSIIterateResponse(
        iteration_id=iter_id,
        mode=req.mode,
        started_at=started_at,
        accepted=False,
        fitness=None,
        candidate_count=0,
        reason="skeleton: LLM adapter not wired; dry_run default" if req.dry_run else "skeleton: not implemented",
    )


@router.get("/history", response_model=RSIHistoryResponse)
async def rsi_history() -> RSIHistoryResponse:
    """List past RSI iterations from vault/rsi/history.jsonl."""
    vault = Path(os.environ.get("SISOUL_VAULT", "~/.sisoul")).expanduser()
    hist_file = vault / "rsi" / "history.jsonl"
    iterations: list[RSIHistoryEntry] = []
    if hist_file.exists():
        import json

        for line in hist_file.read_text().splitlines():
            try:
                obj = json.loads(line)
                iterations.append(RSIHistoryEntry(**obj))
            except Exception:
                continue
    return RSIHistoryResponse(iterations=iterations, count=len(iterations))


@router.post("/gossip", response_model=RSIGossipResponse)
async def rsi_gossip(req: RSIGossipRequest) -> RSIGossipResponse:
    """Broadcast a mutation envelope via FederatedRSI.

    Skeleton: transport not wired → returns broadcast=False.
    """
    try:
        from sisoul.v3.rsi.federated_rsi import FederatedRSI, RSI_MUTATION_TOPIC

        # Without a real transport this raises. Caller should wire transport.
        fr = FederatedRSI(self_did="did:key:self", transport=None, topic=RSI_MUTATION_TOPIC)
        await fr.gossip_mutation(req.mutation)
        return RSIGossipResponse(broadcast=True, envelope=req.mutation)
    except RuntimeError as e:
        return RSIGossipResponse(broadcast=False, envelope=None, error=str(e))
    except Exception as e:
        return RSIGossipResponse(broadcast=False, envelope=None, error=f"unexpected: {e}")


@router.get("/peers", response_model=RSIPeersResponse)
async def rsi_peers() -> RSIPeersResponse:
    """Received peer mutations (in-memory queue from FederatedRSI)."""
    # Skeleton: no persistent peer queue; daemon would inject FederatedRSI instance
    # and we'd return its `received_mutations`. For now return empty.
    return RSIPeersResponse(peer_mutations=[], count=0)


__all__ = ["router"]
