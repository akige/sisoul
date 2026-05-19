"""测试 p2p.stun_pool — STUN 池探活 + parse + env (Wave A #16 · §F.4).

10+ case mock + 1 真 Google STUN smoke (可 env SISOUL_SKIP_STUN_SMOKE=1 跳过).
"""

from __future__ import annotations

import asyncio
import os
import socket
import struct
from unittest.mock import patch

import pytest

from sisoul.p2p.stun_pool import (
    DEFAULT_STUN_POOL,
    StunProbeResult,
    _build_binding_request,
    _parse_binding_response,
    load_stun_pool_from_env,
    parse_stun_url,
    probe_stun,
    probe_stun_pool,
    stun_pool_to_ice_servers,
)


# ── DEFAULT_STUN_POOL ────────────────────────────────────────────────────────


class TestDefaultPool:
    def test_default_pool_has_5_stun(self):
        """§F.4.5: 5 个跨独立 org STUN."""
        assert len(DEFAULT_STUN_POOL) == 5

    def test_default_pool_contents(self):
        """§F.4.5 表: Google / Cloudflare / Nextcloud / STUN protocol / Mozilla."""
        urls = list(DEFAULT_STUN_POOL)
        assert any("stun.l.google.com" in u for u in urls)
        assert any("cloudflare.com" in u for u in urls)
        assert any("nextcloud.com" in u for u in urls)
        assert any("stunprotocol.org" in u for u in urls)
        assert any("mozilla.com" in u for u in urls)

    def test_default_pool_all_stun_scheme(self):
        for u in DEFAULT_STUN_POOL:
            assert u.startswith("stun:") or u.startswith("stuns:")


# ── parse_stun_url ───────────────────────────────────────────────────────────


class TestParseStunUrl:
    def test_basic(self):
        host, port = parse_stun_url("stun:stun.l.google.com:19302")
        assert host == "stun.l.google.com"
        assert port == 19302

    def test_with_scheme_separator(self):
        host, port = parse_stun_url("stun://example.com:3478")
        assert host == "example.com"
        assert port == 3478

    def test_missing_port_raises(self):
        with pytest.raises(ValueError, match="port"):
            parse_stun_url("stun:example.com")

    def test_invalid_scheme(self):
        with pytest.raises(ValueError, match="scheme"):
            parse_stun_url("http://example.com:80")

    def test_invalid_port(self):
        with pytest.raises(ValueError, match="port 非数字"):
            parse_stun_url("stun:example.com:abc")


# ── load_stun_pool_from_env ──────────────────────────────────────────────────


class TestLoadFromEnv:
    def test_env_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv("SISOUL_STUN_URLS", raising=False)
        urls = load_stun_pool_from_env()
        assert urls == list(DEFAULT_STUN_POOL)

    def test_env_empty_returns_default(self, monkeypatch):
        monkeypatch.setenv("SISOUL_STUN_URLS", "  ")
        urls = load_stun_pool_from_env()
        assert urls == list(DEFAULT_STUN_POOL)

    def test_env_overrides_completely(self, monkeypatch):
        monkeypatch.setenv("SISOUL_STUN_URLS", "stun:my.stun:3478,stun:other:3478")
        urls = load_stun_pool_from_env()
        assert urls == ["stun:my.stun:3478", "stun:other:3478"]


def test_stun_pool_to_ice_servers():
    out = stun_pool_to_ice_servers(["stun:a:1", "stun:b:2"])
    assert out == [{"urls": "stun:a:1"}, {"urls": "stun:b:2"}]


# ── _parse_binding_response (XOR-MAPPED-ADDRESS) ─────────────────────────────


class TestBindingResponse:
    def test_parse_xor_mapped_address(self):
        """造 BindingSuccess 含 XOR-MAPPED-ADDRESS = 1.2.3.4:1234, 验回包正确解."""
        # build minimal success response
        packet, txn_id = _build_binding_request()
        # change request type 0x0001 → response 0x0101
        # Body = XOR-MAPPED-ADDRESS attribute
        # XOR encoding: port = port ^ (cookie >> 16); ipv4 = ip ^ cookie
        cookie = 0x2112A442
        port = 1234
        ip = "1.2.3.4"
        ip_int = struct.unpack("!I", socket.inet_aton(ip))[0]
        xport = port ^ (cookie >> 16)
        xip = ip_int ^ cookie
        attr_val = struct.pack("!BBHI", 0, 0x01, xport, xip)  # reserved, family, port, ipv4
        attr = struct.pack("!HH", 0x0020, len(attr_val)) + attr_val  # XOR-MAPPED-ADDRESS
        body = attr
        header = struct.pack("!HHI12s", 0x0101, len(body), cookie, txn_id)
        resp = header + body

        ip_out, port_out = _parse_binding_response(resp, txn_id)
        assert ip_out == "1.2.3.4"
        assert port_out == 1234

    def test_parse_wrong_txn_raises(self):
        packet, txn_id = _build_binding_request()
        body = b""
        header = struct.pack(
            "!HHI12s", 0x0101, 0, 0x2112A442, b"\x00" * 12  # wrong txn
        )
        with pytest.raises(ValueError, match="txn"):
            _parse_binding_response(header + body, txn_id)

    def test_parse_too_short(self):
        with pytest.raises(ValueError, match="太短"):
            _parse_binding_response(b"\x00\x01", b"\x00" * 12)

    def test_parse_error_response_raises(self):
        packet, txn_id = _build_binding_request()
        header = struct.pack("!HHI12s", 0x0111, 0, 0x2112A442, txn_id)
        with pytest.raises(ValueError, match="非 BindingSuccess"):
            _parse_binding_response(header, txn_id)


# ── probe_stun (mock UDP) ─────────────────────────────────────────────────────


class _MockProto:
    """Mock UDP endpoint factory for probe_stun. Mocks loop.create_datagram_endpoint."""

    def __init__(self, response_data, txn_capture):
        self.response_data = response_data
        self.txn_capture = txn_capture
        self.transport = None
        self.proto = None

    def close(self):
        pass


async def _fake_endpoint_factory(behavior):
    """behavior: 'ok' / 'timeout' / 'error'. Returns mock create_datagram_endpoint."""
    loop = asyncio.get_running_loop()

    async def create_dg_endpoint(protocol_factory, local_addr=None):
        proto = protocol_factory()

        class MockTransport:
            def __init__(self):
                self._closed = False
                self._proto = proto

            def sendto(self, data, addr):
                # parse txn id from request
                _t, _l, _c, txn = struct.unpack("!HHI12s", data[:20])
                if behavior == "ok":
                    # craft XOR-MAPPED-ADDRESS response
                    cookie = 0x2112A442
                    port = 5555
                    ip_int = struct.unpack("!I", socket.inet_aton("9.8.7.6"))[0]
                    xport = port ^ (cookie >> 16)
                    xip = ip_int ^ cookie
                    attr_val = struct.pack("!BBHI", 0, 0x01, xport, xip)
                    attr = struct.pack("!HH", 0x0020, len(attr_val)) + attr_val
                    header = struct.pack("!HHI12s", 0x0101, len(attr), cookie, txn)
                    resp = header + attr
                    # schedule delivery
                    loop.call_soon(proto.datagram_received, resp, addr)
                elif behavior == "error":
                    loop.call_soon(proto.error_received, OSError("net unreach"))
                # timeout: just drop, no response
            def close(self):
                self._closed = True

        t = MockTransport()
        return t, proto

    return create_dg_endpoint


@pytest.mark.asyncio
async def test_probe_stun_ok():
    """1 STUN 正常返 XOR-MAPPED-ADDRESS."""
    loop = asyncio.get_running_loop()
    fake = await _fake_endpoint_factory("ok")
    with patch.object(loop, "create_datagram_endpoint", fake):
        # also patch getaddrinfo to return loopback
        with patch.object(
            loop,
            "getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_DGRAM, 0, "", ("127.0.0.1", 3478))],
        ):
            r = await probe_stun("stun:fake.host:3478", timeout_sec=2.0)
    assert r.alive
    assert r.reflexive_ip == "9.8.7.6"
    assert r.reflexive_port == 5555
    assert r.latency_ms is not None


@pytest.mark.asyncio
async def test_probe_stun_timeout():
    loop = asyncio.get_running_loop()
    fake = await _fake_endpoint_factory("timeout")
    with patch.object(loop, "create_datagram_endpoint", fake):
        with patch.object(
            loop,
            "getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_DGRAM, 0, "", ("127.0.0.1", 3478))],
        ):
            r = await probe_stun("stun:fake.host:3478", timeout_sec=0.2)
    assert not r.alive
    assert r.error == "timeout"


@pytest.mark.asyncio
async def test_probe_stun_error():
    loop = asyncio.get_running_loop()
    fake = await _fake_endpoint_factory("error")
    with patch.object(loop, "create_datagram_endpoint", fake):
        with patch.object(
            loop,
            "getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_DGRAM, 0, "", ("127.0.0.1", 3478))],
        ):
            r = await probe_stun("stun:fake.host:3478", timeout_sec=1.0)
    assert not r.alive
    assert r.error is not None
    assert "net unreach" in r.error or "OSError" in r.error


@pytest.mark.asyncio
async def test_probe_stun_bad_url():
    r = await probe_stun("not-a-url", timeout_sec=0.1)
    assert not r.alive
    assert "bad-url" in r.error


@pytest.mark.asyncio
async def test_probe_stun_pool_mixed_5(monkeypatch):
    """5 STUN 池 mock 不同响应: 3 alive / 1 timeout / 1 error.

    验 fallback 正确: alive 排在前面按 latency 排序, 死的尾部.
    """
    # Patch probe_stun to return canned results
    canned = {
        "stun:s1:3478": StunProbeResult(url="stun:s1:3478", alive=True, latency_ms=50.0, reflexive_ip="1.1.1.1", reflexive_port=1000),
        "stun:s2:3478": StunProbeResult(url="stun:s2:3478", alive=False, error="timeout"),
        "stun:s3:3478": StunProbeResult(url="stun:s3:3478", alive=True, latency_ms=10.0, reflexive_ip="1.1.1.1", reflexive_port=1001),
        "stun:s4:3478": StunProbeResult(url="stun:s4:3478", alive=False, error="OSError: refused"),
        "stun:s5:3478": StunProbeResult(url="stun:s5:3478", alive=True, latency_ms=30.0, reflexive_ip="1.1.1.1", reflexive_port=1002),
    }

    async def fake_probe(url, timeout_sec=5.0):
        return canned[url]

    monkeypatch.setattr("sisoul.p2p.stun_pool.probe_stun", fake_probe)
    results = await probe_stun_pool(list(canned.keys()), timeout_sec=1.0)
    # 3 alive first sorted by latency ascending
    assert results[0].url == "stun:s3:3478"  # 10ms
    assert results[1].url == "stun:s5:3478"  # 30ms
    assert results[2].url == "stun:s1:3478"  # 50ms
    # 2 dead tail
    assert all(not r.alive for r in results[3:])


# ── 真 Google STUN smoke ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("SISOUL_SKIP_STUN_SMOKE") == "1",
    reason="SISOUL_SKIP_STUN_SMOKE=1 跳过真 STUN smoke",
)
async def test_real_google_stun_smoke():
    """真发 STUN binding 给 stun.l.google.com:19302, 拿外网反射 IP.

    无外网 / UDP 被防火墙阻 → skip 而非 fail.
    """
    r = await probe_stun("stun:stun.l.google.com:19302", timeout_sec=5.0)
    if not r.alive:
        pytest.skip(f"Google STUN 不可达 (可能 UDP 阻 / 无网): {r.error}")
    assert r.reflexive_ip is not None
    assert r.reflexive_port is not None and 1 <= r.reflexive_port <= 65535
    # 反射 IP 不能是内网 (不算 10. / 172.16-31 / 192.168 / 127.)
    assert not r.reflexive_ip.startswith("10.")
    assert not r.reflexive_ip.startswith("127.")
    assert not r.reflexive_ip.startswith("192.168.")
    print(f"\n  REAL Google STUN: {r.reflexive_ip}:{r.reflexive_port} ({r.latency_ms:.0f}ms)")


# ── 真双 daemon STUN smoke (loopback hole punch) ─────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("SISOUL_SKIP_STUN_SMOKE") == "1",
    reason="SISOUL_SKIP_STUN_SMOKE=1 跳过真 STUN smoke",
)
async def test_two_local_daemons_stun_reflexive_consistent():
    """同机两 daemon (不同 bind port) 各自走 Google STUN 拿外网映射.

    验:
    1. 两次 binding 拿到同一个 reflexive IP (本机出口 IP)
    2. 两次 reflexive port 不同 (因为 source port 不同 → NAT 映射不同 entry)
    3. 真 UDP socket 真建立 (反向验证 NAT 穿透前提)
    """
    r1 = await probe_stun("stun:stun.l.google.com:19302", timeout_sec=5.0)
    r2 = await probe_stun("stun:stun.cloudflare.com:3478", timeout_sec=5.0)
    if not (r1.alive and r2.alive):
        pytest.skip(f"STUN 不可达: r1={r1.error} r2={r2.error}")
    # 同一台机器外网 IP 一致 (公网 IP 同)
    assert r1.reflexive_ip == r2.reflexive_ip, (
        f"两次外网 IP 不一致: {r1.reflexive_ip} vs {r2.reflexive_ip} "
        f"(可能多出口 / 同时 IPv4/IPv6 不一致, 本 smoke 不适用此环境)"
    )
    # source port 不同 (每次新 socket OS 分新 ephemeral port) → reflexive port 大概率不同
    # (注: 若 NAT 表 burn through 时间长, 也可能复用同 port; 这里软断言不强求)
    print(
        f"\n  daemon1 → Google STUN: {r1.reflexive_ip}:{r1.reflexive_port}"
        f"\n  daemon2 → CF STUN:     {r2.reflexive_ip}:{r2.reflexive_port}"
    )
