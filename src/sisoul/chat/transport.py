"""Chat transport over IPFS GossipSub (with local in-memory fallback for tests).

Topic conventions (P2-G §3):

- Chat ciphertext:   ``/sisoul/chat/v1/<topic16>``
  ``topic16 = sha256(min(a,b) || ":" || max(a,b))[:16].hex()`` over DIDs.
- Pre-key announce:  ``/sisoul/prekey/v1/<did_short>``

The transport is intentionally minimal — encryption is fully owned by
:class:`sisoul.chat.double_ratchet.ChatSession`; this module only routes opaque
bytes on the wire.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Deque

logger = logging.getLogger(__name__)


CHAT_TOPIC_PREFIX = "/sisoul/chat/v1/"
PREKEY_TOPIC_PREFIX = "/sisoul/prekey/v1/"


def chat_topic_for(did_a: str, did_b: str) -> str:
    """Returns the deterministic GossipSub topic for a chat between two DIDs."""
    lo, hi = sorted([did_a, did_b])
    h = hashlib.sha256(f"{lo}:{hi}".encode()).hexdigest()[:16]
    return f"{CHAT_TOPIC_PREFIX}{h}"


def prekey_topic_for(did: str) -> str:
    """Returns the GossipSub topic where ``did`` periodically announces its bundle."""
    short = hashlib.sha256(did.encode()).hexdigest()[:16]
    return f"{PREKEY_TOPIC_PREFIX}{short}"


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class ChatTransport(ABC):
    """Abstract pubsub transport for chat / pre-key messages."""

    @abstractmethod
    async def publish(self, topic: str, payload: bytes) -> None: ...

    @abstractmethod
    async def subscribe(self, topic: str) -> AsyncIterator[bytes]: ...

    @abstractmethod
    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# In-memory transport (default for tests; also used as fallback if kubo down)
# ---------------------------------------------------------------------------

class MemoryTransport(ChatTransport):
    """Process-local in-memory pubsub. Identical instance shared across peers in tests.

    Use the module-level :func:`get_shared_memory_transport` to get a single
    bus shared by all peers in the same process.
    """

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue[bytes]]] = defaultdict(list)
        self._history: dict[str, Deque[bytes]] = defaultdict(lambda: deque(maxlen=128))
        self._closed = False

    async def publish(self, topic: str, payload: bytes) -> None:
        if self._closed:
            raise RuntimeError("transport closed")
        self._history[topic].append(payload)
        for q in list(self._queues.get(topic, [])):
            await q.put(payload)

    async def subscribe(self, topic: str) -> AsyncIterator[bytes]:
        q: asyncio.Queue[bytes] = asyncio.Queue()
        # Replay history so a late subscriber still gets a recent message
        for old in self._history.get(topic, ()):
            await q.put(old)
        self._queues[topic].append(q)

        async def _gen() -> AsyncIterator[bytes]:
            try:
                while True:
                    yield await q.get()
            finally:
                try:
                    self._queues[topic].remove(q)
                except ValueError:
                    pass

        return _gen()

    async def close(self) -> None:
        self._closed = True
        self._queues.clear()


_shared_memory_transport: MemoryTransport | None = None


def get_shared_memory_transport() -> MemoryTransport:
    """Singleton MemoryTransport for in-process tests."""
    global _shared_memory_transport
    if _shared_memory_transport is None or _shared_memory_transport._closed:
        _shared_memory_transport = MemoryTransport()
    return _shared_memory_transport


def reset_shared_memory_transport() -> None:
    """Reset the singleton (for test isolation)."""
    global _shared_memory_transport
    _shared_memory_transport = None


# ---------------------------------------------------------------------------
# Kubo GossipSub transport
# ---------------------------------------------------------------------------

class KuboGossipSubTransport(ChatTransport):
    """Talks to a local Kubo IPFS daemon via HTTP API for pubsub.

    Requires ``ipfs daemon --enable-pubsub-experiment`` (already enabled by
    :class:`sisoul.p2p.ipfs_kubo.IPFSKuboNode`).
    """

    def __init__(self, api_url: str = "http://127.0.0.1:5001") -> None:
        self._api = api_url.rstrip("/")
        try:
            import httpx
            self._httpx = httpx
        except ImportError as exc:
            raise RuntimeError("httpx required for KuboGossipSubTransport") from exc
        self._client: Any = None

    async def _get_client(self):
        if self._client is None:
            self._client = self._httpx.AsyncClient(timeout=None)
        return self._client

    @staticmethod
    def _encode_topic(topic: str) -> str:
        # Kubo >= 0.27 requires the topic arg to be MULTIBASE-encoded; for
        # base64url-without-padding the multibase prefix is 'u'. (Older kubo
        # accepted bare base64url — that 500s on 0.32 with "URL arg must be
        # multibase encoded".)
        return "u" + base64.urlsafe_b64encode(topic.encode()).rstrip(b"=").decode()

    @staticmethod
    def _decode_data(data_field: str) -> bytes:
        # kubo pubsub `data` may be multibase ('u' = base64url) on newer daemons,
        # or bare base64 on older ones. Handle both.
        if data_field[:1] == "u":
            data_field = data_field[1:]
        pad = "=" * (-len(data_field) % 4)
        return base64.urlsafe_b64decode(data_field + pad)

    async def publish(self, topic: str, payload: bytes) -> None:
        client = await self._get_client()
        url = f"{self._api}/api/v0/pubsub/pub"
        params = {"arg": self._encode_topic(topic)}
        files = {"file": ("msg", payload, "application/octet-stream")}
        resp = await client.post(url, params=params, files=files)
        if resp.status_code >= 400:
            raise RuntimeError(f"kubo pubsub pub failed: {resp.status_code} {resp.text}")

    async def subscribe(self, topic: str) -> AsyncIterator[bytes]:
        client = await self._get_client()
        url = f"{self._api}/api/v0/pubsub/sub"
        params = {"arg": self._encode_topic(topic)}

        async def _gen() -> AsyncIterator[bytes]:
            async with client.stream("POST", url, params=params) as resp:
                if resp.status_code >= 400:
                    raise RuntimeError(f"kubo pubsub sub failed: {resp.status_code}")
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    data_field = msg.get("data", "")
                    if not data_field:
                        continue
                    try:
                        yield self._decode_data(data_field)
                    except Exception:
                        continue

        return _gen()

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None


# ---------------------------------------------------------------------------
# High-level wire helpers
# ---------------------------------------------------------------------------

@dataclass
class WireEnvelope:
    """Generic envelope on the wire: kind + JSON body."""

    kind: str  # "prekey" | "init" | "msg"
    body: dict[str, Any]

    def to_bytes(self) -> bytes:
        return json.dumps({"kind": self.kind, "body": self.body}, sort_keys=True).encode()

    @classmethod
    def from_bytes(cls, b: bytes) -> "WireEnvelope":
        d = json.loads(b.decode())
        return cls(kind=d["kind"], body=d["body"])


__all__ = [
    "ChatTransport",
    "MemoryTransport",
    "KuboGossipSubTransport",
    "WireEnvelope",
    "chat_topic_for",
    "prekey_topic_for",
    "get_shared_memory_transport",
    "reset_shared_memory_transport",
]
