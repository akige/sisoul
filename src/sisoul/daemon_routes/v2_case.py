"""daemon HTTP routes for v2.0 Case Graph (foundation skeleton).

Endpoints (foundation):
- POST /v2/case          add a case
- GET  /v2/case/{id}     fetch case
- GET  /v2/case/search   ?q= naive search
- GET  /v2/case          list all

Full impl (v2.0 ship): ChromaDB embed retrieval + GossipSub broadcast + EAS attest.
"""
from __future__ import annotations
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from sisoul.v2.case_graph import Case, CaseStore, derive_case_id

router = APIRouter(prefix="/v2/case", tags=["v2-case-graph"])


def _store() -> CaseStore:
    vault = Path(os.environ.get("SISOUL_VAULT", "~/.sisoul")).expanduser()
    return CaseStore(vault)


class CaseAddRequest(BaseModel):
    question: str
    answer: str
    did_author: str
    sources: list[dict] = []
    tags: list[str] = []


class CaseAddResponse(BaseModel):
    id: str
    path: str


@router.post("", response_model=CaseAddResponse)
def add_case(req: CaseAddRequest) -> CaseAddResponse:
    case = Case(
        id=derive_case_id(req.question, req.did_author),
        question=req.question,
        answer=req.answer,
        did_author=req.did_author,
        sources=req.sources,
        tags=req.tags,
    )
    if not case.validate():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid case")
    path = _store().add(case)
    return CaseAddResponse(id=case.id, path=str(path))


@router.get("/{case_id}")
def get_case(case_id: str) -> dict:
    case = _store().get(case_id)
    if not case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="case not found")
    from dataclasses import asdict
    return asdict(case)


@router.get("")
def list_cases(limit: int = 100) -> dict:
    cases = _store().list_all()[:limit]
    from dataclasses import asdict
    return {"cases": [asdict(c) for c in cases], "count": len(cases)}


@router.get("/search/")
def search_cases(q: str, top_k: int = 5) -> dict:
    ret = _store().search(q, top_k=top_k)
    from dataclasses import asdict
    return {
        "query": ret.query,
        "cases": [asdict(c) for c in ret.cases],
        "top_k": ret.top_k,
        "is_hit": ret.is_hit(),
    }
