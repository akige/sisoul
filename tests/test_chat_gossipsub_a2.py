"""A2: GossipSub-first chat — prekey discovery + send + recv across managers.

Uses the shared in-memory transport (same ChatTransport interface as kubo), so a
pass here proves the wiring is correct; the two-machine kubo run validates the
real transport. Also covers per-identity key persistence (save/load round-trip).
"""

from __future__ import annotations

import asyncio

import pytest

from sisoul.chat.pqxdh import generate_pre_key_bundle
from sisoul.chat.session import ChatManager, load_local_keys, save_local_keys
from sisoul.chat.transport import (
    WireEnvelope,
    chat_topic_for,
    get_shared_memory_transport,
    reset_shared_memory_transport,
)
from sisoul.cli_commands.chat import _discover_prekey_gossipsub

ALICE = "did:key:zAlice"
BOB = "did:key:zBob"


@pytest.fixture(autouse=True)
def _chat_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SISOUL_CHAT_DIR", str(tmp_path / "chat"))
    reset_shared_memory_transport()
    yield
    reset_shared_memory_transport()


def _mgr(did: str, master: bytes) -> ChatManager:
    return ChatManager(
        local_did=did,
        master_key=master,
        transport=get_shared_memory_transport(),
        keys=generate_pre_key_bundle(did),
    )


# ── key persistence ───────────────────────────────────────────────────────────


def test_local_keys_persist_roundtrip():
    master = b"M" * 32
    keys = generate_pre_key_bundle(BOB)
    save_local_keys(keys, master)
    loaded = load_local_keys(BOB, master)
    assert loaded is not None
    assert loaded.bundle.to_dict() == keys.bundle.to_dict()
    assert loaded.x25519_identity_priv == keys.x25519_identity_priv
    assert loaded.mlkem_priv == keys.mlkem_priv


def test_local_keys_wrong_master_returns_none():
    save_local_keys(generate_pre_key_bundle(BOB), b"M" * 32)
    assert load_local_keys(BOB, b"X" * 32) is None  # wrong key -> regenerate, not crash


def test_local_keys_absent_returns_none():
    assert load_local_keys("did:key:zNobody", b"M" * 32) is None


# ── GossipSub discovery + end-to-end send/recv ────────────────────────────────


async def test_discover_then_send_recv():
    alice = _mgr(ALICE, b"A" * 32)
    bob = _mgr(BOB, b"B" * 32)

    # Bob announces his prekey bundle on his prekey topic.
    await bob.announce_prekey()

    # Alice has no cached bundle; she discovers it over (memory) GossipSub.
    assert alice.load_peer_prekey(BOB) is None
    got = await _discover_prekey_gossipsub(alice, BOB, timeout=2.0)
    assert got is True
    assert alice.load_peer_prekey(BOB) is not None

    # Bob subscribes to the chat topic to receive.
    topic = chat_topic_for(ALICE, BOB)
    bob_gen = await bob.transport.subscribe(topic)
    received: list[tuple[str, bytes]] = []

    async def _bob_recv() -> None:
        async for raw in bob_gen:
            res = await bob.handle_incoming(WireEnvelope.from_bytes(raw))
            if res is not None:
                received.append(res)
                return

    # Alice sends (publishes init + msg to the chat topic).
    await alice.send(BOB, "hi bob")
    await asyncio.wait_for(_bob_recv(), timeout=3.0)

    assert received and received[0][0] == ALICE
    assert received[0][1] == b"hi bob"


async def test_discover_times_out_when_no_announce():
    alice = _mgr(ALICE, b"A" * 32)
    # nobody announced BOB's prekey -> discovery returns False (not a hang/crash)
    got = await _discover_prekey_gossipsub(alice, BOB, timeout=0.5)
    assert got is False
    assert alice.load_peer_prekey(BOB) is None
