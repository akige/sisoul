"""Friends API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .types import (
    Friend,
    FriendAddRequest,
    FriendBorrowRequest,
    FriendLendRequest,
    ListFriendsResponse,
)

if TYPE_CHECKING:
    from .client import SisoulClient


class FriendsAPI:
    def __init__(self, client: SisoulClient) -> None:
        self._c = client

    def list(self) -> list[Friend]:
        raw = self._c.get("/friend/list")
        return ListFriendsResponse.model_validate(raw).friends

    def add(self, req: FriendAddRequest | dict) -> Friend:
        if isinstance(req, dict):
            req = FriendAddRequest.model_validate(req)
        if not req.did:
            raise ValueError("friends.add: did required")
        raw = self._c.post("/friend/add", req.model_dump(exclude_none=True))
        return Friend.model_validate(raw)

    def remove(self, did: str) -> None:
        if not did:
            raise ValueError("friends.remove: did required")
        self._c.post("/friend/remove", {"did": did})

    def lend(self, req: FriendLendRequest | dict) -> dict[str, Any]:
        if isinstance(req, dict):
            req = FriendLendRequest.model_validate(req)
        if not req.friend_did:
            raise ValueError("friends.lend: friend_did required")
        if not req.resource_id:
            raise ValueError("friends.lend: resource_id required")
        return self._c.post("/friend/lend", req.model_dump(exclude_none=True))

    def borrow(self, req: FriendBorrowRequest | dict) -> dict[str, Any]:
        if isinstance(req, dict):
            req = FriendBorrowRequest.model_validate(req)
        if not req.owner_did:
            raise ValueError("friends.borrow: owner_did required")
        if not req.resource_id:
            raise ValueError("friends.borrow: resource_id required")
        return self._c.post("/friend/borrow", req.model_dump(exclude_none=True))

    def strong_ties(self, threshold: float = 0.7) -> list[Friend]:
        return [f for f in self.list() if f.trust_level >= threshold]
