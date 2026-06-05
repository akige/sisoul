"""sisoul daemon — founder-agent HTTP routes.

Endpoints:
- GET  /v1/founder/status           agent status (vault loaded, provider, rsi_enabled)
- POST /v1/founder/chat             chat with founder-agent (retrieval-only if no LLM)
- POST /v1/founder/recall           query the case-graph directly
- GET  /v1/founder/cases            list all loaded cases
- GET  /v1/founder/lessons          list all loaded lessons

Storage: vault/founder/* (see sisoul.founder.vault).

Security gates (round 10 hardening):
- Per-client rate limit on /chat: SISOUL_FOUNDER_RPM (default 20 requests / 60s)
- Question length cap: 4096 chars max (Pydantic Field constraint)
- Client IP based rate-limit bucket; for P2P chat bridge, use DID-based bucket
"""
from __future__ import annotations
import os
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from sisoul.founder.agent import FounderAgent
from sisoul.founder.vault import FounderVault

founder_router = APIRouter(prefix="/v1/founder", tags=["founder-agent"])

# ── rate limit (per source) ────────────────────────────────────────────────
_RATE_BUCKETS: dict[str, deque] = defaultdict(deque)
_RATE_RPM = int(os.environ.get("SISOUL_FOUNDER_RPM", "20"))  # req/min/client
_RATE_WINDOW = 60.0  # seconds


def _rate_limit_check(key: str) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds)."""
    now = time.time()
    bucket = _RATE_BUCKETS[key]
    # Drop entries older than window
    while bucket and now - bucket[0] > _RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= _RATE_RPM:
        retry = int(_RATE_WINDOW - (now - bucket[0])) + 1
        return False, retry
    bucket.append(now)
    return True, 0


# ── request / response models ────────────────────────────────────────────────


class FounderStatusResponse(BaseModel):
    vault_root: str
    vault_size: dict
    config: dict


class FounderChatRequest(BaseModel):
    # max_length=4096 (was 8192): 4 KiB question cap to prevent
    # token-burst abuse via the public chat surface.
    question: str = Field(..., min_length=1, max_length=4096)
    record: bool = True


class FounderChatResponse(BaseModel):
    answer: str
    provider: str
    cases_recalled: list[str]
    mode: str
    timestamp: str


class FounderRecallRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=512)
    top_k: int = Field(default=3, ge=1, le=20)


class RecalledCase(BaseModel):
    id: str
    question: str
    answer: str
    score: float
    tags: list[str]


class FounderRecallResponse(BaseModel):
    query: str
    matches: list[RecalledCase]


class FounderCasesResponse(BaseModel):
    count: int
    cases: list[dict]


class FounderLessonsResponse(BaseModel):
    count: int
    lessons: list[dict]


# ── route handlers ────────────────────────────────────────────────────────────


def _agent() -> FounderAgent:
    """Fresh FounderAgent per request — vault is lightweight."""
    return FounderAgent()


@founder_router.get("/status", response_model=FounderStatusResponse)
async def founder_status() -> FounderStatusResponse:
    agent = _agent()
    return FounderStatusResponse(**agent.status())


@founder_router.post("/chat", response_model=FounderChatResponse)
async def founder_chat(req: FounderChatRequest, request: Request) -> FounderChatResponse:
    # Source key: client IP (for local HTTP) or X-Source-DID header (for P2P bridge).
    src_did = request.headers.get("x-source-did")
    bucket_key = src_did or (request.client.host if request.client else "unknown")
    allowed, retry_after = _rate_limit_check(bucket_key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"rate limit exceeded ({_RATE_RPM}/min), retry after {retry_after}s",
            headers={"Retry-After": str(retry_after)},
        )
    agent = _agent()
    result = agent.chat(req.question, adapter=None, record=req.record)
    return FounderChatResponse(**result)


@founder_router.post("/recall", response_model=FounderRecallResponse)
async def founder_recall(req: FounderRecallRequest) -> FounderRecallResponse:
    vault = FounderVault()
    matches = vault.recall(req.query, top_k=req.top_k)
    out = [
        RecalledCase(
            id=c.id,
            question=c.question,
            answer=c.answer,
            score=score,
            tags=c.tags,
        )
        for c, score in matches
    ]
    return FounderRecallResponse(query=req.query, matches=out)


@founder_router.get("/cases", response_model=FounderCasesResponse)
async def founder_cases() -> FounderCasesResponse:
    vault = FounderVault()
    cases = [
        {
            "id": c.id,
            "question": c.question,
            "answer": c.answer[:200] + ("..." if len(c.answer) > 200 else ""),
            "tags": c.tags,
            "created_at": c.created_at,
        }
        for c in vault.all_cases()
    ]
    return FounderCasesResponse(count=len(cases), cases=cases)


@founder_router.get("/lessons", response_model=FounderLessonsResponse)
async def founder_lessons() -> FounderLessonsResponse:
    vault = FounderVault()
    lessons = [
        {
            "id": l.id,
            "principle": l.principle,
            "context": l.context,
            "applies_to": l.applies_to,
        }
        for l in vault.all_lessons()
    ]
    return FounderLessonsResponse(count=len(lessons), lessons=lessons)


__all__ = ["founder_router"]
