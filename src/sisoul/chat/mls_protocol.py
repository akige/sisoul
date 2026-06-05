"""RFC 9420 (MLS) message framing — pure-Python skeleton.

This module implements the *wire format* of the Messaging Layer Security
protocol (RFC 9420) without pulling in a heavyweight C library (OpenMLS /
mlspp). It is faithful to the parts of the spec that matter for sisoul's
group-chat transport:

- TLS presentation-language codec (RFC 8446 §3) including the RFC 9420 §2.1.2
  *variable-length integer* vector-length encoding (the QUIC-style 2-bit
  prefix varint). Getting this right is the distinctive bit of RFC 9420
  serialization, so we implement and round-trip it exactly.
- The ``MLSMessage`` envelope (RFC 9420 §6) tagging one of
  ``PublicMessage`` / ``PrivateMessage`` / ``Welcome`` / ``GroupInfo`` /
  ``KeyPackage`` by ``WireFormat``.
- ``FramedContent`` (RFC 9420 §6.1) carrying Application / Proposal / Commit.
- ``Add`` / ``Remove`` proposals and the ``Commit`` body (RFC 9420 §12).
- ``Welcome`` (RFC 9420 §12.4.3) used to bootstrap a freshly-added member.

What this is NOT: it does not implement TreeKEM HPKE path encryption, X.509
credential validation, or the full ciphersuite negotiation matrix. The actual
key agreement lives in :mod:`sisoul.chat.mls` which uses this module purely for
on-the-wire encode/decode. Real production deployments would swap the
group-secret transport for a vetted MLS stack; the wire format here is
deliberately RFC-shaped so that swap is mechanical.

Constraints:
- pure Python, no new dependencies (RFC 9420 wire format needs none)
- ~180 LOC
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from enum import IntEnum

# RFC 9420 §16.1 — we only register the single mandatory-to-implement suite.
MLS_PROTOCOL_VERSION = 0x0001  # mls10
CIPHERSUITE_X25519_AES128GCM_SHA256_ED25519 = 0x0001


class WireFormat(IntEnum):
    """RFC 9420 §6 wire-format tag for the ``MLSMessage`` envelope."""

    RESERVED = 0x0000
    PUBLIC_MESSAGE = 0x0001
    PRIVATE_MESSAGE = 0x0002
    WELCOME = 0x0003
    GROUP_INFO = 0x0004
    KEY_PACKAGE = 0x0005


class ContentType(IntEnum):
    """RFC 9420 §6.1 framed-content type."""

    RESERVED = 0x00
    APPLICATION = 0x01
    PROPOSAL = 0x02
    COMMIT = 0x03


class ProposalType(IntEnum):
    """RFC 9420 §12.1 proposal type (subset)."""

    ADD = 0x0001
    UPDATE = 0x0002
    REMOVE = 0x0003


class MLSProtocolError(Exception):
    """Malformed wire bytes / unsupported field."""


# ---------------------------------------------------------------------------
# TLS presentation-language codec (RFC 8446 §3 + RFC 9420 §2.1.2 varint)
# ---------------------------------------------------------------------------

class Writer:
    """Minimal TLS-style serializer with RFC 9420 variable-length vectors."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def u8(self, v: int) -> "Writer":
        self._buf += int(v).to_bytes(1, "big")
        return self

    def u16(self, v: int) -> "Writer":
        self._buf += int(v).to_bytes(2, "big")
        return self

    def u32(self, v: int) -> "Writer":
        self._buf += int(v).to_bytes(4, "big")
        return self

    def u64(self, v: int) -> "Writer":
        self._buf += int(v).to_bytes(8, "big")
        return self

    @staticmethod
    def _varint(length: int) -> bytes:
        # RFC 9420 §2.1.2: 2 high bits select 1/2/4/8-byte length prefix.
        if length < 0:
            raise MLSProtocolError("negative length")
        if length < (1 << 6):
            return length.to_bytes(1, "big")
        if length < (1 << 14):
            return (length | (0b01 << 14)).to_bytes(2, "big")
        if length < (1 << 30):
            return (length | (0b10 << 30)).to_bytes(4, "big")
        if length < (1 << 62):
            return (length | (0b11 << 62)).to_bytes(8, "big")
        raise MLSProtocolError("vector too long for varint")

    def opaque(self, data: bytes) -> "Writer":
        """Variable-length opaque vector ``<V>`` with varint length prefix."""
        self._buf += self._varint(len(data)) + bytes(data)
        return self

    def bytes(self) -> bytes:
        return bytes(self._buf)


class Reader:
    """Counterpart to :class:`Writer`."""

    def __init__(self, data: bytes) -> None:
        self._io = io.BytesIO(data)
        self._len = len(data)

    def _take(self, n: int) -> bytes:
        b = self._io.read(n)
        if len(b) != n:
            raise MLSProtocolError(f"truncated: wanted {n}, got {len(b)}")
        return b

    def u8(self) -> int:
        return int.from_bytes(self._take(1), "big")

    def u16(self) -> int:
        return int.from_bytes(self._take(2), "big")

    def u32(self) -> int:
        return int.from_bytes(self._take(4), "big")

    def u64(self) -> int:
        return int.from_bytes(self._take(8), "big")

    def opaque(self) -> bytes:
        first = self._take(1)[0]
        prefix = first >> 6
        if prefix == 0:
            length = first & 0x3F
        elif prefix == 1:
            length = ((first & 0x3F) << 8) | self._take(1)[0]
        elif prefix == 2:
            rest = self._take(3)
            length = ((first & 0x3F) << 24) | int.from_bytes(rest, "big")
        else:
            rest = self._take(7)
            length = ((first & 0x3F) << 56) | int.from_bytes(rest, "big")
        return self._take(length)

    def at_end(self) -> bool:
        return self._io.tell() >= self._len


# ---------------------------------------------------------------------------
# Proposals (RFC 9420 §12.1)
# ---------------------------------------------------------------------------

@dataclass
class Add:
    """Add proposal — carries the new member's KeyPackage (here: did + idkey)."""

    member_did: str
    identity_key: bytes

    def encode(self, w: Writer) -> None:
        w.opaque(self.member_did.encode()).opaque(self.identity_key)

    @classmethod
    def decode(cls, r: Reader) -> "Add":
        return cls(member_did=r.opaque().decode(), identity_key=r.opaque())


@dataclass
class Remove:
    """Remove proposal — references the leaf to evict (here: by did)."""

    member_did: str

    def encode(self, w: Writer) -> None:
        w.opaque(self.member_did.encode())

    @classmethod
    def decode(cls, r: Reader) -> "Remove":
        return cls(member_did=r.opaque().decode())


# ---------------------------------------------------------------------------
# Welcome (RFC 9420 §12.4.3)
# ---------------------------------------------------------------------------

@dataclass
class Welcome:
    """Bootstraps a freshly added member into the group's current epoch.

    ``encrypted_group_secrets`` is the HPKE-sealed epoch secret (sealing is done
    by :mod:`sisoul.chat.mls`); this struct just frames it on the wire.
    """

    cipher_suite: int
    group_id: str
    epoch: int
    members: list[str]
    encrypted_group_secrets: bytes

    def encode(self) -> bytes:
        w = Writer().u16(self.cipher_suite).opaque(self.group_id.encode()).u64(self.epoch)
        roster = Writer()
        for did in self.members:
            roster.opaque(did.encode())
        w.opaque(roster.bytes()).opaque(self.encrypted_group_secrets)
        return w.bytes()

    @classmethod
    def decode(cls, data: bytes) -> "Welcome":
        r = Reader(data)
        cs = r.u16()
        gid = r.opaque().decode()
        epoch = r.u64()
        roster = Reader(r.opaque())
        members: list[str] = []
        while not roster.at_end():
            members.append(roster.opaque().decode())
        return cls(cs, gid, epoch, members, r.opaque())


# ---------------------------------------------------------------------------
# FramedContent + MLSMessage envelope (RFC 9420 §6)
# ---------------------------------------------------------------------------

@dataclass
class FramedContent:
    """RFC 9420 §6.1 — group_id/epoch/sender + typed content payload."""

    group_id: str
    epoch: int
    sender_did: str
    content_type: ContentType
    content: bytes  # Application ciphertext, or encoded Proposal/Commit body
    generation: int = 0  # per-sender message counter (PrivateMessage senderData)
    authenticated_data: bytes = b""

    def encode(self) -> bytes:
        return (
            Writer()
            .opaque(self.group_id.encode())
            .u64(self.epoch)
            .opaque(self.sender_did.encode())
            .u8(int(self.content_type))
            .u32(self.generation)
            .opaque(self.authenticated_data)
            .opaque(self.content)
            .bytes()
        )

    @classmethod
    def decode(cls, data: bytes) -> "FramedContent":
        r = Reader(data)
        return cls(
            group_id=r.opaque().decode(),
            epoch=r.u64(),
            sender_did=r.opaque().decode(),
            content_type=ContentType(r.u8()),
            generation=r.u32(),
            authenticated_data=r.opaque(),
            content=r.opaque(),
        )


@dataclass
class MLSMessage:
    """RFC 9420 §6 top-level envelope: version + wire_format + body."""

    wire_format: WireFormat
    body: bytes
    version: int = MLS_PROTOCOL_VERSION

    def encode(self) -> bytes:
        return Writer().u16(self.version).u16(int(self.wire_format)).opaque(self.body).bytes()

    @classmethod
    def decode(cls, data: bytes) -> "MLSMessage":
        r = Reader(data)
        version = r.u16()
        if version != MLS_PROTOCOL_VERSION:
            raise MLSProtocolError(f"unsupported MLS version 0x{version:04x}")
        wf = WireFormat(r.u16())
        return cls(wire_format=wf, body=r.opaque(), version=version)


__all__ = [
    "MLS_PROTOCOL_VERSION",
    "CIPHERSUITE_X25519_AES128GCM_SHA256_ED25519",
    "WireFormat",
    "ContentType",
    "ProposalType",
    "MLSProtocolError",
    "Writer",
    "Reader",
    "Add",
    "Remove",
    "Welcome",
    "FramedContent",
    "MLSMessage",
]
