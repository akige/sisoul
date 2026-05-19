"""Goals API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import Goal, GoalCreateRequest, GoalUpdateRequest, ListGoalsResponse

if TYPE_CHECKING:
    from .client import SisoulClient


class GoalsAPI:
    def __init__(self, client: SisoulClient) -> None:
        self._c = client

    def list(self) -> list[Goal]:
        raw = self._c.get("/goals/list")
        return ListGoalsResponse.model_validate(raw).goals

    def add(self, req: GoalCreateRequest | dict) -> Goal:
        if isinstance(req, dict):
            req = GoalCreateRequest.model_validate(req)
        if not req.title:
            raise ValueError("goals.add: title required")
        raw = self._c.post("/goals/add", req.model_dump(exclude_none=True))
        return Goal.model_validate(raw)

    def update(self, req: GoalUpdateRequest | dict) -> Goal:
        if isinstance(req, dict):
            req = GoalUpdateRequest.model_validate(req)
        if not req.id:
            raise ValueError("goals.update: id required")
        raw = self._c.post("/goals/update", req.model_dump(exclude_none=True))
        return Goal.model_validate(raw)

    def delete(self, goal_id: str) -> None:
        if not goal_id:
            raise ValueError("goals.delete: id required")
        self._c.post("/goals/delete", {"id": goal_id})

    def bump_progress(self, goal_id: str, delta: float) -> Goal:
        goals = self.list()
        target = next((g for g in goals if g.id == goal_id), None)
        if target is None:
            raise ValueError(f"goal {goal_id} not found")
        nxt = max(0.0, min(1.0, target.progress + delta))
        return self.update(GoalUpdateRequest(id=goal_id, progress=nxt))
