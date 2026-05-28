"""Wave B' agent-B1 · Waku two-instance integration test.

§J-2 真验收: same-process 双 WakuTransport 真互发 1k 消息 + store-and-forward
离线-上线 catchup verify + libsodium 加密包装真跑.

不真跑 nwaku subprocess (用户限本 agent 不在 mac/aws-us 真启 daemon),
全部走 mock mode, 但发送路径走真 send/recv API.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from sisoul.p2p.transport import Message
from sisoul.p2p.waku_transport import (
    DEFAULT_STORE_TTL_SEC,
    WakuStoreTTLExceeded,
    WakuTransport,
    _bus_clear,
    build_content_topic,
    did_to_short,
)


SAMPLE_DID_ALICE = "did:key:z6MkrJVnaZkeFzdQyMZu1cF5fXkVqXmTJZ5xS7aBcDeFgHiJ"
SAMPLE_DID_BOB = "did:key:z6MkuTWdJX5wYvBpZ8oTKp2Lm9NqQrStUvWxYzAbCdEfGhIj"
SAMPLE_DID_CAROL = "did:key:z6MkvWxYzAbCdEfGhIjKlMnOpQrStUvWxYzAbCdEfGhIjKlM"


@pytest.fixture(autouse=True)
def _clean_bus():
    _bus_clear()
    yield
    _bus_clear()


@pytest.mark.asyncio
async def test_1k_messages_throughput_and_integrity():
    """Alice send 1000 条, Bob 真收 1000 条 (1:1 一致). §J-2 真验收."""
    alice = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock", recv_queue_size=2000)
    bob = WakuTransport("bob", my_did=SAMPLE_DID_BOB, mode="mock", recv_queue_size=2000)
    await alice.start()
    await bob.start()

    try:
        topic = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
        await bob.subscribe_topic(topic)

        N = 1000
        send_start = time.monotonic()
        for i in range(N):
            await alice.send_to_topic(topic, f"msg-{i:06d}".encode())
        send_elapsed = time.monotonic() - send_start

        recv_start = time.monotonic()
        received: list[Message] = []
        while len(received) < N:
            m = await bob.recv(timeout=5.0)
            if m is None:
                break
            received.append(m)
        recv_elapsed = time.monotonic() - recv_start

        assert len(received) == N, f"丢消息: 发 {N} 收 {len(received)}"

        payloads_set = {m.payload for m in received}
        expected = {f"msg-{i:06d}".encode() for i in range(N)}
        assert payloads_set == expected

        print(f"\n[V3 throughput] 1000 msg: send {send_elapsed:.2f}s ({N/send_elapsed:.0f} msg/s) "
              f"recv {recv_elapsed:.2f}s ({N/recv_elapsed:.0f} msg/s)")
        assert N / send_elapsed > 100
    finally:
        await alice.stop()
        await bob.stop()


@pytest.mark.asyncio
async def test_offline_alice_sends_bob_catchup():
    """Bob 未 start, Alice 发 100 条 → Bob start + query_store 拉到 100 条."""
    alice = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
    await alice.start()
    try:
        topic = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
        N = 100
        for i in range(N):
            await alice.send_to_topic(topic, f"offline-{i:04d}".encode())

        bob = WakuTransport("bob", my_did=SAMPLE_DID_BOB, mode="mock")
        await bob.start()
        try:
            msgs = await bob.query_store(
                SAMPLE_DID_ALICE, since_ts=time.time() - 60, purposes=["borrow"]
            )
            assert len(msgs) == N
            payloads = {m.payload for m in msgs}
            assert payloads == {f"offline-{i:04d}".encode() for i in range(N)}
        finally:
            await bob.stop()
    finally:
        await alice.stop()


@pytest.mark.asyncio
async def test_24h_ttl_query_rejected():
    """query_store(since_ts > 24h 前) 抛 WakuStoreTTLExceeded."""
    bob = WakuTransport("bob", my_did=SAMPLE_DID_BOB, mode="mock")
    await bob.start()
    try:
        with pytest.raises(WakuStoreTTLExceeded):
            await bob.query_store(SAMPLE_DID_ALICE, since_ts=time.time() - DEFAULT_STORE_TTL_SEC - 1)

        msgs = await bob.query_store(SAMPLE_DID_ALICE, since_ts=time.time() - 3600 * 23)
        assert msgs == []
    finally:
        await bob.stop()


@pytest.mark.asyncio
async def test_libsodium_box_envelope_e2e():
    """WakuTransport 是裸 byte 通道, 加密由上层包. 真跑 libsodium box."""
    from nacl.public import Box, PrivateKey

    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    alice_pub = alice_priv.public_key
    bob_pub = bob_priv.public_key

    plaintext = b"sk-secret-borrow-request: claude opus 1000 tokens"
    box_a_to_b = Box(alice_priv, bob_pub)
    ciphertext = box_a_to_b.encrypt(plaintext)
    assert ciphertext != plaintext
    assert len(ciphertext) > len(plaintext)

    alice = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
    bob = WakuTransport("bob", my_did=SAMPLE_DID_BOB, mode="mock")
    await alice.start()
    await bob.start()
    try:
        topic = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
        await bob.subscribe_topic(topic)

        await alice.send_to_topic(topic, ciphertext)

        msg = await bob.recv(timeout=2.0)
        assert msg is not None
        box_b_from_a = Box(bob_priv, alice_pub)
        decrypted = box_b_from_a.decrypt(msg.payload)
        assert decrypted == plaintext
    finally:
        await alice.stop()
        await bob.stop()


@pytest.mark.asyncio
async def test_multi_pair_concurrent():
    """3 sender ↔ 3 receiver 并发互发, 不串台."""
    senders: list[WakuTransport] = []
    receivers: list[WakuTransport] = []
    sender_dids = [
        "did:key:z6MkSenderAAA1111111111111111111111111111111111A",
        "did:key:z6MkSenderBBB2222222222222222222222222222222222B",
        "did:key:z6MkSenderCCC3333333333333333333333333333333333C",
    ]
    receiver_dids = [
        "did:key:z6MkReceiverAAA1111111111111111111111111111111x",
        "did:key:z6MkReceiverBBB2222222222222222222222222222222y",
        "did:key:z6MkReceiverCCC3333333333333333333333333333333z",
    ]

    for i, did in enumerate(sender_dids):
        s = WakuTransport(f"s{i}", my_did=did, mode="mock")
        await s.start()
        senders.append(s)
    for i, did in enumerate(receiver_dids):
        r = WakuTransport(f"r{i}", my_did=did, mode="mock")
        await r.start()
        receivers.append(r)

    try:
        topics = [build_content_topic(sender_dids[i], receiver_dids[i], "borrow") for i in range(3)]
        for i, r in enumerate(receivers):
            await r.subscribe_topic(topics[i])

        N_per_pair = 50

        async def send_loop(idx):
            for j in range(N_per_pair):
                await senders[idx].send_to_topic(topics[idx], f"pair{idx}-{j}".encode())

        async def recv_loop(idx):
            got = []
            while len(got) < N_per_pair:
                m = await receivers[idx].recv(timeout=3.0)
                if m is None:
                    break
                got.append(m)
            return got

        send_tasks = [send_loop(i) for i in range(3)]
        recv_tasks = [recv_loop(i) for i in range(3)]
        await asyncio.gather(*send_tasks)
        results = await asyncio.gather(*recv_tasks)

        for i, got in enumerate(results):
            assert len(got) == N_per_pair, f"pair {i} 丢消息"
            for m in got:
                assert m.payload.startswith(f"pair{i}-".encode())
    finally:
        for t in senders + receivers:
            await t.stop()


@pytest.mark.asyncio
async def test_bidirectional_communication():
    """Alice ↔ Bob 双向 (各 100 条)."""
    alice = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
    bob = WakuTransport("bob", my_did=SAMPLE_DID_BOB, mode="mock")
    await alice.start()
    await bob.start()
    try:
        topic_a_to_b = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
        topic_b_to_a = build_content_topic(SAMPLE_DID_BOB, SAMPLE_DID_ALICE, "borrow")
        await bob.subscribe_topic(topic_a_to_b)
        await alice.subscribe_topic(topic_b_to_a)

        N = 100

        async def alice_send():
            for i in range(N):
                await alice.send_to_topic(topic_a_to_b, f"a→b-{i}".encode())

        async def bob_send():
            for i in range(N):
                await bob.send_to_topic(topic_b_to_a, f"b→a-{i}".encode())

        async def alice_recv():
            got = []
            while len(got) < N:
                m = await alice.recv(timeout=5.0)
                if m is None:
                    break
                got.append(m)
            return got

        async def bob_recv():
            got = []
            while len(got) < N:
                m = await bob.recv(timeout=5.0)
                if m is None:
                    break
                got.append(m)
            return got

        results = await asyncio.gather(alice_send(), bob_send(), alice_recv(), bob_recv())
        alice_got, bob_got = results[2], results[3]
        assert len(alice_got) == N
        assert len(bob_got) == N
        assert all(m.payload.startswith(b"b\xe2\x86\x92a-") for m in alice_got)
        assert all(m.payload.startswith(b"a\xe2\x86\x92b-") for m in bob_got)
    finally:
        await alice.stop()
        await bob.stop()


@pytest.mark.asyncio
async def test_bob_reconnect_pull_missed():
    """Bob 在线时收 N 条 → 断 → Alice 又发 M 条 → Bob 重连 query_store 拉 M 条."""
    alice = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
    bob = WakuTransport("bob", my_did=SAMPLE_DID_BOB, mode="mock")
    await alice.start()
    await bob.start()

    topic = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
    await bob.subscribe_topic(topic)

    for i in range(20):
        await alice.send_to_topic(topic, f"online-{i}".encode())
    online_got = []
    while len(online_got) < 20:
        m = await bob.recv(timeout=2.0)
        if m is None:
            break
        online_got.append(m)
    assert len(online_got) == 20

    await bob.stop()
    await asyncio.sleep(0.05)
    offline_cutoff = time.time()
    await asyncio.sleep(0.05)

    for i in range(15):
        await alice.send_to_topic(topic, f"offline-{i}".encode())

    bob2 = WakuTransport("bob2", my_did=SAMPLE_DID_BOB, mode="mock")
    await bob2.start()
    try:
        missed = await bob2.query_store(SAMPLE_DID_ALICE, since_ts=offline_cutoff, purposes=["borrow"])
        assert len(missed) == 15
        assert {m.payload for m in missed} == {f"offline-{i}".encode() for i in range(15)}
    finally:
        await bob2.stop()
        await alice.stop()


@pytest.mark.asyncio
async def test_purpose_routing_isolation():
    """Alice send borrow + ledger 各 5 条, Bob 只订 borrow → 仅收 borrow."""
    alice = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
    bob = WakuTransport("bob", my_did=SAMPLE_DID_BOB, mode="mock")
    await alice.start()
    await bob.start()
    try:
        topic_borrow = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
        topic_ledger = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "ledger")
        await bob.subscribe_topic(topic_borrow)

        for i in range(5):
            await alice.send_to_topic(topic_borrow, f"b{i}".encode())
            await alice.send_to_topic(topic_ledger, f"l{i}".encode())

        got = []
        while True:
            m = await bob.recv(timeout=0.5)
            if m is None:
                break
            got.append(m)

        assert len(got) == 5
        assert {m.payload for m in got} == {f"b{i}".encode() for i in range(5)}

        msgs_borrow = await bob.query_store(SAMPLE_DID_ALICE, since_ts=time.time() - 60, purposes=["borrow"])
        assert len(msgs_borrow) == 5
        msgs_ledger = await bob.query_store(SAMPLE_DID_ALICE, since_ts=time.time() - 60, purposes=["ledger"])
        assert len(msgs_ledger) == 5
    finally:
        await alice.stop()
        await bob.stop()


@pytest.mark.asyncio
async def test_wildcard_query_store_all_peers():
    """Carol query_store("*") 拉所有寄给 Carol 的."""
    alice = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
    bob = WakuTransport("bob", my_did=SAMPLE_DID_BOB, mode="mock")
    await alice.start()
    await bob.start()
    try:
        topic_a_to_c = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_CAROL, "borrow")
        topic_b_to_c = build_content_topic(SAMPLE_DID_BOB, SAMPLE_DID_CAROL, "borrow")

        for i in range(3):
            await alice.send_to_topic(topic_a_to_c, f"a{i}".encode())
        for i in range(4):
            await bob.send_to_topic(topic_b_to_c, f"b{i}".encode())

        carol = WakuTransport("carol", my_did=SAMPLE_DID_CAROL, mode="mock")
        await carol.start()
        try:
            all_msgs = await carol.query_store("*", since_ts=time.time() - 60, purposes=["borrow"])
            assert len(all_msgs) == 7
            assert {m.payload for m in all_msgs} == (
                {f"a{i}".encode() for i in range(3)} | {f"b{i}".encode() for i in range(4)}
            )
        finally:
            await carol.stop()
    finally:
        await alice.stop()
        await bob.stop()


@pytest.mark.asyncio
async def test_subscribe_then_send_works():
    """Bob 先订, Alice 后发 → Bob 实时收."""
    alice = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
    bob = WakuTransport("bob", my_did=SAMPLE_DID_BOB, mode="mock")
    await alice.start()
    await bob.start()
    try:
        topic = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
        await bob.subscribe_topic(topic)
        await alice.send_to_topic(topic, b"hi")
        m = await bob.recv(timeout=1.0)
        assert m is not None
        assert m.payload == b"hi"
    finally:
        await alice.stop()
        await bob.stop()


@pytest.mark.asyncio
async def test_send_then_subscribe_only_store_has_it():
    """Alice 先发, Bob 后订 → 实时收不到, 但 store 留底."""
    alice = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
    bob = WakuTransport("bob", my_did=SAMPLE_DID_BOB, mode="mock")
    await alice.start()
    await bob.start()
    try:
        topic = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
        await alice.send_to_topic(topic, b"missed")
        await bob.subscribe_topic(topic)
        m = await bob.recv(timeout=0.2)
        assert m is None

        msgs = await bob.query_store(SAMPLE_DID_ALICE, since_ts=time.time() - 60, purposes=["borrow"])
        assert len(msgs) == 1
        assert msgs[0].payload == b"missed"
    finally:
        await alice.stop()
        await bob.stop()
