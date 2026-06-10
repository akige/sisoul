"""In-process daemon event bus → PWA SSE (P1 · 2026-06-10).

Replaces the heartbeat-only `/sisoul/notify/stream` stub with real events:

- ``lend.request``  — lender daemon ingested an inbound borrow-request
- ``lend.update``   — lender approved / denied a request
- ``borrow.update`` — borrower received a GossipSub ack for its request

Design: plain asyncio queues, one per SSE subscriber. ``publish`` is callable
from both the event loop (ingest loop) and FastAPI threadpool threads
(approve/deny routes) — the loop reference is captured at daemon startup via
``bind_loop``.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Optional

_loop: Optional[asyncio.AbstractEventLoop] = None
_subscribers: set[asyncio.Queue] = set()

MAX_QUEUE = 100  # per-subscriber backlog cap (slow consumer drops oldest)


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called once at daemon startup so threadpool routes can publish."""
    global _loop
    _loop = loop


def publish(event_type: str, data: dict[str, Any]) -> None:
    """Fan an event out to all SSE subscribers. Never raises."""
    evt = {"type": event_type, "ts": int(time.time()), "data": data}

    def _fanout() -> None:
        for q in list(_subscribers):
            try:
                if q.qsize() >= MAX_QUEUE:
                    q.get_nowait()  # drop oldest for slow consumers
                q.put_nowait(evt)
            except Exception:  # noqa: BLE001
                pass

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None:
        _fanout()
    elif _loop is not None and _loop.is_running():
        _loop.call_soon_threadsafe(_fanout)
    # no loop yet (early startup) → drop silently; SSE 没人订阅也无所谓


async def subscribe() -> AsyncIterator[dict[str, Any]]:
    """Async iterator of events for one SSE connection."""
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.add(q)
    try:
        while True:
            yield await q.get()
    finally:
        _subscribers.discard(q)


def attach_queue() -> asyncio.Queue:
    """SSE endpoint 直接拿 queue 用 (配 asyncio.wait_for 不会 cancel 坏
    async generator — wait_for(gen.__anext__()) 超时会把 generator 打死)."""
    q: asyncio.Queue = asyncio.Queue()
    _subscribers.add(q)
    return q


def detach_queue(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def sse_format(evt: dict[str, Any]) -> bytes:
    """One event in SSE wire format: event: <type> / data: <inner payload json>.

    data 只发内层 payload — PWA notifyStream 直接 JSON.parse(e.data) 当
    event data 用 (LendRequestItem 等 shape), 不要再包 {type, ts, data} 壳.
    """
    payload = dict(evt.get("data") or {})
    payload.setdefault("ts", evt.get("ts"))
    return (
        f"event: {evt.get('type', 'message')}\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    ).encode("utf-8")


__all__ = ["bind_loop", "publish", "subscribe", "sse_format"]
