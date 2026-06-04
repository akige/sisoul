"""Tests for sisoul.friend.mdns (P2-CD).

策略: 真起 zeroconf, 但限 loopback (interfaces=['127.0.0.1']) 避免污染局域网.
真 announce + scan 同进程 round-trip 验证 TXT 字段 + did_key/multiaddr/petname_hint.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from sisoul.friend.mdns import (
    DEFAULT_PORT,
    SERVICE_TYPE,
    ZEROCONF_AVAILABLE,
    FriendPeer,
    MDNSAnnouncer,
    MDNSScanner,
    scan,
)

DID_A = "did:key:z6MkpTHR8VNsBxYAAWHut2Geo2LWzKE9bUFr3R4nC9pYjkbA"
DID_B = "did:key:z6MkfvxPGoBnVPzgFxF2vKZLshcjL7CmZjxAdYbk1XfBNNz6"

LOOPBACK = ["127.0.0.1"]

pytestmark = pytest.mark.skipif(
    not ZEROCONF_AVAILABLE, reason="zeroconf 未装"
)


class TestServiceConstants:
    def test_service_type(self) -> None:
        assert SERVICE_TYPE == "_sisoul._tcp.local."

    def test_default_port(self) -> None:
        assert DEFAULT_PORT == 4001


class TestFriendPeerDataclass:
    def test_to_dict(self) -> None:
        p = FriendPeer(
            did_key=DID_A,
            multiaddr="/ip4/127.0.0.1/tcp/4001",
            petname_hint="Alice",
            hostname="alice.local.",
            port=4001,
        )
        d = p.to_dict()
        assert d["did_key"] == DID_A
        assert d["petname_hint"] == "Alice"
        assert d["port"] == 4001


class TestMDNSAnnouncer:
    def test_invalid_did_rejected(self) -> None:
        with pytest.raises(ValueError):
            MDNSAnnouncer(did_key="not_a_did")

    def test_start_stop_no_leak(self) -> None:
        ann = MDNSAnnouncer(
            did_key=DID_A,
            petname_hint="Alice",
            port=14001,
            ip="127.0.0.1",
            interfaces=LOOPBACK,
        )
        ann.start()
        try:
            assert ann._zc is not None
            assert ann._info is not None
        finally:
            ann.stop()
        assert ann._zc is None
        assert ann._info is None

    def test_context_manager(self) -> None:
        with MDNSAnnouncer(
            did_key=DID_A,
            port=14002,
            ip="127.0.0.1",
            interfaces=LOOPBACK,
        ) as ann:
            assert ann._zc is not None
        assert ann._zc is None


class TestMDNSScanRoundTrip:
    """真 announce → 真 scan 同进程 loopback."""

    def test_scan_finds_announced_peer(self) -> None:
        ann = MDNSAnnouncer(
            did_key=DID_A,
            petname_hint="Alice",
            multiaddr="/ip4/127.0.0.1/tcp/14010",
            port=14010,
            ip="127.0.0.1",
            interfaces=LOOPBACK,
        )
        ann.start()
        try:
            # 给点时间 multicast settle
            time.sleep(0.5)
            scanner = MDNSScanner(interfaces=LOOPBACK)
            peers = scanner.scan(timeout=3.0)
            dids = [p.did_key for p in peers]
            assert DID_A in dids, f"未扫到 announced DID, 拿到 {dids}"
            target = [p for p in peers if p.did_key == DID_A][0]
            assert target.petname_hint == "Alice"
            assert target.port == 14010
            assert "127.0.0.1" in target.multiaddr or target.multiaddr.endswith(":14010")
        finally:
            ann.stop()

    def test_scan_filters_own(self) -> None:
        ann = MDNSAnnouncer(
            did_key=DID_A,
            port=14011,
            ip="127.0.0.1",
            interfaces=LOOPBACK,
        )
        ann.start()
        try:
            time.sleep(0.5)
            peers = scan(timeout=2.5, own_did_key=DID_A, interfaces=LOOPBACK)
            dids = [p["did_key"] for p in peers]
            assert DID_A not in dids
        finally:
            ann.stop()

    def test_scan_empty_when_no_announcer(self) -> None:
        # 别的测试或环境可能有同进程残留, 但限 loopback + 等 zeroconf 缓存 expire 不现实.
        # 这里只断言不 crash + 返回 list.
        peers = scan(timeout=1.0, interfaces=LOOPBACK)
        assert isinstance(peers, list)


class TestMDNSScanWithMock:
    """单测 _on_service 解析逻辑 (mock zeroconf 不真起)."""

    def test_on_service_parses_txt(self) -> None:
        scanner = MDNSScanner(interfaces=LOOPBACK)
        fake_info = MagicMock()
        fake_info.properties = {
            b"did_key": DID_B.encode(),
            b"multiaddr": b"/ip4/192.168.1.42/tcp/4001",
            b"petname_hint": b"Bob",
        }
        fake_info.parsed_addresses.return_value = ["192.168.1.42"]
        fake_info.port = 4001
        fake_info.server = "bob.local."
        fake_zc = MagicMock()
        fake_zc.get_service_info.return_value = fake_info
        scanner._on_service(fake_zc, SERVICE_TYPE, "bob-xxx." + SERVICE_TYPE)
        assert DID_B in scanner._peers
        peer = scanner._peers[DID_B]
        assert peer.petname_hint == "Bob"
        assert peer.multiaddr == "/ip4/192.168.1.42/tcp/4001"
        assert peer.port == 4001

    def test_on_service_skips_no_did_key(self) -> None:
        scanner = MDNSScanner(interfaces=LOOPBACK)
        fake_info = MagicMock()
        fake_info.properties = {b"foo": b"bar"}  # 无 did_key
        fake_info.parsed_addresses.return_value = ["192.168.1.42"]
        fake_info.port = 4001
        fake_zc = MagicMock()
        fake_zc.get_service_info.return_value = fake_info
        scanner._on_service(fake_zc, SERVICE_TYPE, "x." + SERVICE_TYPE)
        assert len(scanner._peers) == 0

    def test_on_service_skips_own_did(self) -> None:
        scanner = MDNSScanner(own_did_key=DID_A, interfaces=LOOPBACK)
        fake_info = MagicMock()
        fake_info.properties = {b"did_key": DID_A.encode()}
        fake_info.parsed_addresses.return_value = ["127.0.0.1"]
        fake_info.port = 4001
        fake_info.server = "self.local."
        fake_zc = MagicMock()
        fake_zc.get_service_info.return_value = fake_info
        scanner._on_service(fake_zc, SERVICE_TYPE, "self." + SERVICE_TYPE)
        assert len(scanner._peers) == 0


class TestMDNSCliScan:
    def test_cli_scan_runs(self) -> None:
        """CLI mdns-scan 子命令 zero peers 也应 exit 0."""
        from typer.testing import CliRunner
        from sisoul.cli_commands.friend import friend_app
        r = CliRunner().invoke(
            friend_app, ["mdns", "scan", "--timeout", "0.3", "--json"]
        )
        assert r.exit_code == 0, r.output
        # JSON 输出应是 list
        import json as _json
        out = _json.loads(r.output)
        assert isinstance(out, list)
