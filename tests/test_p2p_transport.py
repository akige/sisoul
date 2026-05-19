"""测试 p2p.transport — libp2p / WebRTC / InMemory 选择 + 收发 (波 4 dev-A)."""

from __future__ import annotations

import asyncio

import pytest

from sisoul.p2p.transport import (
    AIORTC_AVAILABLE,
    LIBP2P_AVAILABLE,
    InMemoryTransport,
    LibP2PTransport,
    LibP2PUnavailable,
    Message,
    PeerInfo,
    Transport,
    WebRTCTransport,
    select_transport,
)


# ── PeerInfo / Message dataclass ─────────────────────────────────────────────


class TestDataclasses:
    def test_peer_info_default_ts(self):
        p = PeerInfo(peer_id="abc", multiaddr="inmem://abc", transport="inmem")
        assert p.last_seen_ts > 0

    def test_message_default_ts(self):
        m = Message(from_peer="a", to_peer="b", payload=b"x")
        assert m.ts > 0


# ── InMemoryTransport ────────────────────────────────────────────────────────


class TestInMemoryTransport:
    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        t = InMemoryTransport(node_label="alice")
        ma = await t.start()
        assert ma.startswith("inmem://")
        assert t.peer_id
        await t.stop()

    @pytest.mark.asyncio
    async def test_double_start_raises(self):
        t = InMemoryTransport(node_label="x")
        await t.start()
        with pytest.raises(RuntimeError):
            await t.start()
        await t.stop()

    @pytest.mark.asyncio
    async def test_send_to_unstarted_raises(self):
        t = InMemoryTransport(node_label="x")
        with pytest.raises(RuntimeError):
            await t.send("nobody", b"x")

    @pytest.mark.asyncio
    async def test_recv_to_unstarted_raises(self):
        t = InMemoryTransport(node_label="x")
        with pytest.raises(RuntimeError):
            await t.recv(timeout=0.1)

    @pytest.mark.asyncio
    async def test_two_nodes_send_recv(self):
        alice = InMemoryTransport(node_label="alice")
        bob = InMemoryTransport(node_label="bob")
        await alice.start()
        await bob.start()
        try:
            await alice.send(bob.peer_id, b"hello bob")
            msg = await bob.recv(timeout=1.0)
            assert msg is not None
            assert msg.from_peer == alice.peer_id
            assert msg.payload == b"hello bob"
        finally:
            await alice.stop()
            await bob.stop()

    @pytest.mark.asyncio
    async def test_send_to_unknown_peer_raises(self):
        a = InMemoryTransport(node_label="a")
        await a.start()
        try:
            with pytest.raises(ConnectionError):
                await a.send("not-a-real-peer", b"x")
        finally:
            await a.stop()

    @pytest.mark.asyncio
    async def test_recv_timeout_returns_none(self):
        a = InMemoryTransport(node_label="a")
        await a.start()
        try:
            msg = await a.recv(timeout=0.1)
            assert msg is None
        finally:
            await a.stop()


# ── WebRTCTransport (only if aiortc installed) ───────────────────────────────


@pytest.mark.skipif(not AIORTC_AVAILABLE, reason="aiortc 未装")
class TestWebRTCTransport:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        t = WebRTCTransport(node_label="webrtc-a")
        ma = await t.start(port=12345)
        assert ma.startswith("webrtc://")
        assert "12345" in ma
        await t.stop()

    @pytest.mark.asyncio
    async def test_send_via_inmem_signaling(self):
        """Phase 3 同机 daemon-mediated, 走 in-memory bus (跟 InMemoryTransport 同 bus)."""
        alice = WebRTCTransport(node_label="alice")
        bob = WebRTCTransport(node_label="bob")
        await alice.start()
        await bob.start()
        try:
            await alice.send(bob.peer_id, b"webrtc payload")
            msg = await bob.recv(timeout=1.0)
            assert msg is not None
            assert msg.payload == b"webrtc payload"
        finally:
            await alice.stop()
            await bob.stop()


# ── LibP2PTransport (skip if unavailable) ────────────────────────────────────


class TestLibP2PTransport:
    @pytest.mark.skipif(LIBP2P_AVAILABLE, reason="libp2p 库可用 (当前环境不会触发 fallback)")
    def test_unavailable_raises_on_construct(self):
        with pytest.raises(LibP2PUnavailable):
            LibP2PTransport(node_label="x")


# ── select_transport ─────────────────────────────────────────────────────────


class TestSelectTransport:
    def test_auto_select_returns_transport(self):
        t = select_transport(node_label="sel-1")
        assert isinstance(t, Transport)
        # 当前环境 libp2p 不可用, aiortc 可用 → webrtc
        if not LIBP2P_AVAILABLE and AIORTC_AVAILABLE:
            assert t.name == "webrtc"
        elif not LIBP2P_AVAILABLE and not AIORTC_AVAILABLE:
            assert t.name == "inmem"

    def test_prefer_inmem_explicit(self):
        t = select_transport(node_label="sel-2", prefer="inmem")
        assert t.name == "inmem"

    def test_prefer_invalid_raises(self):
        with pytest.raises(ValueError):
            select_transport(prefer="nonsense")

    @pytest.mark.skipif(not AIORTC_AVAILABLE, reason="aiortc 未装")
    def test_prefer_webrtc(self):
        t = select_transport(node_label="sel-3", prefer="webrtc")
        assert t.name == "webrtc"

    def test_libp2p_fallback_when_unavailable(self, monkeypatch):
        """模拟 libp2p / aiortc 全不可用 → 最终 fallback inmem."""
        import sisoul.p2p.transport as tmod
        monkeypatch.setattr(tmod, "LIBP2P_AVAILABLE", False)
        monkeypatch.setattr(tmod, "AIORTC_AVAILABLE", False)
        t = tmod.select_transport(node_label="fb")
        assert t.name == "inmem"


# pytest-asyncio: asyncio_mode=auto 在 pyproject [tool.pytest.ini_options] 配
