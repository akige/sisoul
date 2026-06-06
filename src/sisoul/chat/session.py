"""High-level chat manager: handshake + ratchet + transport + persistence.

Combines:
- PQXDH (:mod:`sisoul.chat.pqxdh`) for post-quantum hybrid key exchange
- Double Ratchet (:mod:`sisoul.chat.double_ratchet`) for per-message FS
- ChatTransport (:mod:`sisoul.chat.transport`) for delivery over GossipSub

Session state persisted to ``~/.sisoul/chat/sessions/<peer-did>.json``,
encrypted at rest with libsodium SecretBox keyed by a per-peer KDF on the
local identity master key (passed in by caller).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import nacl.bindings as nb
from nacl.secret import SecretBox
from nacl.utils import random as nacl_random

from sisoul.chat.double_ratchet import ChatSession, CipherMessage
from sisoul.chat.pqxdh import (
    LocalKeyMaterial,
    PreKeyBundle,
    complete_handshake_initiator,
    complete_handshake_responder,
    generate_pre_key_bundle,
)
from sisoul.chat.transport import (
    ChatTransport,
    WireEnvelope,
    chat_topic_for,
    prekey_topic_for,
)


CHAT_DIR_ENV = "SISOUL_CHAT_DIR"
DEFAULT_CHAT_DIR = Path.home() / ".sisoul" / "chat"


def chat_dir() -> Path:
    d = Path(os.environ.get(CHAT_DIR_ENV) or DEFAULT_CHAT_DIR)
    d.mkdir(parents=True, exist_ok=True)
    (d / "sessions").mkdir(exist_ok=True)
    (d / "prekeys").mkdir(exist_ok=True)
    (d / "log").mkdir(exist_ok=True)
    return d


def _peer_storage_key(master_key: bytes, peer_did: str) -> bytes:
    return hashlib.sha256(b"sisoul-chat-storage-v1\x00" + master_key + peer_did.encode()).digest()


def _encrypt_at_rest(master_key: bytes, peer_did: str, plaintext: bytes) -> bytes:
    key = _peer_storage_key(master_key, peer_did)
    box = SecretBox(key)
    return bytes(box.encrypt(plaintext, nacl_random(SecretBox.NONCE_SIZE)))


def _decrypt_at_rest(master_key: bytes, peer_did: str, blob: bytes) -> bytes:
    key = _peer_storage_key(master_key, peer_did)
    return SecretBox(key).decrypt(blob)


# ---------------------------------------------------------------------------
# Local key-material persistence (so daemon-announce, recv, and send all share
# the SAME identity keys across processes — required for GossipSub prekey flow).
# ---------------------------------------------------------------------------

def _local_keys_path(local_did: str) -> Path:
    return chat_dir() / "keys" / f"{_safe_did(local_did)}.enc"


def save_local_keys(keys: LocalKeyMaterial, master_key: bytes) -> None:
    """Persist local key material, encrypted at rest with master_key (chmod 600)."""
    path = _local_keys_path(keys.did)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = _encrypt_at_rest(
        bytes(master_key[:32]), "__local_keys__", json.dumps(keys.to_secret_dict()).encode()
    )
    path.write_bytes(blob)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_local_keys(local_did: str, master_key: bytes) -> LocalKeyMaterial | None:
    """Load persisted key material for ``local_did`` (None if absent / corrupt / mismatch)."""
    path = _local_keys_path(local_did)
    if not path.exists():
        return None
    try:
        raw = _decrypt_at_rest(bytes(master_key[:32]), "__local_keys__", path.read_bytes())
        km = LocalKeyMaterial.from_secret_dict(json.loads(raw.decode()))
    except Exception:  # noqa: BLE001  (corrupt / wrong key / schema change -> regenerate)
        return None
    return km if km.did == local_did else None


@dataclass
class StoredSession:
    """Persisted chat session for a single peer."""

    peer_did: str
    session: ChatSession
    peer_bundle: PreKeyBundle | None = None
    initiated: bool = False

    def to_bytes(self) -> bytes:
        d = {
            "peer_did": self.peer_did,
            "session": self.session.serialize(),
            "peer_bundle": self.peer_bundle.to_dict() if self.peer_bundle else None,
            "initiated": self.initiated,
        }
        return json.dumps(d).encode()

    @classmethod
    def from_bytes(cls, b: bytes) -> "StoredSession":
        d = json.loads(b.decode())
        return cls(
            peer_did=d["peer_did"],
            session=ChatSession.deserialize(d["session"]),
            peer_bundle=PreKeyBundle.from_dict(d["peer_bundle"]) if d["peer_bundle"] else None,
            initiated=d.get("initiated", False),
        )


class ChatManager:
    """Per-DID chat manager."""

    def __init__(
        self,
        local_did: str,
        master_key: bytes,
        transport: ChatTransport,
        keys: LocalKeyMaterial | None = None,
    ) -> None:
        if len(master_key) < 32:
            raise ValueError("master_key must be >= 32 bytes")
        self.local_did = local_did
        self._master_key = bytes(master_key[:32])
        self.transport = transport
        self.keys: LocalKeyMaterial = keys or generate_pre_key_bundle(local_did)
        self._sessions: dict[str, StoredSession] = {}

    # ------------------------------------------------------------------
    # Pre-key bundle distribution
    # ------------------------------------------------------------------

    async def announce_prekey(self) -> str:
        """Publishes current pre-key bundle to our pre-key GossipSub topic."""
        topic = prekey_topic_for(self.local_did)
        env = WireEnvelope(kind="prekey", body=self.keys.bundle.to_dict()).to_bytes()
        await self.transport.publish(topic, env)
        return topic

    def cache_peer_prekey(self, bundle: PreKeyBundle) -> None:
        path = chat_dir() / "prekeys" / f"{_safe_did(bundle.did)}.json"
        path.write_text(bundle.to_json())

    def load_peer_prekey(self, peer_did: str) -> PreKeyBundle | None:
        path = chat_dir() / "prekeys" / f"{_safe_did(peer_did)}.json"
        if not path.exists():
            return None
        return PreKeyBundle.from_json(path.read_text())

    def rotate_prekey(self) -> PreKeyBundle:
        """Regenerates local pre-key bundle (keeps DID)."""
        self.keys = generate_pre_key_bundle(self.local_did)
        return self.keys.bundle

    # ------------------------------------------------------------------
    # Handshake
    # ------------------------------------------------------------------

    async def open_session(self, peer_did: str, peer_bundle: PreKeyBundle | None = None) -> StoredSession:
        """Opens a new outbound session to ``peer_did`` (or returns cached)."""
        if peer_did in self._sessions:
            return self._sessions[peer_did]
        bundle = peer_bundle or self.load_peer_prekey(peer_did)
        if bundle is None:
            raise RuntimeError(f"no pre-key bundle for {peer_did}; call announce / cache first")

        shared, ek_pub, ct = complete_handshake_initiator(self.keys, bundle)
        # Pick our first Double Ratchet sending key derived from peer signed pre-key
        # (peer can decrypt with their signed pre-key private which they hold)
        session, init_msg = ChatSession.init_outbound(
            shared_secret=shared,
            peer_ratchet_pub=bundle.signed_pre_key_pub,
            associated_data=_pair_ad(self.local_did, peer_did),
            first_message=b"\x00sisoul-chat-init",
        )
        stored = StoredSession(peer_did=peer_did, session=session, peer_bundle=bundle, initiated=True)
        self._sessions[peer_did] = stored

        # Publish bootstrap envelope so peer can answer
        topic = chat_topic_for(self.local_did, peer_did)
        env = WireEnvelope(
            kind="init",
            body={
                "from_did": self.local_did,
                "ek_pub": ek_pub.hex(),
                "ik_pub": self.keys.x25519_identity_pub.hex(),
                "mlkem_ct": ct.hex(),
                "ratchet_msg": init_msg.to_dict(),
            },
        ).to_bytes()
        await self.transport.publish(topic, env)

        return stored

    async def accept_init(self, env: WireEnvelope) -> StoredSession:
        """Responder side: process an "init" envelope from a peer."""
        body = env.body
        peer_did = body["from_did"]
        ek_pub = bytes.fromhex(body["ek_pub"])
        ik_pub = bytes.fromhex(body["ik_pub"])
        ct = bytes.fromhex(body["mlkem_ct"])
        init_msg = CipherMessage.from_dict(body["ratchet_msg"])

        shared = complete_handshake_responder(
            self.keys, peer_did, ik_pub, ek_pub, ct
        )
        session, first_pt = ChatSession.init_inbound(
            shared_secret=shared,
            own_ratchet_priv=self.keys.x25519_spk_priv,
            initial_message=init_msg,
            associated_data=_pair_ad(peer_did, self.local_did),
        )
        stored = StoredSession(peer_did=peer_did, session=session, initiated=False)
        self._sessions[peer_did] = stored
        # first_pt is the bootstrap marker; ignore content
        return stored

    # ------------------------------------------------------------------
    # Send / recv
    # ------------------------------------------------------------------

    async def send(self, peer_did: str, plaintext: str | bytes) -> None:
        if peer_did not in self._sessions:
            await self.open_session(peer_did)
        stored = self._sessions[peer_did]
        if isinstance(plaintext, str):
            plaintext = plaintext.encode()
        msg = stored.session.encrypt(plaintext)
        env = WireEnvelope(
            kind="msg",
            body={"from_did": self.local_did, "ct": msg.to_dict()},
        ).to_bytes()
        topic = chat_topic_for(self.local_did, peer_did)
        await self.transport.publish(topic, env)

    async def handle_incoming(self, env: WireEnvelope) -> tuple[str, bytes] | None:
        """Process a wire envelope from chat topic. Returns (peer_did, plaintext) or None."""
        if env.kind == "init":
            await self.accept_init(env)
            return None
        if env.kind == "msg":
            peer_did = env.body["from_did"]
            if peer_did == self.local_did:
                return None  # echo of our own send
            stored = self._sessions.get(peer_did)
            if stored is None:
                # We haven't seen the init yet — caller must ensure ordering.
                return None
            msg = CipherMessage.from_dict(env.body["ct"])
            pt = stored.session.decrypt(msg)
            return peer_did, pt
        return None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def persist(self) -> None:
        sessions_dir = chat_dir() / "sessions"
        for peer_did, stored in self._sessions.items():
            blob = _encrypt_at_rest(self._master_key, peer_did, stored.to_bytes())
            (sessions_dir / f"{_safe_did(peer_did)}.bin").write_bytes(blob)

    def load(self, peer_did: str) -> StoredSession | None:
        path = chat_dir() / "sessions" / f"{_safe_did(peer_did)}.bin"
        if not path.exists():
            return None
        try:
            blob = path.read_bytes()
            plain = _decrypt_at_rest(self._master_key, peer_did, blob)
            stored = StoredSession.from_bytes(plain)
            self._sessions[peer_did] = stored
            return stored
        except Exception:
            return None

    def list_sessions(self) -> list[dict[str, Any]]:
        out = []
        for peer_did, stored in self._sessions.items():
            out.append({
                "peer_did": peer_did,
                "sending_chain_length": stored.session.sending_chain_length,
                "receiving_chain_length": stored.session.receiving_chain_length,
                "initiated": stored.initiated,
            })
        return out


def _safe_did(did: str) -> str:
    return hashlib.sha256(did.encode()).hexdigest()[:32]


def _pair_ad(initiator_did: str, responder_did: str) -> bytes:
    """Associated data binding the DID pair into the AEAD."""
    return f"{initiator_did}|{responder_did}".encode()


__all__ = [
    "ChatManager",
    "StoredSession",
    "chat_dir",
]
