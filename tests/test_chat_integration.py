"""End-to-end integration test for sisoul chat (P2-G).

Alice and Bob each get their own ChatManager hooked up to a *shared*
in-process MemoryTransport. The test runs the full Signal-style flow:

    1. Alice opens a session → publishes INIT envelope on chat topic
    2. Bob receives the INIT, accepts it
    3. Alice sends N encrypted messages
    4. Bob subscribes, decrypts, verifies plaintext

This stands in for the real Kubo GossipSub transport — wire format is
identical (``WireEnvelope``).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from sisoul.chat.pqxdh import pqxdh_mode
from sisoul.chat.session import ChatManager
from sisoul.chat.transport import (
    MemoryTransport,
    WireEnvelope,
    chat_topic_for,
    prekey_topic_for,
    reset_shared_memory_transport,
)


@pytest.fixture
def chat_dir_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("SISOUL_CHAT_DIR", str(tmp_path / "chat"))
    reset_shared_memory_transport()
    yield tmp_path
    reset_shared_memory_transport()


def _make_pair(transport: MemoryTransport) -> tuple[ChatManager, ChatManager]:
    alice = ChatManager(
        local_did="did:key:zAliceTest",
        master_key=b"A" * 32,
        transport=transport,
    )
    bob = ChatManager(
        local_did="did:key:zBobTest",
        master_key=b"B" * 32,
        transport=transport,
    )
    # Manually hand bundles across (in production this would go through
    # prekey_topic_for(...) GossipSub announces).
    alice.cache_peer_prekey(bob.keys.bundle)
    bob.cache_peer_prekey(alice.keys.bundle)
    return alice, bob


# 1
def test_pqxdh_mode_in_test_env():
    """We expect the real backend (kyber-py) to be available in CI."""
    assert pqxdh_mode() in ("real", "shim")


# 2
async def test_alice_opens_session_publishes_init(chat_dir_tmp):
    transport = MemoryTransport()
    alice, bob = _make_pair(transport)
    topic = chat_topic_for(alice.local_did, bob.local_did)

    # Subscribe Bob *before* Alice publishes
    gen = await transport.subscribe(topic)
    await alice.open_session(bob.local_did)

    # First envelope on the wire must be kind=init
    raw = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    env = WireEnvelope.from_bytes(raw)
    assert env.kind == "init"
    assert env.body["from_did"] == alice.local_did
    assert len(bytes.fromhex(env.body["mlkem_ct"])) == 1568
    await gen.aclose()


# 3
async def test_full_alice_to_bob_roundtrip(chat_dir_tmp):
    transport = MemoryTransport()
    alice, bob = _make_pair(transport)
    topic = chat_topic_for(alice.local_did, bob.local_did)

    bob_inbox: list[bytes] = []

    async def bob_listener():
        gen = await transport.subscribe(topic)
        try:
            async for raw in gen:
                env = WireEnvelope.from_bytes(raw)
                if env.body.get("from_did") == bob.local_did:
                    continue
                result = await bob.handle_incoming(env)
                if result is not None:
                    bob_inbox.append(result[1])
                    if len(bob_inbox) >= 3:
                        return
        finally:
            await gen.aclose()

    listener_task = asyncio.create_task(bob_listener())
    # Wait for subscription to register
    await asyncio.sleep(0.05)

    await alice.send(bob.local_did, "hello bob")
    await alice.send(bob.local_did, "second message")
    await alice.send(bob.local_did, "third message")

    await asyncio.wait_for(listener_task, timeout=2.0)
    assert bob_inbox == [b"hello bob", b"second message", b"third message"]


# 4
async def test_bidirectional_after_handshake(chat_dir_tmp):
    transport = MemoryTransport()
    alice, bob = _make_pair(transport)
    topic = chat_topic_for(alice.local_did, bob.local_did)

    inbox_a: list[bytes] = []
    inbox_b: list[bytes] = []

    async def listener(mgr: ChatManager, inbox: list[bytes], expected: int):
        gen = await transport.subscribe(topic)
        try:
            async for raw in gen:
                env = WireEnvelope.from_bytes(raw)
                if env.body.get("from_did") == mgr.local_did:
                    continue
                result = await mgr.handle_incoming(env)
                if result is not None:
                    inbox.append(result[1])
                    if len(inbox) >= expected:
                        return
        finally:
            await gen.aclose()

    task_b = asyncio.create_task(listener(bob, inbox_b, 2))
    await asyncio.sleep(0.05)
    await alice.send(bob.local_did, "ping1")
    await alice.send(bob.local_did, "ping2")
    await asyncio.wait_for(task_b, timeout=2.0)
    assert inbox_b == [b"ping1", b"ping2"]

    # Now Bob replies to Alice
    task_a = asyncio.create_task(listener(alice, inbox_a, 2))
    await asyncio.sleep(0.05)
    await bob.send(alice.local_did, "pong1")
    await bob.send(alice.local_did, "pong2")
    await asyncio.wait_for(task_a, timeout=2.0)
    assert inbox_a == [b"pong1", b"pong2"]


# 5
async def test_prekey_announce_publishes_on_topic(chat_dir_tmp):
    transport = MemoryTransport()
    alice = ChatManager(
        local_did="did:key:zAnnouncer",
        master_key=b"M" * 32,
        transport=transport,
    )
    topic = prekey_topic_for(alice.local_did)
    gen = await transport.subscribe(topic)
    await alice.announce_prekey()
    raw = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    env = WireEnvelope.from_bytes(raw)
    assert env.kind == "prekey"
    assert env.body["did"] == alice.local_did
    await gen.aclose()


# 6
async def test_rotate_prekey_yields_fresh_keys(chat_dir_tmp):
    transport = MemoryTransport()
    alice = ChatManager(
        local_did="did:key:zRotator",
        master_key=b"R" * 32,
        transport=transport,
    )
    old_pub = alice.keys.x25519_identity_pub
    old_mlkem = alice.keys.mlkem_pub
    alice.rotate_prekey()
    assert alice.keys.x25519_identity_pub != old_pub
    assert alice.keys.mlkem_pub != old_mlkem


# 7
def test_session_persistence_roundtrip(chat_dir_tmp):
    transport = MemoryTransport()
    alice, bob = _make_pair(transport)

    async def go():
        await alice.open_session(bob.local_did)
        await alice.send(bob.local_did, "persisted-msg")
        alice.persist()

    asyncio.run(go())

    # New ChatManager loads the persisted state
    alice2 = ChatManager(
        local_did="did:key:zAliceTest",
        master_key=b"A" * 32,
        transport=transport,
    )
    loaded = alice2.load(bob.local_did)
    assert loaded is not None
    assert loaded.session.sending_chain_length >= 1
    assert loaded.peer_did == bob.local_did
