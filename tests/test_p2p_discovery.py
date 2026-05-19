"""测试 p2p.discovery — Manual / mDNS / DHT / Composite (波 4 dev-A)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sisoul.p2p.discovery import (
    DHTDiscoverer,
    Discoverer,
    ManualDiscoverer,
    CompositeDiscoverer,
    build_default_discoverer,
)
from sisoul.p2p.transport import PeerInfo


# ── ManualDiscoverer ─────────────────────────────────────────────────────────


class TestManualDiscoverer:
    async def test_basic_add_list(self, tmp_path):
        d = ManualDiscoverer(store_path=tmp_path / "peers.json")
        await d.start()
        d.add_peer(PeerInfo(peer_id="alice", multiaddr="inmem://alice", transport="inmem"))
        d.add_peer(PeerInfo(peer_id="bob", multiaddr="inmem://bob", transport="inmem"))
        peers = d.list_peers()
        assert len(peers) == 2
        ids = {p.peer_id for p in peers}
        assert ids == {"alice", "bob"}
        await d.stop()

    async def test_persistence_across_restart(self, tmp_path):
        store = tmp_path / "peers.json"
        d1 = ManualDiscoverer(store_path=store)
        d1.add_peer(PeerInfo(peer_id="x", multiaddr="m://x", transport="manual"))
        assert store.exists()
        # 重新加载
        d2 = ManualDiscoverer(store_path=store)
        peers = d2.list_peers()
        assert len(peers) == 1
        assert peers[0].peer_id == "x"

    async def test_remove_peer(self, tmp_path):
        d = ManualDiscoverer(store_path=tmp_path / "peers.json")
        d.add_peer(PeerInfo(peer_id="a", multiaddr="m://a", transport="manual"))
        assert d.remove_peer("a") is True
        assert d.remove_peer("a") is False
        assert d.list_peers() == []

    async def test_no_store_path_works_in_memory(self):
        d = ManualDiscoverer(store_path=None)
        d.add_peer(PeerInfo(peer_id="m", multiaddr="m://m", transport="manual"))
        assert len(d.list_peers()) == 1
        # save 是 no-op
        d._save()  # noqa: SLF001

    async def test_load_corrupted_json_does_not_raise(self, tmp_path, caplog):
        store = tmp_path / "peers.json"
        store.write_text("not json", encoding="utf-8")
        d = ManualDiscoverer(store_path=store)
        # 加载失败 log warning, 不 raise
        assert d.list_peers() == []


# ── DHTDiscoverer (noop 当前) ────────────────────────────────────────────────


class TestDHTDiscoverer:
    async def test_noop_when_libp2p_unavailable(self):
        d = DHTDiscoverer()
        await d.start()
        assert d.list_peers() == []
        await d.stop()


# ── CompositeDiscoverer ─────────────────────────────────────────────────────


class TestCompositeDiscoverer:
    async def test_aggregate_peers(self, tmp_path):
        m1 = ManualDiscoverer(store_path=tmp_path / "p1.json")
        m1.add_peer(PeerInfo(peer_id="a", multiaddr="m://a", transport="manual"))
        m2 = ManualDiscoverer(store_path=tmp_path / "p2.json")
        m2.add_peer(PeerInfo(peer_id="b", multiaddr="m://b", transport="manual"))
        c = CompositeDiscoverer([m1, m2])
        await c.start()
        ids = {p.peer_id for p in c.list_peers()}
        assert ids == {"a", "b"}
        await c.stop()

    async def test_empty_list_raises(self):
        with pytest.raises(ValueError):
            CompositeDiscoverer([])

    async def test_add_peer_goes_to_manual(self, tmp_path):
        m = ManualDiscoverer(store_path=tmp_path / "p.json")
        d = DHTDiscoverer()
        c = CompositeDiscoverer([d, m])
        c.add_peer(PeerInfo(peer_id="x", multiaddr="m://x", transport="manual"))
        assert any(p.peer_id == "x" for p in c.list_peers())

    async def test_add_peer_without_manual_raises(self):
        c = CompositeDiscoverer([DHTDiscoverer()])
        with pytest.raises(RuntimeError):
            c.add_peer(PeerInfo(peer_id="x", multiaddr="m://x", transport="any"))


# ── build_default_discoverer ────────────────────────────────────────────────


class TestBuildDefault:
    async def test_always_has_manual(self, tmp_path):
        c = build_default_discoverer(
            my_peer_id="me",
            my_port=12345,
            manual_store_path=tmp_path / "peers.json",
            enable_mdns=False,
        )
        # 至少含 ManualDiscoverer
        has_manual = any(isinstance(d, ManualDiscoverer) for d in c._discoverers)  # noqa: SLF001
        assert has_manual

    async def test_can_disable_mdns(self, tmp_path):
        c = build_default_discoverer(
            my_peer_id="me",
            my_port=0,
            manual_store_path=tmp_path / "p.json",
            enable_mdns=False,
        )
        # 无 mDNS
        from sisoul.p2p.discovery import MDNSDiscoverer
        assert not any(isinstance(d, MDNSDiscoverer) for d in c._discoverers)  # noqa: SLF001
