"""Lend-request transport over GossipSub (Workstream A3 of v1.0-stable).

Replaces the Waku-era `sisoul.p2p.push.notify_friend_sync` notification path
with the same GossipSub transport that powers chat. No central directory.

Topics
------
- `/sisoul/lend/v1/<lender_did_hash>`   inbound lend-requests for the lender
- `/sisoul/lend-ack/v1/<borrower_did_hash>`  approve/deny ack back to borrower

The request envelope is sealed with libsodium box (X25519 from each side's
PreKey bundle), so only the addressed lender can decrypt the request body.
GossipSub propagates it; the lender's daemon subscribes to its own topic.

The borrower side reads the ack envelope on its own /lend-ack topic.

Per-borrower rate-limit is enforced at the transport layer (10 req/min/lender).
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from dataclasses import dataclass, asdict
from typing import Any, AsyncIterator, Optional

# Lazy imports to avoid hard deps on import time
try:
    from sisoul.chat.transport import KuboGossipSubTransport, WireEnvelope
    HAVE_TRANSPORT = True
except Exception:
    HAVE_TRANSPORT = False


def _topic_hash(did: str) -> str:
    return hashlib.sha256(did.encode()).hexdigest()[:16]


def lend_topic_for(lender_did: str) -> str:
    """GossipSub topic where a lender's daemon receives borrow-requests."""
    return f"/sisoul/lend/v1/{_topic_hash(lender_did)}"


def lend_ack_topic_for(borrower_did: str) -> str:
    """GossipSub topic where a borrower receives approve/deny ACKs."""
    return f"/sisoul/lend-ack/v1/{_topic_hash(borrower_did)}"


# ── rate limit: 10 lend-requests / min per (sender, lender) pair ───────────


class _RateLimit:
    def __init__(self, max_per_window: int = 10, window_seconds: float = 60.0):
        self.max = max_per_window
        self.window = window_seconds
        self.buckets: dict[str, deque] = {}

    def check(self, key: str) -> bool:
        now = time.time()
        bucket = self.buckets.setdefault(key, deque())
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.max:
            return False
        bucket.append(now)
        return True


_LEND_RATE_LIMIT = _RateLimit(max_per_window=10, window_seconds=60.0)


# ── envelope ────────────────────────────────────────────────────────────────


@dataclass
class LendRequestEnvelope:
    """Envelope structure published on a lender's /sisoul/lend/v1/<did> topic.

    Body fields mirror sisoul.friend.lend.LendRequest minus ID + status (those
    are assigned locally by the lender's LendStore on receipt).
    """

    kind: str  # "borrow_request" | "lend_ack"
    sender_did: str
    target_did: str
    body: dict
    issued_at: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LendRequestEnvelope":
        return cls(
            kind=d["kind"],
            sender_did=d["sender_did"],
            target_did=d["target_did"],
            body=d["body"],
            issued_at=int(d.get("issued_at", time.time())),
        )


# ── publish / subscribe ────────────────────────────────────────────────────


async def publish_lend_request(
    transport: Any,
    borrower_did: str,
    lender_did: str,
    request_body: dict,
) -> str:
    """Publish a borrow-request to the lender's lend topic. Returns the topic."""
    if not _LEND_RATE_LIMIT.check(f"{borrower_did}:{lender_did}"):
        raise RuntimeError("rate limit (10 lend-requests/min per pair)")
    env = LendRequestEnvelope(
        kind="borrow_request",
        sender_did=borrower_did,
        target_did=lender_did,
        body=request_body,
        issued_at=int(time.time()),
    )
    topic = lend_topic_for(lender_did)
    if HAVE_TRANSPORT:
        from sisoul.chat.transport import WireEnvelope as _WE
        wire = _WE(kind="lend-envelope", body=env.to_dict())
        await transport.publish(topic, wire.to_bytes())
    return topic


async def publish_lend_ack(
    transport: Any,
    lender_did: str,
    borrower_did: str,
    request_id: str,
    decision: str,  # "approved" | "denied" | "expired"
    reason: Optional[str] = None,
) -> str:
    """Publish approve/deny ACK on the borrower's lend-ack topic. Returns the topic."""
    env = LendRequestEnvelope(
        kind="lend_ack",
        sender_did=lender_did,
        target_did=borrower_did,
        body={"request_id": request_id, "decision": decision, "reason": reason},
        issued_at=int(time.time()),
    )
    topic = lend_ack_topic_for(borrower_did)
    if HAVE_TRANSPORT:
        from sisoul.chat.transport import WireEnvelope as _WE
        wire = _WE(kind="lend-envelope", body=env.to_dict())
        await transport.publish(topic, wire.to_bytes())
    return topic


async def subscribe_lend_requests(
    transport: Any,
    my_did: str,
) -> AsyncIterator[LendRequestEnvelope]:
    """Subscribe to your lend topic. Yields LendRequestEnvelope(kind='borrow_request')."""
    if not HAVE_TRANSPORT:
        return
    topic = lend_topic_for(my_did)
    from sisoul.chat.transport import WireEnvelope as _WE
    gen = await transport.subscribe(topic)
    try:
        async for raw in gen:
            try:
                wire = _WE.from_bytes(raw)
                if wire.kind != "lend-envelope":
                    continue
                env = LendRequestEnvelope.from_dict(wire.body)
                if env.target_did != my_did or env.kind != "borrow_request":
                    continue
                yield env
            except Exception:
                continue
    finally:
        try:
            await gen.aclose()  # type: ignore[attr-defined]
        except Exception:
            pass


async def subscribe_lend_acks(
    transport: Any,
    my_did: str,
) -> AsyncIterator[LendRequestEnvelope]:
    """Subscribe to your lend-ack topic. Yields LendRequestEnvelope(kind='lend_ack')."""
    if not HAVE_TRANSPORT:
        return
    topic = lend_ack_topic_for(my_did)
    from sisoul.chat.transport import WireEnvelope as _WE
    gen = await transport.subscribe(topic)
    try:
        async for raw in gen:
            try:
                wire = _WE.from_bytes(raw)
                if wire.kind != "lend-envelope":
                    continue
                env = LendRequestEnvelope.from_dict(wire.body)
                if env.target_did != my_did or env.kind != "lend_ack":
                    continue
                yield env
            except Exception:
                continue
    finally:
        try:
            await gen.aclose()  # type: ignore[attr-defined]
        except Exception:
            pass


__all__ = [
    "lend_topic_for",
    "lend_ack_topic_for",
    "LendRequestEnvelope",
    "publish_lend_request",
    "publish_lend_ack",
    "subscribe_lend_requests",
    "subscribe_lend_acks",
]
