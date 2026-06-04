"""sisoul chat (P2-G · Signal-grade).

Double Ratchet (forward secrecy) + PQXDH (post-quantum hybrid handshake)
running over the existing Kubo (GossipSub) transport.

Public API entry points:
- :class:`sisoul.chat.double_ratchet.ChatSession`
- :func:`sisoul.chat.pqxdh.generate_pre_key_bundle`
- :func:`sisoul.chat.pqxdh.complete_handshake_initiator`
- :func:`sisoul.chat.pqxdh.complete_handshake_responder`
- :class:`sisoul.chat.transport.ChatTransport`
"""

from sisoul.chat.double_ratchet import ChatSession, CipherMessage, DoubleRatchetError  # noqa: F401
from sisoul.chat.pqxdh import (  # noqa: F401
    PQXDHError,
    PreKeyBundle,
    complete_handshake_initiator,
    complete_handshake_responder,
    generate_pre_key_bundle,
    pqxdh_mode,
)
from sisoul.chat.session import ChatManager, StoredSession, chat_dir  # noqa: F401
from sisoul.chat.transport import (  # noqa: F401
    ChatTransport,
    KuboGossipSubTransport,
    MemoryTransport,
    WireEnvelope,
    chat_topic_for,
    get_shared_memory_transport,
    prekey_topic_for,
    reset_shared_memory_transport,
)

__all__ = [
    "ChatManager",
    "ChatSession",
    "ChatTransport",
    "CipherMessage",
    "DoubleRatchetError",
    "KuboGossipSubTransport",
    "MemoryTransport",
    "PQXDHError",
    "PreKeyBundle",
    "StoredSession",
    "WireEnvelope",
    "chat_dir",
    "chat_topic_for",
    "complete_handshake_initiator",
    "complete_handshake_responder",
    "generate_pre_key_bundle",
    "get_shared_memory_transport",
    "pqxdh_mode",
    "prekey_topic_for",
    "reset_shared_memory_transport",
]
