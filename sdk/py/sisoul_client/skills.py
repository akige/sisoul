"""Skills API - §28 §3.6 packaging spec.

注意: /sisoul/skill/* 路径已含 /sisoul 前缀, 走 absolute=True (不通过 base_url 拼接).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import (
    EndSessionRequest,
    EndSessionResponse,
    SkillBorrowRequest,
    SkillBorrowResponse,
    SkillCreateRequest,
    SkillCreateResponse,
    SkillItem,
    SkillLendRequest,
    SkillLendResponse,
    SkillListResponse,
    SkillSessionItem,
    SkillSessionsResponse,
)

if TYPE_CHECKING:
    from .client import SisoulClient


def _abs(base_url: str, path: str) -> str:
    # base_url 形如 http://host:8088/sisoul, path 形如 /sisoul/skill/list
    # 取 base_url 的 origin 部分拼 path 避免重复 /sisoul.
    if path.startswith("http"):
        return path
    if "://" in base_url:
        scheme, rest = base_url.split("://", 1)
        host = rest.split("/", 1)[0]
        return f"{scheme}://{host}{path}"
    return path


class SkillsAPI:
    def __init__(self, client: SisoulClient) -> None:
        self._c = client

    def list(self) -> SkillListResponse:
        url = _abs(self._c.base_url, "/sisoul/skill/list")
        raw = self._c.get(url, absolute=True)
        return SkillListResponse.model_validate(raw)

    def owned(self) -> list[SkillItem]:
        return self.list().owned

    def available(self) -> list[SkillItem]:
        return self.list().available_to_borrow

    def create(self, req: SkillCreateRequest | dict) -> SkillCreateResponse:
        if isinstance(req, dict):
            req = SkillCreateRequest.model_validate(req)
        if not req.name:
            raise ValueError("skills.create: name required")
        if not req.system_prompt:
            raise ValueError("skills.create: system_prompt required")
        url = _abs(self._c.base_url, "/sisoul/skill/create")
        raw = self._c.post(url, req.model_dump(exclude_none=True), absolute=True)
        return SkillCreateResponse.model_validate(raw)

    def lend(self, req: SkillLendRequest | dict) -> SkillLendResponse:
        if isinstance(req, dict):
            req = SkillLendRequest.model_validate(req)
        if not req.skill_id:
            raise ValueError("skills.lend: skill_id required")
        url = _abs(self._c.base_url, "/sisoul/skill/lend")
        raw = self._c.post(url, req.model_dump(exclude_none=True), absolute=True)
        return SkillLendResponse.model_validate(raw)

    def borrow(self, req: SkillBorrowRequest | dict) -> SkillBorrowResponse:
        if isinstance(req, dict):
            req = SkillBorrowRequest.model_validate(req)
        if not req.owner_did:
            raise ValueError("skills.borrow: owner_did required")
        if not req.qualified_name:
            raise ValueError("skills.borrow: qualified_name required")
        url = _abs(self._c.base_url, "/sisoul/skill/borrow")
        raw = self._c.post(url, req.model_dump(exclude_none=True), absolute=True)
        return SkillBorrowResponse.model_validate(raw)

    def sessions(self) -> list[SkillSessionItem]:
        url = _abs(self._c.base_url, "/sisoul/skill/sessions")
        raw = self._c.get(url, absolute=True)
        return SkillSessionsResponse.model_validate(raw).sessions

    def active_sessions(self) -> list[SkillSessionItem]:
        return [s for s in self.sessions() if s.status == "active"]

    def end_session(self, session_id: str, reason: str | None = None) -> EndSessionResponse:
        if not session_id:
            raise ValueError("skills.end_session: session_id required")
        req = EndSessionRequest(session_id=session_id, reason=reason)
        url = _abs(self._c.base_url, "/sisoul/skill/end-session")
        raw = self._c.post(url, req.model_dump(exclude_none=True), absolute=True)
        return EndSessionResponse.model_validate(raw)
