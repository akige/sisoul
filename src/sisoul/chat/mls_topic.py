"""GossipSub integration for MLS group chat.

Each MLS group maps to one pubsub topic derived from its ``group_id``::

    /sisoul/mls/v1/<group_id_hash[:16]>

This mirrors the 1:1 chat topic scheme in :mod:`sisoul.chat.transport` (which
hashes the DID pair) but keys on the opaque group id instead. The same
:class:`~sisoul.chat.transport.ChatTransport` implementations (Kubo GossipSub
or the in-memory test bus) carry the opaque MLS ``MLSMessage`` bytes — the
transport never sees plaintext or group secrets.

Handshake messages (Commit / Welcome) and application messages all ride the
same topic; receivers dispatch on the decoded :class:`WireFormat`.
"""

from __future__ import annotations

import hashlib

from sisoul.chat.mls_protocol import MLSMessage, WireFormat
from sisoul.chat.transport import ChatTransport

MLS_TOPIC_PREFIX = "/sisoul/mls/v1/"


def mls_topic_for(group_id: str) -> str:
    """Returns the deterministic GossipSub topic for an MLS group."""
    h = hashlib.sha256(group_id.encode()).hexdigest()[:16]
    return f"{MLS_TOPIC_PREFIX}{h}"


class MLSTopic:
    """Thin publish/subscribe wrapper binding an MLS group to its pubsub topic."""

    def __init__(self, group_id: str, transport: ChatTransport) -> None:
        self.group_id = group_id
        self.topic = mls_topic_for(group_id)
        self._transport = transport

    async def publish(self, message: bytes) -> None:
        """Publish an encoded ``MLSMessage`` (application or handshake) to the group."""
        await self._transport.publish(self.topic, message)

    async def messages(self):
        """Async-iterate raw ``MLSMessage`` byte payloads off the group topic."""
        gen = await self._transport.subscribe(self.topic)
        async for payload in gen:
            yield payload

    @staticmethod
    def classify(payload: bytes) -> WireFormat:
        """Peek the wire-format tag without fully processing the message."""
        return MLSMessage.decode(payload).wire_format

    async def close(self) -> None:
        await self._transport.close()


__all__ = ["MLS_TOPIC_PREFIX", "mls_topic_for", "MLSTopic"]
