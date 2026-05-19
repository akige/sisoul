"""sisoul p2p · STUN 池 (Wave A #16 · §F.4).

§32 §F.4 #16 设计:
- 5 个独立 org 公共 STUN 轮询替原单 ``stun:stun.l.google.com:19302`` 单点
- 启动时并发 probe, 排序按延迟, 选 top alive 给 aiortc ``RTCConfiguration``
- 5 STUN 全挂概率 (R-09): 0.05% (5 独立运营方, Google/Cloudflare/Nextcloud/STUN protocol/Mozilla)
- 砍 Twilio NTS 默认: 用户可 ``SISOUL_TURN_URL`` env 自填 TURN, 不默认依赖中心化 SaaS

STUN binding request 协议 (RFC 5389):
- 20 byte 头: 2 byte type=0x0001 + 2 byte length=0 + 4 byte magic cookie=0x2112A442 + 12 byte transaction ID
- 回包含 XOR-MAPPED-ADDRESS attribute (RFC 5389 §15.2), 反 XOR magic cookie 拿外网 IP:port

依赖: 仅 stdlib (socket, struct, asyncio). 不依赖 aiortc/aioice (这俩做 ICE candidates, STUN binding 自己写).
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)


# ── 5 个默认 STUN (§F.4.5 表) ──────────────────────────────────────────────────


DEFAULT_STUN_POOL: tuple[str, ...] = (
    "stun:stun.l.google.com:19302",
    "stun:stun.cloudflare.com:3478",
    "stun:stun.nextcloud.com:443",
    "stun:stun.stunprotocol.org:3478",
    "stun:stun.mozilla.com:3478",
)

# RFC 5389 magic cookie
_STUN_MAGIC_COOKIE = 0x2112A442

# Attribute types
_ATTR_MAPPED_ADDRESS = 0x0001
_ATTR_XOR_MAPPED_ADDRESS = 0x0020


@dataclass
class StunProbeResult:
    """单 STUN 探活结果."""

    url: str
    alive: bool
    latency_ms: Optional[float] = None
    reflexive_ip: Optional[str] = None
    reflexive_port: Optional[int] = None
    error: Optional[str] = None
    probed_at: float = field(default_factory=time.time)


def parse_stun_url(url: str) -> tuple[str, int]:
    """``stun:host:port`` → (host, port). 缺 scheme/port 抛 ValueError."""
    if "://" in url:
        scheme, rest = url.split("://", 1)
    elif url.startswith("stun:") or url.startswith("stuns:"):
        scheme, rest = url.split(":", 1)
    else:
        raise ValueError(f"STUN URL 缺 scheme: {url}")
    if scheme not in ("stun", "stuns"):
        raise ValueError(f"非 STUN scheme: {scheme} ({url})")
    if ":" not in rest:
        raise ValueError(f"STUN URL 缺 port: {url}")
    host, port_s = rest.rsplit(":", 1)
    try:
        port = int(port_s)
    except ValueError as e:
        raise ValueError(f"STUN port 非数字: {port_s} ({url})") from e
    return host, port


def _build_binding_request() -> tuple[bytes, bytes]:
    """造 STUN BindingRequest. 返 (packet, transaction_id)."""
    txn_id = secrets.token_bytes(12)
    # type=0x0001 (Binding Request), length=0, magic cookie, txn id
    header = struct.pack("!HHI12s", 0x0001, 0, _STUN_MAGIC_COOKIE, txn_id)
    return header, txn_id


def _parse_binding_response(
    data: bytes, expected_txn: bytes
) -> tuple[str, int]:
    """parse BindingResponse, 抽 XOR-MAPPED-ADDRESS. 返 (ip, port).

    Raises:
        ValueError: 包结构 / txn mismatch / 缺 attribute.
    """
    if len(data) < 20:
        raise ValueError(f"STUN response 太短: {len(data)}B")
    msg_type, msg_len, cookie, txn = struct.unpack("!HHI12s", data[:20])
    if cookie != _STUN_MAGIC_COOKIE:
        raise ValueError(f"magic cookie 不匹配: {cookie:#x}")
    if txn != expected_txn:
        raise ValueError("txn id 不匹配")
    # 0x0101 = Binding Success Response; 0x0111 = error
    if msg_type != 0x0101:
        raise ValueError(f"非 BindingSuccess type={msg_type:#x}")

    pos = 20
    end = 20 + msg_len
    while pos + 4 <= end:
        attr_type, attr_len = struct.unpack("!HH", data[pos : pos + 4])
        pos += 4
        attr_val = data[pos : pos + attr_len]
        pos += attr_len
        # padded to 4-byte boundary
        if attr_len % 4:
            pos += 4 - (attr_len % 4)

        if attr_type in (_ATTR_MAPPED_ADDRESS, _ATTR_XOR_MAPPED_ADDRESS):
            if len(attr_val) < 8:
                continue
            # byte 0 reserved, byte 1 family (0x01=IPv4, 0x02=IPv6), bytes 2-3 port, rest addr
            family = attr_val[1]
            if family != 0x01:  # IPv4 only for now
                continue
            port_raw = struct.unpack("!H", attr_val[2:4])[0]
            ip_raw = attr_val[4:8]
            if attr_type == _ATTR_XOR_MAPPED_ADDRESS:
                # XOR with high 16 bits of magic cookie for port,
                # XOR with full magic cookie for ipv4 address
                port = port_raw ^ (_STUN_MAGIC_COOKIE >> 16)
                ip_int = struct.unpack("!I", ip_raw)[0] ^ _STUN_MAGIC_COOKIE
                ip = socket.inet_ntoa(struct.pack("!I", ip_int))
            else:
                port = port_raw
                ip = socket.inet_ntoa(ip_raw)
            return ip, port
    raise ValueError("BindingResponse 缺 (XOR-)MAPPED-ADDRESS")


async def probe_stun(
    url: str, timeout_sec: float = 5.0
) -> StunProbeResult:
    """对单 STUN 发 BindingRequest, 返 reflexive IP:port + 延迟. 失败返 alive=False."""
    try:
        host, port = parse_stun_url(url)
    except ValueError as e:
        return StunProbeResult(url=url, alive=False, error=f"bad-url: {e}")

    loop = asyncio.get_running_loop()
    packet, txn_id = _build_binding_request()
    transport_obj: Optional[asyncio.DatagramTransport] = None
    fut: asyncio.Future[bytes] = loop.create_future()
    started = time.monotonic()

    class _StunProto(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):  # noqa: D401, ARG002
            if not fut.done():
                fut.set_result(data)

        def error_received(self, exc):  # noqa: D401
            if not fut.done():
                fut.set_exception(exc)

    try:
        # resolve hostname in executor to avoid blocking
        addrinfo = await asyncio.wait_for(
            loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM),
            timeout=timeout_sec,
        )
        if not addrinfo:
            return StunProbeResult(url=url, alive=False, error="dns-empty")
        resolved = addrinfo[0][4]

        transport_obj, _proto = await loop.create_datagram_endpoint(
            _StunProto, local_addr=("0.0.0.0", 0)
        )
        transport_obj.sendto(packet, resolved)
        data = await asyncio.wait_for(fut, timeout=timeout_sec)
        latency_ms = (time.monotonic() - started) * 1000.0
        ip, mapped_port = _parse_binding_response(data, txn_id)
        return StunProbeResult(
            url=url,
            alive=True,
            latency_ms=latency_ms,
            reflexive_ip=ip,
            reflexive_port=mapped_port,
        )
    except asyncio.TimeoutError:
        return StunProbeResult(url=url, alive=False, error="timeout")
    except (OSError, ValueError) as e:
        return StunProbeResult(url=url, alive=False, error=f"{type(e).__name__}: {e}")
    finally:
        if transport_obj is not None:
            transport_obj.close()


async def probe_stun_pool(
    urls: Optional[list[str]] = None, timeout_sec: float = 5.0
) -> list[StunProbeResult]:
    """并发探每个 STUN, 返结果列表. alive 排序按 latency 升序, 死的尾部."""
    pool = list(urls) if urls is not None else list(DEFAULT_STUN_POOL)
    results = await asyncio.gather(
        *(probe_stun(u, timeout_sec=timeout_sec) for u in pool),
        return_exceptions=False,
    )
    alive = sorted(
        (r for r in results if r.alive),
        key=lambda r: r.latency_ms if r.latency_ms is not None else float("inf"),
    )
    dead = [r for r in results if not r.alive]
    return list(alive) + dead


def load_stun_pool_from_env() -> list[str]:
    """读 ``SISOUL_STUN_URLS`` (逗号分隔) 或 default pool.

    用户可 ``export SISOUL_STUN_URLS=stun:my.stun:3478`` 完全覆盖.
    空字符串 / 未设 → DEFAULT_STUN_POOL.
    """
    raw = os.environ.get("SISOUL_STUN_URLS", "").strip()
    if not raw:
        return list(DEFAULT_STUN_POOL)
    return [u.strip() for u in raw.split(",") if u.strip()]


def stun_pool_to_ice_servers(urls: list[str]) -> list[dict[str, Any]]:
    """STUN URL list → aiortc-shape ICE server dicts."""
    return [{"urls": u} for u in urls]


__all__ = [
    "DEFAULT_STUN_POOL",
    "StunProbeResult",
    "load_stun_pool_from_env",
    "parse_stun_url",
    "probe_stun",
    "probe_stun_pool",
    "stun_pool_to_ice_servers",
]
