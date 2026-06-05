"""MLS group chat (RFC 9420) — pure-Python key-agreement skeleton.

:class:`MLSGroup` provides asynchronous group key agreement for chats with more
than two participants, complementing the 1:1 Double Ratchet in
:mod:`sisoul.chat.double_ratchet`. It implements the *semantics* of RFC 9420
that matter for sisoul:

- **Epoch ratchet** — every membership change (add / remove) re-keys the group
  to a fresh independent ``epoch_secret`` and bumps the epoch counter. Because
  epoch secrets are independent (not chained), compromise of one epoch's keys
  reveals neither earlier nor later epochs (post-compromise security; a stronger
  variant of RFC 9420 §2.10).
- **Forward secrecy on join** — a member added at epoch *N* receives only the
  epoch-*N* secret via its Welcome; messages from epochs ``< N`` are
  undecryptable to it.
- **Forward secrecy on removal** — a removed member is excluded from the
  re-key recipient set, so it cannot derive any epoch ``> N`` secret even though
  it still holds epoch *N*.
- **Group AEAD** — application messages are sealed with AES-128-GCM under a key
  derived from the epoch's ``encryption_secret`` plus the sender's leaf index and
  a per-sender generation counter (RFC 9420 §9 sender ratchet, simplified).

Group secrets are distributed with an HPKE-style seal (X25519 + HKDF-SHA256 +
AES-128-GCM) to each recipient's identity key — mirroring RFC 9420's Welcome /
TreeKEM path-secret encryption. NOTE: in this skeleton, identity keypairs are
*derived deterministically from the DID string* so tests need no key registry.
That is intentionally insecure (anyone holding the DID can derive its private
key) and stands in for RFC 9420 KeyPackages backed by real per-device keys; the
wire shape is faithful so the swap to a vetted MLS stack is mechanical.

Wire framing lives in :mod:`sisoul.chat.mls_protocol`.
"""

from __future__ import annotations

import json
import os
from typing import Any

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF, HKDFExpand

from sisoul.chat.mls_protocol import (
    CIPHERSUITE_X25519_AES128GCM_SHA256_ED25519 as _SUITE,
    Add,
    ContentType,
    FramedContent,
    MLSMessage,
    MLSProtocolError,
    Reader,
    Remove,
    Welcome,
    WireFormat,
    Writer,
)

_SECRET_LEN = 32
_AEAD_KEY_LEN = 16  # AES-128-GCM per ciphersuite
_NONCE_LEN = 12


class MLSGroupError(Exception):
    """Group-level failure: bad epoch, non-member sender, replay, auth fail."""


# ---------------------------------------------------------------------------
# Key-schedule + HPKE-style helpers
# ---------------------------------------------------------------------------

def _expand_with_label(secret: bytes, label: bytes, context: bytes, length: int) -> bytes:
    """RFC 9420 §8 ``ExpandWithLabel`` over HKDF-SHA256."""
    info = (
        Writer().u16(length).opaque(b"MLS 1.0 " + label).opaque(context).bytes()
    )
    return HKDFExpand(algorithm=SHA256(), length=length, info=info).derive(secret)


def _identity_priv(did: str) -> X25519PrivateKey:
    """Deterministic skeleton identity key (see module docstring caveat)."""
    seed = _expand_with_label(b"\x00" * 32, b"sisoul-mls-skel-id", did.encode(), 32)
    return X25519PrivateKey.from_private_bytes(seed)


def _identity_pub_bytes(did: str) -> bytes:
    return _identity_priv(did).public_key().public_bytes_raw()


def _hpke_seal(recipient_did: str, plaintext: bytes, aad: bytes) -> bytes:
    """Seal ``plaintext`` to a member's identity key (X25519 + HKDF + AESGCM)."""
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes_raw()
    shared = eph.exchange(X25519PublicKey.from_public_bytes(_identity_pub_bytes(recipient_did)))
    key = HKDF(algorithm=SHA256(), length=_AEAD_KEY_LEN, salt=None,
               info=b"sisoul-mls-hpke" + eph_pub).derive(shared)
    ct = AESGCM(key).encrypt(b"\x00" * _NONCE_LEN, plaintext, aad)
    return eph_pub + ct


def _hpke_open(recipient_did: str, blob: bytes, aad: bytes) -> bytes:
    eph_pub, ct = blob[:32], blob[32:]
    shared = _identity_priv(recipient_did).exchange(X25519PublicKey.from_public_bytes(eph_pub))
    key = HKDF(algorithm=SHA256(), length=_AEAD_KEY_LEN, salt=None,
               info=b"sisoul-mls-hpke" + eph_pub).derive(shared)
    return AESGCM(key).decrypt(b"\x00" * _NONCE_LEN, ct, aad)


def _encode_sealed_map(sealed: dict[str, bytes]) -> bytes:
    w = Writer()
    for did, blob in sealed.items():
        w.opaque(did.encode()).opaque(blob)
    return w.bytes()


def _decode_sealed_map(data: bytes) -> dict[str, bytes]:
    r = Reader(data)
    out: dict[str, bytes] = {}
    while not r.at_end():
        # NB: read did then blob in explicit order — Python evaluates the RHS of
        # ``out[k] = v`` before the subscript key, which would reverse the fields.
        did = r.opaque().decode()
        out[did] = r.opaque()
    return out


# ---------------------------------------------------------------------------
# MLSGroup
# ---------------------------------------------------------------------------

class MLSGroup:
    """A single member's view of an MLS group (RFC 9420 skeleton)."""

    def __init__(self, group_id: str, members: list[str], my_did: str | None = None) -> None:
        if not members:
            raise MLSGroupError("group needs at least one member")
        # Preserve order, drop duplicates.
        seen: set[str] = set()
        self.members: list[str] = [m for m in members if not (m in seen or seen.add(m))]
        self.group_id = group_id
        self.my_did = my_did if my_did is not None else self.members[0]
        if self.my_did not in self.members:
            raise MLSGroupError(f"my_did {self.my_did!r} not in members")
        self.epoch = 0
        self._epoch_secret = os.urandom(_SECRET_LEN)
        self._send_gen = 0
        self._active = True
        self._seen: set[tuple[int, str, int]] = set()  # (epoch, sender, generation)

    # --- key schedule -------------------------------------------------------

    @property
    def _encryption_secret(self) -> bytes:
        return _expand_with_label(self._epoch_secret, b"encryption", b"", _SECRET_LEN)

    def _message_keys(self, leaf_index: int, generation: int) -> tuple[bytes, bytes]:
        ctx = leaf_index.to_bytes(4, "big") + generation.to_bytes(4, "big")
        es = self._encryption_secret
        return (
            _expand_with_label(es, b"key", ctx, _AEAD_KEY_LEN),
            _expand_with_label(es, b"nonce", ctx, _NONCE_LEN),
        )

    def _app_aad(self, sender_did: str, generation: int) -> bytes:
        return (
            Writer().opaque(self.group_id.encode()).u64(self.epoch)
            .opaque(sender_did.encode()).u32(generation).bytes()
        )

    # --- application messages ----------------------------------------------

    def encrypt(self, plaintext: bytes) -> bytes:
        """Group-AEAD encrypt ``plaintext`` → encoded ``MLSMessage`` bytes."""
        if not self._active:
            raise MLSGroupError("inactive member (removed) cannot send")
        if not isinstance(plaintext, (bytes, bytearray)):
            raise TypeError("plaintext must be bytes")
        leaf = self.members.index(self.my_did)
        gen = self._send_gen
        self._send_gen += 1
        key, nonce = self._message_keys(leaf, gen)
        aad = self._app_aad(self.my_did, gen)
        ct = AESGCM(key).encrypt(nonce, bytes(plaintext), aad)
        fc = FramedContent(
            group_id=self.group_id, epoch=self.epoch, sender_did=self.my_did,
            content_type=ContentType.APPLICATION, content=ct, generation=gen,
        )
        return MLSMessage(WireFormat.PRIVATE_MESSAGE, fc.encode()).encode()

    def decrypt(self, ciphertext: bytes, sender_did: str) -> bytes:
        """Decrypt an application ``MLSMessage``. Raises on epoch/sender/replay."""
        try:
            msg = MLSMessage.decode(bytes(ciphertext))
            if msg.wire_format != WireFormat.PRIVATE_MESSAGE:
                raise MLSGroupError(f"not an application message: {msg.wire_format}")
            fc = FramedContent.decode(msg.body)
        except (MLSProtocolError, ValueError) as exc:
            raise MLSGroupError(f"malformed message: {exc}") from exc

        if fc.content_type != ContentType.APPLICATION:
            raise MLSGroupError("not application content")
        if fc.group_id != self.group_id:
            raise MLSGroupError("group_id mismatch")
        if fc.sender_did != sender_did:
            raise MLSGroupError("sender_did mismatch (header vs claimed)")
        if fc.epoch != self.epoch:
            # Cross-epoch: forward secrecy — we no longer hold that epoch's key.
            raise MLSGroupError(f"epoch mismatch: msg={fc.epoch} group={self.epoch}")
        if sender_did not in self.members:
            raise MLSGroupError(f"sender {sender_did!r} not a group member")
        rkey = (fc.epoch, sender_did, fc.generation)
        if rkey in self._seen:
            raise MLSGroupError("replay detected")

        leaf = self.members.index(sender_did)
        key, nonce = self._message_keys(leaf, fc.generation)
        aad = self._app_aad(sender_did, fc.generation)
        try:
            pt = AESGCM(key).decrypt(nonce, fc.content, aad)
        except Exception as exc:  # InvalidTag etc.
            raise MLSGroupError(f"AEAD auth failed: {exc}") from exc
        self._seen.add(rkey)
        return pt

    # --- membership (commits) ----------------------------------------------

    def _rekey_commit(self, proposal_type: ContentType, proposal_body: bytes,
                      new_members: list[str], recipients: list[str]) -> bytes:
        """Generate a fresh epoch, seal it to ``recipients``, build a Commit msg."""
        new_secret = os.urandom(_SECRET_LEN)
        new_epoch = self.epoch + 1
        aad = Writer().opaque(self.group_id.encode()).u64(new_epoch).bytes()
        sealed = {did: _hpke_seal(did, new_secret, aad) for did in recipients}
        roster = Writer()
        for did in new_members:
            roster.opaque(did.encode())
        body = (
            Writer().u8(int(proposal_type)).opaque(proposal_body).u64(new_epoch)
            .opaque(roster.bytes()).opaque(_encode_sealed_map(sealed)).bytes()
        )
        # Advance our own state.
        self.members = list(new_members)
        self.epoch = new_epoch
        self._epoch_secret = new_secret
        self._send_gen = 0
        self._seen.clear()
        return MLSMessage(WireFormat.PUBLIC_MESSAGE, body).encode()

    def add_member(self, did: str) -> bytes:
        """Add ``did``; re-key and return the Commit broadcast to existing members.

        The new member is bootstrapped separately via :meth:`create_welcome`.
        """
        if did in self.members:
            raise MLSGroupError(f"{did!r} already a member")
        new_members = self.members + [did]
        # New epoch sealed to everyone (existing + new) so all converge.
        prop = Writer()
        Add(did, _identity_pub_bytes(did)).encode(prop)
        return self._rekey_commit(ContentType.PROPOSAL, prop.bytes(), new_members, new_members)

    def remove_member(self, did: str) -> bytes:
        """Remove ``did``; re-key sealing the new epoch only to remaining members."""
        if did not in self.members:
            raise MLSGroupError(f"{did!r} not a member")
        if did == self.my_did:
            raise MLSGroupError("cannot remove self")
        remaining = [m for m in self.members if m != did]
        prop = Writer()
        Remove(did).encode(prop)
        return self._rekey_commit(ContentType.PROPOSAL, prop.bytes(), remaining, remaining)

    def apply_commit(self, commit: bytes) -> None:
        """Process a Commit from another member: adopt the new epoch if included."""
        try:
            msg = MLSMessage.decode(bytes(commit))
            if msg.wire_format != WireFormat.PUBLIC_MESSAGE:
                raise MLSGroupError("not a handshake/commit message")
            r = Reader(msg.body)
            _ptype = r.u8()
            _prop = r.opaque()
            new_epoch = r.u64()
            roster_r = Reader(r.opaque())
            new_members: list[str] = []
            while not roster_r.at_end():
                new_members.append(roster_r.opaque().decode())
            sealed = _decode_sealed_map(r.opaque())
        except (MLSProtocolError, ValueError) as exc:
            raise MLSGroupError(f"malformed commit: {exc}") from exc

        self.members = new_members
        if self.my_did in sealed:
            aad = Writer().opaque(self.group_id.encode()).u64(new_epoch).bytes()
            self._epoch_secret = _hpke_open(self.my_did, sealed[self.my_did], aad)
            self.epoch = new_epoch
            self._send_gen = 0
            self._seen.clear()
            self._active = self.my_did in new_members
        else:
            # Not a recipient → we were removed: cannot learn the new epoch.
            self._active = False

    # --- welcome ------------------------------------------------------------

    def create_welcome(self, did: str) -> bytes:
        """Seal the *current* epoch secret to ``did`` (founding or post-add join)."""
        if did not in self.members:
            raise MLSGroupError(f"{did!r} not in roster; add_member first")
        aad = Writer().opaque(self.group_id.encode()).u64(self.epoch).bytes()
        sealed = _hpke_seal(did, self._epoch_secret, aad)
        return MLSMessage(
            WireFormat.WELCOME,
            Welcome(_SUITE, self.group_id, self.epoch, self.members, sealed).encode(),
        ).encode()

    @classmethod
    def from_welcome(cls, welcome: bytes, my_did: str) -> "MLSGroup":
        """Join a group from a Welcome message addressed to ``my_did``."""
        try:
            msg = MLSMessage.decode(bytes(welcome))
            if msg.wire_format != WireFormat.WELCOME:
                raise MLSGroupError("not a Welcome message")
            w = Welcome.decode(msg.body)
        except (MLSProtocolError, ValueError) as exc:
            raise MLSGroupError(f"malformed welcome: {exc}") from exc
        if my_did not in w.members:
            raise MLSGroupError(f"{my_did!r} not in welcome roster")
        aad = Writer().opaque(w.group_id.encode()).u64(w.epoch).bytes()
        try:
            secret = _hpke_open(my_did, w.encrypted_group_secrets, aad)
        except Exception as exc:
            raise MLSGroupError(f"welcome unseal failed: {exc}") from exc
        g = cls.__new__(cls)
        g.group_id = w.group_id
        g.members = list(w.members)
        g.my_did = my_did
        g.epoch = w.epoch
        g._epoch_secret = secret
        g._send_gen = 0
        g._active = True
        g._seen = set()
        return g

    # --- introspection / persistence ---------------------------------------

    def ratchet_epoch(self) -> int:
        """Current group epoch (forward-secret boundary)."""
        return self.epoch

    def serialize_state(self) -> bytes:
        """JSON-encoded state for persistence (caller wraps secrets at rest)."""
        return json.dumps({
            "group_id": self.group_id,
            "members": self.members,
            "my_did": self.my_did,
            "epoch": self.epoch,
            "epoch_secret": self._epoch_secret.hex(),
            "send_gen": self._send_gen,
            "active": self._active,
            "seen": [[e, s, g] for (e, s, g) in self._seen],
        }, sort_keys=True).encode()

    @classmethod
    def from_state(cls, blob: bytes) -> "MLSGroup":
        try:
            d: dict[str, Any] = json.loads(bytes(blob).decode())
        except (ValueError, UnicodeDecodeError) as exc:
            raise MLSGroupError(f"bad state blob: {exc}") from exc
        g = cls.__new__(cls)
        g.group_id = d["group_id"]
        g.members = list(d["members"])
        g.my_did = d["my_did"]
        g.epoch = int(d["epoch"])
        g._epoch_secret = bytes.fromhex(d["epoch_secret"])
        g._send_gen = int(d["send_gen"])
        g._active = bool(d.get("active", True))
        g._seen = {(int(e), s, int(gn)) for e, s, gn in d.get("seen", [])}
        return g

    def __repr__(self) -> str:
        return (f"MLSGroup(group_id={self.group_id!r}, epoch={self.epoch}, "
                f"members={len(self.members)}, me={self.my_did!r})")


__all__ = ["MLSGroup", "MLSGroupError"]
