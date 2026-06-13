"""测试 p2p.waku_transport — WakuTransport 单元 + mock REST + fallback (Wave B' agent-B1)."""

from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sisoul.p2p.transport import Message
from sisoul.p2p.waku_transport import (
    DEFAULT_BOOTSTRAP,
    DEFAULT_REST_PORT,
    DEFAULT_STORE_NODES,
    DEFAULT_STORE_TTL_SEC,
    ENV_WAKU_BOOTSTRAP,
    ENV_WAKU_DNS_DISCOVERY_URL,
    ENV_WAKU_RUN_OWN_STORE,
    ENV_WAKU_STORE_NODE,
    LIBP2P_PUBSUB_AVAILABLE,
    MAX_WAKU_PAYLOAD_BYTES,
    WakuBinaryNotFound,
    WakuDaemonStartTimeout,
    WakuMessage,
    WakuMessageTooLarge,
    WakuNotStarted,
    WakuStatus,
    WakuStoreTTLExceeded,
    WakuTopicInvalid,
    WakuTransport,
    _bus_clear,
    build_content_topic,
    detect_nwaku_version,
    did_to_short,
    find_nwaku_binary,
    nwaku_static_download_url,
    parse_content_topic,
    resolve_bootstrap_nodes,
    resolve_store_node,
    select_transport_with_waku,
    topic_matches_peer,
)


@pytest.fixture(autouse=True)
def _clean_bus():
    _bus_clear()
    yield
    _bus_clear()


SAMPLE_DID_ALICE = "did:key:z6MkrJVnaZkeFzdQyMZu1cF5fXkVqXmTJZ5xS7aBcDeFgHiJ"
SAMPLE_DID_BOB = "did:key:z6MkuTWdJX5wYvBpZ8oTKp2Lm9NqQrStUvWxYzAbCdEfGhIj"


class TestDidToShort:
    def test_basic(self):
        s = did_to_short(SAMPLE_DID_ALICE)
        assert len(s) == 16
        assert ":" not in s
        assert s == SAMPLE_DID_ALICE.replace(":", "_").split("_")[-1][-16:]

    def test_custom_n(self):
        assert len(did_to_short(SAMPLE_DID_ALICE, n=8)) == 8

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            did_to_short("")

    def test_too_short_raises(self):
        with pytest.raises(ValueError):
            did_to_short("did:key:z6")


class TestBuildContentTopic:
    def test_basic(self):
        t = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
        assert t.startswith("/sisoul/")
        assert t.endswith("/v1/borrow")
        assert "_" in t
        p = parse_content_topic(t)
        assert p["purpose"] == "borrow"
        assert p["version"] == "v1"

    def test_wildcard(self):
        t = build_content_topic(SAMPLE_DID_ALICE, "*", "heartbeat")
        assert "_any/" in t

    def test_purpose_invalid(self):
        with pytest.raises(WakuTopicInvalid):
            build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "bad/purpose")

    def test_purpose_with_dash_ok(self):
        t = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "vault-sync")
        assert t.endswith("/vault-sync")


class TestParseContentTopic:
    def test_roundtrip(self):
        t = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "proxy")
        p = parse_content_topic(t)
        assert p["purpose"] == "proxy"
        assert p["did_a"] == did_to_short(SAMPLE_DID_ALICE)
        assert p["did_b"] == did_to_short(SAMPLE_DID_BOB)

    def test_invalid_format(self):
        with pytest.raises(WakuTopicInvalid):
            parse_content_topic("/foo/bar")

    def test_not_sisoul(self):
        with pytest.raises(WakuTopicInvalid):
            parse_content_topic("/waku/2/dm/proto")

    def test_no_underscore_in_pair(self):
        with pytest.raises(WakuTopicInvalid):
            parse_content_topic("/sisoul/nopair/v1/borrow")


class TestTopicMatchesPeer:
    def test_match_a(self):
        t = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
        alice_short = did_to_short(SAMPLE_DID_ALICE)
        assert topic_matches_peer(t, alice_short)

    def test_match_b(self):
        t = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
        bob_short = did_to_short(SAMPLE_DID_BOB)
        assert topic_matches_peer(t, bob_short)

    def test_not_match(self):
        t = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
        assert not topic_matches_peer(t, "deadbeefdeadbeef")

    def test_wildcard_match_any(self):
        t = build_content_topic(SAMPLE_DID_ALICE, "*", "heartbeat")
        assert topic_matches_peer(t, "abcdef1234567890")

    def test_invalid_topic_returns_false(self):
        assert not topic_matches_peer("/foo", "x")


class TestWakuMessage:
    def test_to_from_json_roundtrip(self):
        wm = WakuMessage(
            payload=b"hello",
            content_topic="/sisoul/a_b/v1/borrow",
            timestamp=1700000000.5,
            meta=b"thread-1",
            ephemeral=False,
        )
        j = wm.to_json()
        assert j["contentTopic"] == "/sisoul/a_b/v1/borrow"
        assert base64.b64decode(j["payload"]) == b"hello"
        assert j["timestamp"] == int(1700000000.5 * 1e9)

        wm2 = WakuMessage.from_json(j)
        assert wm2.payload == b"hello"
        assert wm2.content_topic == "/sisoul/a_b/v1/borrow"
        assert abs(wm2.timestamp - 1700000000.5) < 0.001
        assert wm2.meta == b"thread-1"

    def test_ephemeral_flag(self):
        wm = WakuMessage(payload=b"x", content_topic="/sisoul/a_b/v1/heartbeat", timestamp=time.time(), ephemeral=True)
        assert wm.to_json()["ephemeral"] is True

    def test_from_json_empty_payload(self):
        wm = WakuMessage.from_json({"contentTopic": "/sisoul/a_b/v1/borrow", "timestamp": 0})
        assert wm.payload == b""


class TestFindNwakuBinary:
    def test_no_binary_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path))
        with patch("sisoul.p2p.waku_transport.Path.home", return_value=tmp_path):
            assert find_nwaku_binary() is None

    def test_custom_path_exists(self, tmp_path):
        fake = tmp_path / "nwaku"
        fake.write_text("#!/bin/sh\necho ok\n")
        fake.chmod(0o755)
        assert find_nwaku_binary(fake) == fake

    def test_custom_path_not_exists(self, tmp_path):
        assert find_nwaku_binary(tmp_path / "nope") is None

    def test_on_path(self, tmp_path, monkeypatch):
        fake = tmp_path / "nwaku"
        fake.write_text("#!/bin/sh\necho ok\n")
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path))
        assert find_nwaku_binary() == fake


class TestDetectNwakuVersion:
    def test_real_subprocess_fail(self, tmp_path):
        assert detect_nwaku_version(tmp_path / "nope") is None

    def test_parse_version(self, tmp_path):
        fake = tmp_path / "nwaku"
        fake.write_text("#!/bin/sh\necho 'Nwaku version: v0.32.0 commit abc'\n")
        fake.chmod(0o755)
        v = detect_nwaku_version(fake)
        assert v == "v0.32.0"


class TestNwakuStaticDownloadUrl:
    def test_linux_amd64(self):
        with patch("platform.system", return_value="Linux"), patch("platform.machine", return_value="x86_64"):
            url = nwaku_static_download_url("0.32.0")
            assert "linux-amd64" in url
            assert "v0.32.0" in url

    def test_darwin_arm64(self):
        with patch("platform.system", return_value="Darwin"), patch("platform.machine", return_value="arm64"):
            url = nwaku_static_download_url()
            assert "macos-arm64" in url

    def test_unsupported_platform(self):
        with patch("platform.system", return_value="OpenBSD"):
            with pytest.raises(WakuBinaryNotFound):
                nwaku_static_download_url()

    def test_unsupported_arch(self):
        with patch("platform.system", return_value="Linux"), patch("platform.machine", return_value="riscv64"):
            with pytest.raises(WakuBinaryNotFound):
                nwaku_static_download_url()


class TestModeDecide:
    def test_explicit_mock(self):
        t = WakuTransport("test", mode="mock")
        assert t._decide_mode() == "mock"

    def test_explicit_external(self):
        t = WakuTransport("test", mode="external-daemon", external_rest_url="http://x")
        assert t._decide_mode() == "external-daemon"

    def test_auto_no_binary_no_libp2p(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path))
        with patch("sisoul.p2p.waku_transport.find_nwaku_binary", return_value=None), \
             patch("sisoul.p2p.waku_transport.LIBP2P_PUBSUB_AVAILABLE", False):
            t = WakuTransport("test")
            assert t._decide_mode() == "mock"

    def test_auto_with_libp2p(self):
        with patch("sisoul.p2p.waku_transport.find_nwaku_binary", return_value=None), \
             patch("sisoul.p2p.waku_transport.LIBP2P_PUBSUB_AVAILABLE", True):
            t = WakuTransport("test")
            assert t._decide_mode() == "libp2p-pubsub"

    def test_auto_with_binary(self, tmp_path):
        fake = tmp_path / "nwaku"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        t = WakuTransport("test", nwaku_binary=str(fake))
        assert t._decide_mode() == "nwaku-subprocess"

    def test_auto_external_url_overrides(self):
        t = WakuTransport("test", external_rest_url="http://127.0.0.1:8645")
        assert t._decide_mode() == "external-daemon"


class TestLifecycleMockMode:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        ma = await t.start()
        assert ma.startswith("waku-mock://")
        assert t.mode == "mock"
        assert t.peer_id
        await t.stop()

    @pytest.mark.asyncio
    async def test_double_start_raises(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        await t.start()
        with pytest.raises(RuntimeError):
            await t.start()
        await t.stop()

    @pytest.mark.asyncio
    async def test_stop_unstarted_noop(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        await t.stop()

    @pytest.mark.asyncio
    async def test_send_before_start_raises(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        with pytest.raises(WakuNotStarted):
            await t.send(SAMPLE_DID_BOB, b"x")

    @pytest.mark.asyncio
    async def test_recv_before_start_raises(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        with pytest.raises(WakuNotStarted):
            await t.recv(timeout=0.01)


class TestSendRecvBus:
    @pytest.mark.asyncio
    async def test_two_node_send_recv(self):
        alice = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        bob = WakuTransport("bob", my_did=SAMPLE_DID_BOB, mode="mock")
        await alice.start()
        await bob.start()
        try:
            topic_a_to_b = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
            await bob.subscribe_topic(topic_a_to_b)
            await alice.send_to_topic(topic_a_to_b, b"hello bob")
            msg = await bob.recv(timeout=2.0)
            assert msg is not None
            assert msg.payload == b"hello bob"
        finally:
            await alice.stop()
            await bob.stop()

    @pytest.mark.asyncio
    async def test_payload_too_large_raises(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        await t.start()
        try:
            big = b"x" * (MAX_WAKU_PAYLOAD_BYTES + 1)
            with pytest.raises(WakuMessageTooLarge):
                await t.send(SAMPLE_DID_BOB, big)
            with pytest.raises(WakuMessageTooLarge):
                await t.send_to_topic(
                    build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow"),
                    big,
                )
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_send_to_topic_invalid_topic(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        await t.start()
        try:
            with pytest.raises(WakuTopicInvalid):
                await t.send_to_topic("/foo/bar", b"x")
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_recv_timeout_returns_none(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        await t.start()
        try:
            assert await t.recv(timeout=0.05) is None
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_ephemeral_skips_store(self):
        alice = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        bob = WakuTransport("bob", my_did=SAMPLE_DID_BOB, mode="mock")
        await alice.start()
        await bob.start()
        try:
            topic = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "heartbeat")
            await bob.subscribe_topic(topic)
            await alice.send_to_topic(topic, b"hb", ephemeral=True)
            m = await bob.recv(timeout=1.0)
            assert m is not None
            await asyncio.sleep(0.01)
            stored = await bob.query_store(SAMPLE_DID_ALICE, since_ts=time.time() - 60, purposes=["heartbeat"])
            assert stored == []
        finally:
            await alice.stop()
            await bob.stop()


class TestStoreAndForward:
    @pytest.mark.asyncio
    async def test_query_store_ttl_exceeded(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        await t.start()
        try:
            with pytest.raises(WakuStoreTTLExceeded):
                await t.query_store(SAMPLE_DID_BOB, since_ts=time.time() - DEFAULT_STORE_TTL_SEC - 60)
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_store_offline_then_catchup(self):
        """Bob 离线时 Alice 发, Bob 上线 query_store 拉. 真 store-and-forward."""
        alice = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        await alice.start()
        try:
            topic = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
            for i in range(5):
                await alice.send_to_topic(topic, f"req-{i}".encode())

            bob = WakuTransport("bob", my_did=SAMPLE_DID_BOB, mode="mock")
            await bob.start()
            try:
                msgs = await bob.query_store(SAMPLE_DID_ALICE, since_ts=time.time() - 60, purposes=["borrow"])
                assert len(msgs) == 5
                payloads = sorted(m.payload for m in msgs)
                assert payloads == [f"req-{i}".encode() for i in range(5)]
            finally:
                await bob.stop()
        finally:
            await alice.stop()

    @pytest.mark.asyncio
    async def test_gc_store_cleans_expired(self):
        from sisoul.p2p.waku_transport import _WAKU_GLOBAL_STORE

        alice = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock", store_ttl_sec=1.0)
        await alice.start()
        try:
            topic = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
            await alice.send_to_topic(topic, b"old")
            assert sum(len(v) for v in _WAKU_GLOBAL_STORE.values()) == 1
            await asyncio.sleep(1.2)
            cleaned = await alice.gc_store()
            assert cleaned == 1
            assert sum(len(v) for v in _WAKU_GLOBAL_STORE.values()) == 0
        finally:
            await alice.stop()


class TestSubscribeRegister:
    @pytest.mark.asyncio
    async def test_register_peer(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        t.register_peer(SAMPLE_DID_BOB, "/ip4/1.2.3.4/tcp/30303")
        assert did_to_short(SAMPLE_DID_BOB) in t._known_peers

    @pytest.mark.asyncio
    async def test_subscribe_idempotent(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        await t.start()
        try:
            topic = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
            await t.subscribe_topic(topic)
            await t.subscribe_topic(topic)
            assert len(t._subscribed_topics) == 1
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_subscribe_invalid_topic_raises(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        await t.start()
        try:
            with pytest.raises(WakuTopicInvalid):
                await t.subscribe_topic("/foo")
        finally:
            await t.stop()


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_initial(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        st = t.status()
        assert isinstance(st, WakuStatus)
        assert not st.running
        assert st.sent_count == 0
        await t.start()
        try:
            st = t.status()
            assert st.running
            assert st.mode == "mock"
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_status_counts(self):
        alice = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="mock")
        bob = WakuTransport("bob", my_did=SAMPLE_DID_BOB, mode="mock")
        await alice.start()
        await bob.start()
        try:
            topic = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
            await bob.subscribe_topic(topic)
            await alice.send_to_topic(topic, b"x")
            assert alice.status().sent_count == 1
            assert bob.status().subscribed_topics == 1
        finally:
            await alice.stop()
            await bob.stop()


class TestRestPath:
    @pytest.mark.asyncio
    async def test_external_daemon_connect_fail(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="external-daemon",
                          external_rest_url="http://127.0.0.1:1")
        with pytest.raises(WakuDaemonStartTimeout):
            await t.start()

    @pytest.mark.asyncio
    async def test_external_daemon_success_mock(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="external-daemon",
                          external_rest_url="http://127.0.0.1:8645")

        fake_info = {"listenAddresses": ["/ip4/127.0.0.1/tcp/60000/p2p/16Uiu2HAmFake"]}

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=MagicMock(
            status_code=200, json=lambda: fake_info, raise_for_status=lambda: None
        ))):
            ma = await t.start()
            assert "16Uiu2HAmFake" in ma
            await t.stop()

    @pytest.mark.asyncio
    async def test_send_via_rest_mock(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="external-daemon",
                          external_rest_url="http://127.0.0.1:8645")

        fake_info = {"listenAddresses": ["/ip4/127.0.0.1/tcp/60000/p2p/16Uiu2HAmFake"]}
        get_mock = AsyncMock(return_value=MagicMock(
            status_code=200, json=lambda: fake_info, raise_for_status=lambda: None
        ))
        post_mock = AsyncMock(return_value=MagicMock(
            status_code=200, raise_for_status=lambda: None
        ))

        with patch("httpx.AsyncClient.get", new=get_mock), \
             patch("httpx.AsyncClient.post", new=post_mock):
            await t.start()
            topic = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
            await t.send_to_topic(topic, b"payload")
            assert post_mock.call_count == 1
            await t.stop()

    @pytest.mark.asyncio
    async def test_send_via_rest_http_error_raises(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="external-daemon",
                          external_rest_url="http://127.0.0.1:8645")

        fake_info = {"listenAddresses": ["/ip4/127.0.0.1/tcp/60000/p2p/16Uiu2HAmFake"]}
        get_mock = AsyncMock(return_value=MagicMock(
            status_code=200, json=lambda: fake_info, raise_for_status=lambda: None
        ))

        def raise_http(*a, **kw):
            raise httpx.HTTPError("fake net err")

        post_mock = AsyncMock(side_effect=raise_http)

        with patch("httpx.AsyncClient.get", new=get_mock), \
             patch("httpx.AsyncClient.post", new=post_mock):
            await t.start()
            topic = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
            from sisoul.p2p.waku_transport import WakuError
            with pytest.raises(WakuError):
                await t.send_to_topic(topic, b"x")
            await t.stop()

    @pytest.mark.asyncio
    async def test_query_store_via_rest_mock(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="external-daemon",
                          external_rest_url="http://127.0.0.1:8645")

        fake_info = {"listenAddresses": ["/ip4/127.0.0.1/tcp/60000/p2p/16Uiu2HAmFake"]}

        topic_b_to_a = build_content_topic(SAMPLE_DID_BOB, SAMPLE_DID_ALICE, "borrow")
        fake_msg = WakuMessage(
            payload=b"hi alice",
            content_topic=topic_b_to_a,
            timestamp=time.time(),
        )
        store_response = {"messages": [fake_msg.to_json()]}

        async def get_handler(url, *a, **kw):
            if "store" in url:
                return MagicMock(status_code=200, json=lambda: store_response, raise_for_status=lambda: None)
            return MagicMock(status_code=200, json=lambda: fake_info, raise_for_status=lambda: None)

        with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=get_handler)):
            await t.start()
            msgs = await t.query_store(SAMPLE_DID_BOB, since_ts=time.time() - 60, purposes=["borrow"])
            assert len(msgs) >= 1
            assert any(m.payload == b"hi alice" for m in msgs)
            await t.stop()


class TestNwakuSubprocess:
    @pytest.mark.asyncio
    async def test_start_no_binary_raises(self):
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="nwaku-subprocess")
        with patch("sisoul.p2p.waku_transport.find_nwaku_binary", return_value=None):
            with pytest.raises(WakuBinaryNotFound):
                await t.start()

    @pytest.mark.asyncio
    async def test_start_subprocess_proc_dies(self, tmp_path):
        fake = tmp_path / "nwaku"
        fake.write_text("#!/bin/sh\nexit 1\n")
        fake.chmod(0o755)

        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="nwaku-subprocess",
                          nwaku_binary=str(fake), startup_timeout_sec=2.0)
        with pytest.raises(WakuDaemonStartTimeout):
            await t.start()


class _FakeProc:
    """可控的假 nwaku 子进程: poll() 返 _alive 决定的退出码."""

    def __init__(self, name: str = "proc"):
        self.name = name
        self._alive = True
        self.returncode = None
        self.stderr = None
        self.terminated = False

    def poll(self):
        return None if self._alive else self.returncode

    def die(self, code: int = 137):
        self._alive = False
        self.returncode = code

    def terminate(self):
        self.terminated = True
        self.die(0)

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.die(-9)


class TestWatchdogRestart:
    """#5: nwaku 子进程 watchdog — crash 自动重启 + 重放订阅."""

    @pytest.mark.asyncio
    async def test_watchdog_restarts_dead_proc_and_resubscribes(self):
        """子进程 crash → watchdog 重启 + re-POST 订阅. restart_count++."""
        t = WakuTransport(
            "alice", my_did=SAMPLE_DID_ALICE, mode="nwaku-subprocess",
            watchdog_interval_sec=0.05, watchdog_max_restarts=5,
        )
        # 模拟已 start 状态 (跳过真 spawn): 装一个活着的 fake proc + 一些订阅.
        t._started = True
        t._mode = "nwaku-subprocess"
        first_proc = _FakeProc("p1")
        t._proc = first_proc
        t._http = MagicMock()
        topic = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
        t._subscribed_topics = {topic}

        resub_calls: list[str] = []

        async def fake_resubscribe():
            resub_calls.extend(t._subscribed_topics)

        # 重启时换上新的活 proc (模拟 _spawn_and_wait_nwaku 成功).
        new_proc = _FakeProc("p2")

        async def fake_spawn():
            t._proc = new_proc

        with patch.object(t, "_spawn_and_wait_nwaku", new=fake_spawn), \
             patch.object(t, "_resubscribe_all", new=fake_resubscribe), \
             patch.object(t, "_kill_proc", new=AsyncMock()):
            wd = asyncio.create_task(t._watchdog_loop())
            # 先确认稳态不重启
            await asyncio.sleep(0.15)
            assert t.restart_count == 0
            # 杀掉进程 → watchdog 下一轮应重启
            first_proc.die(137)
            # 等到重启发生 (轮询 restart_count)
            for _ in range(40):
                await asyncio.sleep(0.05)
                if t.restart_count >= 1:
                    break
            t._stopping = True
            wd.cancel()
            await asyncio.gather(wd, return_exceptions=True)

        assert t.restart_count == 1, "watchdog 应重启 1 次"
        assert t._proc is new_proc, "重启后应换上新 proc"
        assert topic in resub_calls, "重启后应重放订阅"

    @pytest.mark.asyncio
    async def test_watchdog_respects_max_restarts(self):
        """连续 crash 超过 max_restarts → watchdog 放弃 (不无限重启)."""
        t = WakuTransport(
            "alice", my_did=SAMPLE_DID_ALICE, mode="nwaku-subprocess",
            watchdog_interval_sec=0.02, watchdog_max_restarts=3,
            watchdog_restart_backoff_sec=0.01,
        )
        t._started = True
        t._mode = "nwaku-subprocess"
        t._proc = _FakeProc("dead")
        t._proc.die(1)  # 一开始就死
        t._http = MagicMock()

        # 每次"重启"都立刻又死 → 必撞 max_restarts 上限
        async def fake_spawn():
            p = _FakeProc("restarted")
            p.die(1)
            t._proc = p

        with patch.object(t, "_spawn_and_wait_nwaku", new=fake_spawn), \
             patch.object(t, "_resubscribe_all", new=AsyncMock()), \
             patch.object(t, "_kill_proc", new=AsyncMock()):
            await asyncio.wait_for(t._watchdog_loop(), timeout=5.0)

        # 放弃后 restart_count 恰好等于上限 (不超)
        assert t.restart_count == 3

    @pytest.mark.asyncio
    async def test_watchdog_no_restart_when_stopping(self):
        """stop() 期间 (_stopping=True) 进程死了也不重启."""
        t = WakuTransport(
            "alice", my_did=SAMPLE_DID_ALICE, mode="nwaku-subprocess",
            watchdog_interval_sec=0.02,
        )
        t._started = True
        t._mode = "nwaku-subprocess"
        dead = _FakeProc("dead")
        dead.die(0)
        t._proc = dead
        t._stopping = True  # 模拟正在 shutdown

        spawn = AsyncMock()
        with patch.object(t, "_spawn_and_wait_nwaku", new=spawn):
            # watchdog 应立刻返回 (stopping), 不调 spawn
            await asyncio.wait_for(t._watchdog_loop(), timeout=2.0)
        spawn.assert_not_called()
        assert t.restart_count == 0

    @pytest.mark.asyncio
    async def test_stop_cancels_watchdog(self):
        """stop() 取消 watchdog task 且不卡死."""
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="nwaku-subprocess")
        t._started = True
        t._mode = "nwaku-subprocess"
        t._proc = _FakeProc("alive")
        t._http = AsyncMock()
        # 手动挂一个 watchdog
        t._watchdog_task = asyncio.create_task(t._watchdog_loop())
        await asyncio.sleep(0.05)
        with patch.object(t, "_kill_proc", new=AsyncMock()):
            await t.stop()
        assert t._watchdog_task is None
        assert t._started is False
        assert t._stopping is False

    @pytest.mark.asyncio
    async def test_watchdog_disabled_no_task(self, tmp_path):
        """watchdog_enabled=False → start 后不挂 watchdog task."""
        fake = tmp_path / "nwaku"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        t = WakuTransport(
            "alice", my_did=SAMPLE_DID_ALICE, mode="mock",
            watchdog_enabled=False,
        )
        await t.start()  # mock 模式不挂 watchdog 本来就不挂, 但确认 flag 路径
        assert t._watchdog_task is None
        await t.stop()

    @pytest.mark.asyncio
    async def test_resubscribe_all_posts_each_topic(self):
        """_resubscribe_all 对每个已订阅 topic POST 一次 filter subscription."""
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="nwaku-subprocess",
                          external_rest_url=None)
        t._rest_port = 8645
        topic1 = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "borrow")
        topic2 = build_content_topic(SAMPLE_DID_ALICE, SAMPLE_DID_BOB, "heartbeat")
        t._subscribed_topics = {topic1, topic2}

        post_mock = AsyncMock(return_value=MagicMock(raise_for_status=lambda: None))
        t._http = MagicMock()
        t._http.post = post_mock
        await t._resubscribe_all()
        assert post_mock.call_count == 2
        posted_filters = [
            c.kwargs["json"]["contentFilters"][0] for c in post_mock.call_args_list
        ]
        assert set(posted_filters) == {topic1, topic2}


class TestFleetDecentralization:
    """#4: fleet 去中心化 — 可配置 bootstrap/store + ENR DNS discovery + 自跑 store.

    硬约束: 默认 (无参数/env) 行为与改动前完全一致, 现有 status.im fleet 仍工作.
    """

    def test_resolve_bootstrap_default_is_status_im(self, monkeypatch):
        monkeypatch.delenv(ENV_WAKU_BOOTSTRAP, raising=False)
        assert resolve_bootstrap_nodes() == list(DEFAULT_BOOTSTRAP)

    def test_resolve_bootstrap_explicit_wins(self, monkeypatch):
        monkeypatch.setenv(ENV_WAKU_BOOTSTRAP, "/dns4/env-node/tcp/1/p2p/16Uiu2EnvX")
        custom = ["/dns4/explicit/tcp/2/p2p/16Uiu2Explicit"]
        assert resolve_bootstrap_nodes(custom) == custom  # 显式 > env

    def test_resolve_bootstrap_env_override(self, monkeypatch):
        monkeypatch.setenv(
            ENV_WAKU_BOOTSTRAP,
            "/dns4/a/tcp/1/p2p/16Uiu2A, /dns4/b/tcp/2/p2p/16Uiu2B",
        )
        out = resolve_bootstrap_nodes()
        assert out == ["/dns4/a/tcp/1/p2p/16Uiu2A", "/dns4/b/tcp/2/p2p/16Uiu2B"]

    def test_resolve_store_default(self, monkeypatch):
        monkeypatch.delenv(ENV_WAKU_STORE_NODE, raising=False)
        assert resolve_store_node() == DEFAULT_STORE_NODES[0]

    def test_resolve_store_env_override(self, monkeypatch):
        monkeypatch.setenv(ENV_WAKU_STORE_NODE, "/dns4/mystore/tcp/3/p2p/16Uiu2Store")
        assert resolve_store_node() == "/dns4/mystore/tcp/3/p2p/16Uiu2Store"

    def test_build_args_default_unchanged(self):
        """默认 (无 dns/own-store) args 应含 --store=false 且无 dns-discovery."""
        from pathlib import Path as _P
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="nwaku-subprocess")
        args = t._build_nwaku_args(_P("/usr/bin/nwaku"))
        assert "--store=false" in args
        assert not any(a.startswith("--dns-discovery") for a in args)
        # 默认 bootstrap = status.im fleet 前 4 个
        bn_args = [a for a in args if a.startswith("--discv5-bootstrap-node=")]
        assert len(bn_args) == len(DEFAULT_BOOTSTRAP[:4])

    def test_build_args_dns_discovery_when_url_set(self):
        url = "enrtree://AOGECG@waku.example.org"
        t = WakuTransport(
            "alice", my_did=SAMPLE_DID_ALICE, mode="nwaku-subprocess",
            dns_discovery_url=url,
        )
        args = t._build_nwaku_args(__import__("pathlib").Path("/usr/bin/nwaku"))
        assert "--dns-discovery=true" in args
        assert f"--dns-discovery-url={url}" in args

    def test_build_args_run_own_store(self):
        t = WakuTransport(
            "alice", my_did=SAMPLE_DID_ALICE, mode="nwaku-subprocess",
            run_own_store=True,
        )
        args = t._build_nwaku_args(__import__("pathlib").Path("/usr/bin/nwaku"))
        assert "--store=true" in args
        assert "--store=false" not in args

    def test_env_dns_and_own_store_picked_up(self, monkeypatch):
        monkeypatch.setenv(ENV_WAKU_DNS_DISCOVERY_URL, "enrtree://ENVTREE@x.org")
        monkeypatch.setenv(ENV_WAKU_RUN_OWN_STORE, "1")
        t = WakuTransport("alice", my_did=SAMPLE_DID_ALICE, mode="nwaku-subprocess")
        args = t._build_nwaku_args(__import__("pathlib").Path("/usr/bin/nwaku"))
        assert "--dns-discovery=true" in args
        assert "--dns-discovery-url=enrtree://ENVTREE@x.org" in args
        assert "--store=true" in args

    def test_custom_bootstrap_via_constructor(self):
        custom = ["/dns4/myfleet/tcp/9/p2p/16Uiu2Mine"]
        t = WakuTransport(
            "alice", my_did=SAMPLE_DID_ALICE, mode="nwaku-subprocess",
            bootstrap_nodes=custom,
        )
        assert t._bootstrap_nodes == custom
        args = t._build_nwaku_args(__import__("pathlib").Path("/usr/bin/nwaku"))
        assert "--discv5-bootstrap-node=/dns4/myfleet/tcp/9/p2p/16Uiu2Mine" in args


class TestSelectTransportWithWaku:
    def test_default_returns_waku(self):
        t = select_transport_with_waku(node_label="alice", my_did=SAMPLE_DID_ALICE)
        assert t.name == "waku"

    def test_explicit_inmem(self):
        t = select_transport_with_waku(node_label="alice", prefer="inmem")
        assert t.name == "inmem"

    def test_invalid_prefer(self):
        with pytest.raises(ValueError):
            select_transport_with_waku(prefer="bogus")
