"""sisoul daemon · Goal-mode v1.1 endpoints (Phase 2 P2-3).

- GET  /sisoul/goal/upcoming?within_hours=24
- POST /sisoul/goal/{id}/snooze
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from sisoul.goal.scheduler import scan_upcoming, snooze_goal
from sisoul.vault.storage import DEFAULT_VAULT_DIR


def _resolve_vault_root(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get("SISOUL_VAULT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_VAULT_DIR


class UpcomingItem(BaseModel):
    id: str
    title: str
    next_review_at: str
    seconds_until: float
    status: str
    path: str


class UpcomingResponse(BaseModel):
    vault_root: str
    within_hours: float
    items: list[UpcomingItem]


class SnoozeRequest(BaseModel):
    hours: float = Field(default=24, gt=0, le=24 * 365)
    vault: str | None = None


class SnoozeResponse(BaseModel):
    id: str
    old_next_review_at: str | None
    new_next_review_at: str
    path: str


def _ensure_safe_id(file_id: str) -> None:
    if "/" in file_id or "\\" in file_id or ".." in file_id or file_id.startswith("."):
        raise HTTPException(status_code=400, detail=f"invalid id: {file_id!r}")


def create_router() -> APIRouter:
    router = APIRouter(prefix="/sisoul/goal", tags=["goal"])

    @router.get("/upcoming", response_model=UpcomingResponse)
    def upcoming(
        within_hours: float = Query(default=24, gt=0, le=24 * 365),
        vault: str | None = Query(default=None),
    ) -> UpcomingResponse:
        root = _resolve_vault_root(vault)
        items = scan_upcoming(root, within_hours=within_hours)
        return UpcomingResponse(
            vault_root=str(root),
            within_hours=within_hours,
            items=[
                UpcomingItem(
                    id=g.id,
                    title=g.title,
                    next_review_at=str(g.next_review_at),
                    seconds_until=g.seconds_until,
                    status=g.status,
                    path=str(g.path),
                )
                for g in items
            ],
        )

    @router.post("/{goal_id}/snooze", response_model=SnoozeResponse)
    def snooze(goal_id: str, req: SnoozeRequest) -> SnoozeResponse:
        _ensure_safe_id(goal_id)
        root = _resolve_vault_root(req.vault)
        try:
            result = snooze_goal(goal_id, req.hours, vault=root)
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return SnoozeResponse(
            id=result["id"],
            old_next_review_at=result["old_next_review_at"],
            new_next_review_at=result["new_next_review_at"],
            path=result["path"],
        )

    return router


goal_router = create_router()
