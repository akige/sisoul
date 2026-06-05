"""sisoul daemon — founder-agent HTTP routes.

Endpoints:
- GET  /v1/founder/status           agent status (vault loaded, provider, rsi_enabled)
- POST /v1/founder/chat             chat with founder-agent (retrieval-only if no LLM)
- POST /v1/founder/recall           query the case-graph directly
- GET  /v1/founder/cases            list all loaded cases
- GET  /v1/founder/lessons          list all loaded lessons

Storage: vault/founder/* (see sisoul.founder.vault).
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from sisoul.founder.agent import FounderAgent
from sisoul.founder.vault import FounderVault

founder_router = APIRouter(prefix="/v1/founder", tags=["founder-agent"])


# ── request / response models ────────────────────────────────────────────────


class FounderStatusResponse(BaseModel):
    vault_root: str
    vault_size: dict
    config: dict


class FounderChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=8192)
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
async def founder_chat(req: FounderChatRequest) -> FounderChatResponse:
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
