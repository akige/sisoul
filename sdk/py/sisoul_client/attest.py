"""Attest API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types import (
    AttestCreateRequest,
    AttestCreateResponse,
    AttestEntry,
    AttestHistoryResponse,
)

if TYPE_CHECKING:
    from .client import SisoulClient


class AttestAPI:
    def __init__(self, client: SisoulClient) -> None:
        self._c = client

    def history(self) -> list[AttestEntry]:
        raw = self._c.get("/attest/history")
        return AttestHistoryResponse.model_validate(raw).history

    def create(self, req: AttestCreateRequest | dict) -> AttestCreateResponse:
        if isinstance(req, dict):
            req = AttestCreateRequest.model_validate(req)
        if not req.schema_:
            raise ValueError("attest.create: schema required")
        if not req.subject_did:
            raise ValueError("attest.create: subject_did required")
        raw = self._c.post(
            "/attest/create",
            req.model_dump(exclude_none=True, by_alias=True),
        )
        return AttestCreateResponse.model_validate(raw)

    def by_schema(self, schema: str) -> list[AttestEntry]:
        return [e for e in self.history() if e.schema_ == schema]

    def since(self, timestamp_sec: int) -> list[AttestEntry]:
        return [e for e in self.history() if e.timestamp >= timestamp_sec]
