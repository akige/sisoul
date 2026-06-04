"""Signal-style Double Ratchet on top of upstream ``python-doubleratchet`` (1.3.x).

Provides a simple synchronous ``ChatSession`` API on top of the library's
``recommended`` Curve25519 / HKDF-SHA512 / AES-GCM-HMAC building blocks.

Forward secrecy: each call to ``encrypt`` advances the sending chain;
receiving any newer DH ratchet key from the peer rotates the root chain.
Old chain/message keys are zeroized by the underlying library.

Persistence: ``session.serialize()`` returns a JSON-compatible dict.
``ChatSession.deserialize(...)`` restores. Combined with libsodium SecretBox
wrapper (caller's responsibility) this gives encrypted session storage.

Constraints (P2-G):
- < 400 LOC (this file is ~330 LOC)
- API: init_outbound / init_inbound / encrypt / decrypt
- All synchronous (wraps library coroutines via internal event loop helper)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from doubleratchet import DoubleRatchet, EncryptedMessage, Header
from doubleratchet.recommended import (
    aead_aes_hmac,
    diffie_hellman_ratchet_curve25519,
    kdf_hkdf,
)
from doubleratchet.recommended.aead_aes_hmac import HashFunction as AEADHash
from doubleratchet.recommended.kdf_hkdf import HashFunction as KDFHash


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DoubleRatchetError(Exception):
    """Wraps any underlying Double-Ratchet failure (decrypt / auth / DoS)."""


# ---------------------------------------------------------------------------
# Concrete KDF / AEAD subclasses (recommended config)
# ---------------------------------------------------------------------------

class _RootChainKDF(kdf_hkdf.KDF):
    @staticmethod
    def _get_hash_function() -> KDFHash:
        return KDFHash.SHA_512

    @staticmethod
    def _get_info() -> bytes:
        return b"sisoul-chat-root-chain-v1"


class _MessageChainKDF(kdf_hkdf.KDF):
    @staticmethod
    def _get_hash_function() -> KDFHash:
        return KDFHash.SHA_512

    @staticmethod
    def _get_info() -> bytes:
        return b"sisoul-chat-message-chain-v1"


class _AEAD(aead_aes_hmac.AEAD):
    @staticmethod
    def _get_hash_function() -> AEADHash:
        return AEADHash.SHA_512

    @staticmethod
    def _get_info() -> bytes:
        return b"sisoul-chat-aead-v1"


class _ChatDoubleRatchet(DoubleRatchet):
    """Concrete DoubleRatchet wiring Header into the AEAD associated data."""

    @staticmethod
    def _build_associated_data(associated_data: bytes, header: Header) -> bytes:
        # Bind header (ratchet pub + counters) to AEAD AAD so tampering is detected.
        return (
            associated_data
            + header.ratchet_pub
            + header.previous_sending_chain_length.to_bytes(4, "big")
            + header.sending_chain_length.to_bytes(4, "big")
        )


_DH = diffie_hellman_ratchet_curve25519.DiffieHellmanRatchet

_RATCHET_CONFIG: dict[str, Any] = {
    "diffie_hellman_ratchet_class": _DH,
    "root_chain_kdf": _RootChainKDF,
    "message_chain_kdf": _MessageChainKDF,
    "message_chain_constant": b"\x01\x02",
    "dos_protection_threshold": 100,
    "max_num_skipped_message_keys": 1000,
    "aead": _AEAD,
}


# ---------------------------------------------------------------------------
# asyncio helper
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine to completion from sync code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # Inside running loop — use a fresh loop in a thread.
        import threading
        result: list[Any] = [None]
        error: list[BaseException | None] = [None]

        def runner() -> None:
            new_loop = asyncio.new_event_loop()
            try:
                result[0] = new_loop.run_until_complete(coro)
            except BaseException as exc:  # noqa: BLE001
                error[0] = exc
            finally:
                new_loop.close()

        t = threading.Thread(target=runner)
        t.start()
        t.join()
        if error[0] is not None:
            raise error[0]
        return result[0]
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Encrypted message wire format
# ---------------------------------------------------------------------------

@dataclass
class CipherMessage:
    """Wire format for an encrypted chat message (header + body)."""

    header_ratchet_pub: bytes
    previous_sending_chain_length: int
    sending_chain_length: int
    ciphertext: bytes

    def to_dict(self) -> dict[str, Any]:
        return {
            "ratchet_pub": self.header_ratchet_pub.hex(),
            "prev_n": self.previous_sending_chain_length,
            "n": self.sending_chain_length,
            "ct": self.ciphertext.hex(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CipherMessage":
        return cls(
            header_ratchet_pub=bytes.fromhex(d["ratchet_pub"]),
            previous_sending_chain_length=int(d["prev_n"]),
            sending_chain_length=int(d["n"]),
            ciphertext=bytes.fromhex(d["ct"]),
        )

    @classmethod
    def from_json(cls, s: str) -> "CipherMessage":
        return cls.from_dict(json.loads(s))

    def to_encrypted_message(self) -> EncryptedMessage:
        return EncryptedMessage(
            header=Header(
                ratchet_pub=self.header_ratchet_pub,
                previous_sending_chain_length=self.previous_sending_chain_length,
                sending_chain_length=self.sending_chain_length,
            ),
            ciphertext=self.ciphertext,
        )

    @classmethod
    def from_encrypted_message(cls, m: EncryptedMessage) -> "CipherMessage":
        return cls(
            header_ratchet_pub=m.header.ratchet_pub,
            previous_sending_chain_length=m.header.previous_sending_chain_length,
            sending_chain_length=m.header.sending_chain_length,
            ciphertext=m.ciphertext,
        )


# ---------------------------------------------------------------------------
# ChatSession
# ---------------------------------------------------------------------------

class ChatSession:
    """Signal-grade Double Ratchet session between two peers."""

    def __init__(self, dr: _ChatDoubleRatchet, associated_data: bytes) -> None:
        self._dr = dr
        self._ad = associated_data

    # --- Construction --------------------------------------------------------

    @classmethod
    def init_outbound(
        cls,
        shared_secret: bytes,
        peer_ratchet_pub: bytes,
        associated_data: bytes = b"",
        first_message: bytes = b"\x00",
    ) -> tuple["ChatSession", CipherMessage]:
        """Initiator side. Returns (session, first_encrypted_message).

        The first encrypted message must be sent to peer to bootstrap.
        """
        if len(shared_secret) != 32:
            raise ValueError("shared_secret must be 32 bytes (HKDF from PQXDH output)")
        if len(peer_ratchet_pub) != 32:
            raise ValueError("peer_ratchet_pub must be 32 bytes (Curve25519)")

        async def _do() -> tuple[_ChatDoubleRatchet, EncryptedMessage]:
            return await _ChatDoubleRatchet.encrypt_initial_message(
                shared_secret=shared_secret,
                recipient_ratchet_pub=peer_ratchet_pub,
                message=first_message,
                associated_data=associated_data,
                **_RATCHET_CONFIG,
            )

        try:
            dr, msg = _run(_do())
        except Exception as exc:
            raise DoubleRatchetError(f"init_outbound failed: {exc}") from exc
        return cls(dr, associated_data), CipherMessage.from_encrypted_message(msg)

    @classmethod
    def init_inbound(
        cls,
        shared_secret: bytes,
        own_ratchet_priv: bytes,
        initial_message: CipherMessage,
        associated_data: bytes = b"",
    ) -> tuple["ChatSession", bytes]:
        """Responder side. Returns (session, decrypted_first_plaintext)."""
        if len(shared_secret) != 32:
            raise ValueError("shared_secret must be 32 bytes")
        if len(own_ratchet_priv) != 32:
            raise ValueError("own_ratchet_priv must be 32 bytes (Curve25519)")

        async def _do() -> tuple[_ChatDoubleRatchet, bytes]:
            return await _ChatDoubleRatchet.decrypt_initial_message(
                shared_secret=shared_secret,
                own_ratchet_priv=own_ratchet_priv,
                message=initial_message.to_encrypted_message(),
                associated_data=associated_data,
                **_RATCHET_CONFIG,
            )

        try:
            dr, pt = _run(_do())
        except Exception as exc:
            raise DoubleRatchetError(f"init_inbound failed: {exc}") from exc
        return cls(dr, associated_data), pt

    # --- Steady state --------------------------------------------------------

    def encrypt(self, plaintext: bytes) -> CipherMessage:
        if not isinstance(plaintext, (bytes, bytearray)):
            raise TypeError("plaintext must be bytes")

        async def _do() -> EncryptedMessage:
            return await self._dr.encrypt_message(bytes(plaintext), self._ad)

        try:
            msg = _run(_do())
        except Exception as exc:
            raise DoubleRatchetError(f"encrypt failed: {exc}") from exc
        return CipherMessage.from_encrypted_message(msg)

    def decrypt(self, message: CipherMessage) -> bytes:
        async def _do() -> bytes:
            return await self._dr.decrypt_message(message.to_encrypted_message(), self._ad)

        try:
            return _run(_do())
        except Exception as exc:
            raise DoubleRatchetError(f"decrypt failed: {exc}") from exc

    # --- Introspection / persistence ----------------------------------------

    @property
    def sending_chain_length(self) -> int:
        return self._dr.sending_chain_length

    @property
    def receiving_chain_length(self) -> int:
        return self._dr.receiving_chain_length

    def serialize(self) -> dict[str, Any]:
        """Returns a JSON-compatible dict for persistence."""
        return {
            "dr": self._dr.json,
            "ad": self._ad.hex(),
        }

    @classmethod
    def deserialize(cls, blob: dict[str, Any]) -> "ChatSession":
        try:
            dr = _ChatDoubleRatchet.from_json(blob["dr"], **_RATCHET_CONFIG)
        except Exception as exc:
            raise DoubleRatchetError(f"deserialize failed: {exc}") from exc
        return cls(dr, bytes.fromhex(blob["ad"]))


__all__ = [
    "ChatSession",
    "CipherMessage",
    "DoubleRatchetError",
]
