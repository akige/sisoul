"""tests for sisoul.p2p.push · core notify + WS + macOS notify mock (Wave B' P1-1).

15+ case (实际 ~20). 全 mock + pytest, 不在 mac/aws-us 真测.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from sisoul.p2p import push as push_mod
from sisoul.p2p.push import (
    HEARTBEAT_TOPIC,
    HeartbeatTracker,
    InMemoryWakuTransport,
    Notification,
    NotificationStore,
    PushService,
    WakuMessage,
    create_push_service,
    get_peer_status,
    get_push_service,
    inbox_topic,
    list_recent_notifications,
    notify_friend_sync,
    record_external_heartbeat,
    set_mock_macos_notify,
    set_push_service,
    _reset_for_test,
)


@pytest.fixture
def tmp_store(tmp_path: Path) -> NotificationStore:
    return NotificationStore(db_path=tmp_path / "notify.db")


@pytest.fixture(autouse=True)
def _reset() -> Any:
    _reset_for_test()
    yield
    _reset_for_test()


# ── topic ───────────────────────────────────────────────────────────────────


class TestTopic:
    def test_inbox_topic_format(self) -> None:
        assert inbox_topic("did:key:zAlice") == "/sisoul/did:key:zAlice_inbox/v1/notify"

    def test_inbox_topic_strips_slashes_in_did(self) -> None:
        t = inbox_topic("did/x/y")
        assert "/" not in t.removeprefix("/sisoul/").split("_inbox")[0]


# ── NotificationStore ──────────────────────────────────────────────────────


class TestNotificationStore:
    def test_insert_and_list(self, tmp_store: NotificationStore) -> None:
        n = Notification(
            notify_id="n_test1",
            kind="borrow_request",
            source_did="did:key:zBob",
            target_did="did:key:zAlice",
            payload={"amount": 1000},
            ts=time.time(),
        )
        tmp_store.insert(n)
        rows = tmp_store.list_recent()
        assert len(rows) == 1
        assert rows[0].notify_id == "n_test1"
        assert rows[0].payload == {"amount": 1000}

    def test_mark_read(self, tmp_store: NotificationStore) -> None:
        n = Notification(
            "n_x", "system", "s", "t", {}, time.time(), False, []
        )
        tmp_store.insert(n)
        assert tmp_store.mark_read("n_x") is True
        assert tmp_store.mark_read("n_nonexistent") is False
        rows = tmp_store.list_recent()
        assert rows[0].read is True

    def test_filter_unread_only(self, tmp_store: NotificationStore) -> None:
        t = time.time()
        tmp_store.insert(Notification("n1", "system", "a", "z", {}, t, True, []))
        tmp_store.insert(Notification("n2", "system", "a", "z", {}, t, False, []))
        unread = tmp_store.list_recent(unread_only=True)
        assert len(unread) == 1 and unread[0].notify_id == "n2"

    def test_filter_kind(self, tmp_store: NotificationStore) -> None:
        t = time.time()
        tmp_store.insert(
            Notification("nA", "borrow_request", "a", "z", {}, t)
        )
        tmp_store.insert(
            Notification("nB", "lend_response", "a", "z", {}, t + 1)
        )
        rows = tmp_store.list_recent(kind="borrow_request")
        assert len(rows) == 1 and rows[0].notify_id == "nA"

    def test_filter_target_did(self, tmp_store: NotificationStore) -> None:
        t = time.time()
        tmp_store.insert(
            Notification("nA", "system", "x", "did:Alice", {}, t)
        )
        tmp_store.insert(
            Notification("nB", "system", "x", "did:Bob", {}, t + 1)
        )
        rows = tmp_store.list_recent(target_did="did:Alice")
        assert len(rows) == 1 and rows[0].notify_id == "nA"

    def test_catchup_cursor(self, tmp_store: NotificationStore) -> None:
        assert tmp_store.get_catchup_cursor("/topic/x") == 0.0
        tmp_store.set_catchup_cursor("/topic/x", 12345.0)
        assert tmp_store.get_catchup_cursor("/topic/x") == 12345.0


# ── HeartbeatTracker ───────────────────────────────────────────────────────


class TestHeartbeat:
    def test_unknown_when_no_data(self, tmp_store: NotificationStore) -> None:
        tr = HeartbeatTracker(store=tmp_store)
        s = tr.get_status("did:key:zBob")
        assert s.state == "unknown"
        assert s.last_heartbeat_ts is None

    def test_online_when_recent(self, tmp_store: NotificationStore) -> None:
        tr = HeartbeatTracker(store=tmp_store, offline_threshold_sec=300)
        tr.record("did:key:zBob", time.time() - 10)
        s = tr.get_status("did:key:zBob")
        assert s.state == "online"
        assert s.last_seen_age_sec is not None and s.last_seen_age_sec < 300

    def test_offline_when_stale(self, tmp_store: NotificationStore) -> None:
        tr = HeartbeatTracker(store=tmp_store, offline_threshold_sec=300)
        tr.record("did:key:zBob", time.time() - 600)  # 10min ago
        s = tr.get_status("did:key:zBob")
        assert s.state == "offline"

    def test_list_all_mixed(self, tmp_store: NotificationStore) -> None:
        tr = HeartbeatTracker(store=tmp_store, offline_threshold_sec=300)
        now = time.time()
        tr.record("did:A", now - 10)
        tr.record("did:B", now - 700)
        tr.record("did:C", now - 60)
        peers = {p.did: p.state for p in tr.list_all(now=now)}
        assert peers == {"did:A": "online", "did:B": "offline", "did:C": "online"}


# ── InMemoryWakuTransport ──────────────────────────────────────────────────


class TestInMemoryWakuTransport:
    @pytest.mark.asyncio
    async def test_subscribe_and_send_fanout(self) -> None:
        t = InMemoryWakuTransport()
        got: list[WakuMessage] = []

        async def cb(m: WakuMessage) -> None:
            got.append(m)

        await t.subscribe_topic("/foo", cb)
        await t.send("/foo", {"hello": "world"})
        assert len(got) == 1
        assert got[0].payload == {"hello": "world"}

    @pytest.mark.asyncio
    async def test_query_store_since_ts(self) -> None:
        t = InMemoryWakuTransport()
        m1 = await t.send("/foo", {"i": 1})
        await asyncio.sleep(0.01)
        m2 = await t.send("/foo", {"i": 2})
        # since 0 → both
        all_msgs = await t.query_store("/foo", since_ts=0.0)
        assert len(all_msgs) == 2
        # since m1.ts → only m2
        after = await t.query_store("/foo", since_ts=m1.ts)
        assert len(after) == 1
        assert after[0].msg_id == m2.msg_id

    @pytest.mark.asyncio
    async def test_shared_store_across_transports(self) -> None:
        """模拟跨 peer: 两个 transport 共享 store, send 一个 query 另一个也能拿."""
        shared: dict = {}
        a = InMemoryWakuTransport(peer_id="A", shared_store=shared)
        b = InMemoryWakuTransport(peer_id="B", shared_store=shared)
        await a.send("/topic/x", {"from": "A"})
        msgs = await b.query_store("/topic/x")
        assert len(msgs) == 1
        assert msgs[0].payload == {"from": "A"}

    @pytest.mark.asyncio
    async def test_unsubscribe(self) -> None:
        t = InMemoryWakuTransport()
        got: list[WakuMessage] = []

        async def cb(m: WakuMessage) -> None:
            got.append(m)

        await t.subscribe_topic("/foo", cb)
        await t.send("/foo", {"i": 1})
        await t.unsubscribe("/foo")
        await t.send("/foo", {"i": 2})
        assert len(got) == 1


# ── PushService 主流程 ────────────────────────────────────────────────────


class TestPushService:
    @pytest.mark.asyncio
    async def test_start_subscribes_inbox_and_heartbeat(
        self, tmp_store: NotificationStore
    ) -> None:
        t = InMemoryWakuTransport()
        svc = PushService("did:key:zAlice", t, store=tmp_store)
        await svc.start()
        try:
            assert inbox_topic("did:key:zAlice") in t._subscribers
            assert HEARTBEAT_TOPIC in t._subscribers
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_on_inbox_message_persists_and_fanouts(
        self, tmp_store: NotificationStore
    ) -> None:
        t = InMemoryWakuTransport()
        svc = PushService("did:key:zAlice", t, store=tmp_store)
        got: list[Notification] = []

        async def listener(n: Notification) -> None:
            got.append(n)

        await svc.register_listener(listener)
        await svc.start()
        try:
            # 别人通过同 transport send 到 Alice inbox
            await t.send(
                inbox_topic("did:key:zAlice"),
                {
                    "kind": "borrow_request",
                    "source_did": "did:key:zBob",
                    "amount": 1000,
                    "resource_type": "llm_quota",
                },
            )
            # 等 fanout
            await asyncio.sleep(0.05)
            assert len(got) == 1
            assert got[0].kind == "borrow_request"
            assert got[0].source_did == "did:key:zBob"
            persisted = tmp_store.list_recent()
            assert len(persisted) == 1
            assert "ws" in persisted[0].delivered_via
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_notify_friend_via_send(
        self, tmp_store: NotificationStore
    ) -> None:
        shared: dict = {}
        alice_t = InMemoryWakuTransport(peer_id="alice", shared_store=shared)
        bob_t = InMemoryWakuTransport(peer_id="bob", shared_store=shared)
        alice_svc = PushService("did:key:zAlice", alice_t, store=tmp_store)
        # Alice push to Bob's inbox
        msg = await alice_svc.notify_friend(
            "did:key:zBob",
            "borrow_request",
            {"amount": 500, "model": "claude-opus-4-7"},
        )
        assert msg.topic == inbox_topic("did:key:zBob")
        # Bob query store → see it
        msgs = await bob_t.query_store(inbox_topic("did:key:zBob"))
        assert len(msgs) == 1
        assert msgs[0].payload["kind"] == "borrow_request"
        assert msgs[0].payload["source_did"] == "did:key:zAlice"

    @pytest.mark.asyncio
    async def test_macos_notify_mock_called(
        self, tmp_store: NotificationStore
    ) -> None:
        calls: list[tuple[str, str]] = []

        def mock_notify(title: str, message: str) -> bool:
            calls.append((title, message))
            return True

        set_mock_macos_notify(mock_notify)
        t = InMemoryWakuTransport()
        svc = PushService("did:key:zAlice", t, store=tmp_store)
        await svc.start()
        try:
            await t.send(
                inbox_topic("did:key:zAlice"),
                {
                    "kind": "borrow_request",
                    "source_did": "did:key:zBob",
                    "amount": 100,
                    "resource_type": "llm_quota",
                },
            )
            await asyncio.sleep(0.05)
            assert len(calls) == 1
            assert "sisoul" in calls[0][0]
            assert "想借" in calls[0][1]
        finally:
            await svc.stop()
            set_mock_macos_notify(None)

    @pytest.mark.asyncio
    async def test_dedup_via_msg_id(
        self, tmp_store: NotificationStore
    ) -> None:
        """同一 msg_id 投两次 → store 只 1 条."""
        t = InMemoryWakuTransport()
        svc = PushService("did:key:zAlice", t, store=tmp_store)
        msg = WakuMessage(
            topic=inbox_topic("did:key:zAlice"),
            payload={"kind": "system", "source_did": "did:key:zBob"},
        )
        await svc._on_inbox_message(msg)
        await svc._on_inbox_message(msg)  # 再次, 应去重
        rows = tmp_store.list_recent()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_heartbeat_received_updates_tracker(
        self, tmp_store: NotificationStore
    ) -> None:
        t = InMemoryWakuTransport()
        svc = PushService("did:key:zAlice", t, store=tmp_store)
        await svc.start()
        try:
            await t.send(
                HEARTBEAT_TOPIC,
                {"did": "did:key:zBob", "ts": time.time()},
            )
            await asyncio.sleep(0.05)
            s = svc.tracker.get_status("did:key:zBob")
            assert s.state == "online"
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_loop_sends_self_heartbeat(
        self, tmp_store: NotificationStore
    ) -> None:
        t = InMemoryWakuTransport()
        svc = PushService(
            "did:key:zAlice",
            t,
            store=tmp_store,
            heartbeat_interval_sec=0.1,
        )
        await svc.start()
        try:
            await asyncio.sleep(0.25)  # 应至少跑 2 轮
            stored = await t.query_store(HEARTBEAT_TOPIC)
            assert len(stored) >= 2
            for m in stored:
                assert m.payload["did"] == "did:key:zAlice"
        finally:
            await svc.stop()

    @pytest.mark.asyncio
    async def test_listener_unregister(
        self, tmp_store: NotificationStore
    ) -> None:
        t = InMemoryWakuTransport()
        svc = PushService("did:key:zAlice", t, store=tmp_store)
        got: list[Notification] = []

        async def cb(n: Notification) -> None:
            got.append(n)

        unreg = await svc.register_listener(cb)
        await svc.start()
        try:
            await t.send(
                inbox_topic("did:key:zAlice"),
                {"kind": "system", "source_did": "did:key:zBob"},
            )
            await asyncio.sleep(0.05)
            assert len(got) == 1
            await unreg()
            await t.send(
                inbox_topic("did:key:zAlice"),
                {"kind": "system", "source_did": "did:key:zBob"},
            )
            await asyncio.sleep(0.05)
            assert len(got) == 1  # 没再涨
        finally:
            await svc.stop()


# ── top-level helpers ──────────────────────────────────────────────────────


class TestTopLevelHelpers:
    def test_get_peer_status_without_service(self, tmp_path: Path, monkeypatch) -> None:
        # 用临时 db 避免污染
        from sisoul.p2p import push as p
        monkeypatch.setattr(p, "_DEFAULT_NOTIFY_DB", tmp_path / "n.db")
        s = get_peer_status("did:key:zUnknown")
        assert s.state == "unknown"

    def test_record_external_heartbeat_without_service(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from sisoul.p2p import push as p
        monkeypatch.setattr(p, "_DEFAULT_NOTIFY_DB", tmp_path / "n.db")
        record_external_heartbeat("did:key:zBob")
        s = get_peer_status("did:key:zBob")
        assert s.state == "online"

    @pytest.mark.asyncio
    async def test_create_push_service_uses_inmem_transport(
        self, tmp_store: NotificationStore
    ) -> None:
        svc = create_push_service("did:key:zAlice", store=tmp_store)
        assert get_push_service() is svc
        assert isinstance(svc.transport, InMemoryWakuTransport)
        await svc.start()
        try:
            assert svc._started
        finally:
            await svc.stop()
