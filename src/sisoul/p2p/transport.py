"""sisoul p2p · transport 抽象 (Phase 3 W31-W36 · 波 4 dev-A).

§28 §1.1 模块 9 · §29 §6.2 R3 (py-libp2p 不成熟 fallback WebRTC).

设计:
- 抽象 ``Transport`` ABC: ``start / stop / send / recv / peer_id``.
- 优先 ``LibP2PTransport`` (用 ``libp2p`` 库, py-libp2p PyPI 名), import / runtime 失败自动降级.
- 降级到 ``WebRTCTransport`` (用 ``aiortc``). 本环境实际跑这条 (libp2p protobuf 版本冲突).
- 最后兜底 ``InMemoryTransport`` (同进程双 node, 测试用; 也是 daemon 内 echo loopback).

模块级辅助:
- ``select_transport()`` 自动选最佳可用.
- ``LIBP2P_AVAILABLE / AIORTC_AVAILABLE`` 启动时探测 flag.

⚠️ 安全:
- ``LibP2PTransport`` / ``WebRTCTransport`` 是**裸 byte 通道**, **加密由调用方** (sync.py) 用
  ``p2p.encryption.encrypt/decrypt`` 包裹明文后再发送.
- ``WebRTCTransport`` 信令走 daemon HTTP loopback (本机) 或 manual file exchange (跨机内测).
  Phase 4 友共享上 STUN/TURN.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)


# ── 启动时探测可用性 (一次, 模块级) ─────────────────────────────────────────────


def _probe_libp2p() -> bool:
    """探 libp2p 是否可 import + 基础 API 可用.

    py-libp2p PyPI 包名是 ``libp2p`` (无连字符). 当前 (2026-05) 版本对 protobuf 6.x gencode
    跟 5.x runtime 不兼容 → 直接 import 就 raise VersionError. 探测捕获.
    """
    try:
        import libp2p  # noqa: F401 — 仅探测
        # 进一步探基础 API 存在
        from libp2p import new_host  # noqa: F401
        return True
    except Exception as e:  # noqa: BLE001 — 任何 import/runtime 失败都 fallback
        log.debug("libp2p 不可用, fallback: %s", e)
        return False


def _probe_aiortc() -> bool:
    """探 aiortc 是否可用 (WebRTC fallback)."""
    try:
        import aiortc  # noqa: F401
        from aiortc import RTCPeerConnection  # noqa: F401
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("aiortc 不可用: %s", e)
        return False


LIBP2P_AVAILABLE: bool = _probe_libp2p()
AIORTC_AVAILABLE: bool = _probe_aiortc()


# ── 数据类 ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PeerInfo:
    """已知 peer 元数据."""

    peer_id: str  # transport-specific ID (libp2p PeerID hex / WebRTC offer SDP hash / InMemory random)
    multiaddr: str  # "/ip4/127.0.0.1/tcp/9876/p2p/<peer_id>" 或 "webrtc://<sdp_hash>" 或 "inmem://<id>"
    transport: str  # "libp2p" | "webrtc" | "inmem"
    last_seen_ts: float = field(default_factory=time.time)


@dataclass
class Message:
    """发送 / 接收消息. payload 是加密后 bytes (调用方负责加解密)."""

    from_peer: str
    to_peer: str
    payload: bytes
    ts: float = field(default_factory=time.time)


# ── ABC ───────────────────────────────────────────────────────────────────────


class Transport(ABC):
    """transport 抽象基类."""

    name: str = "abstract"

    @abstractmethod
    async def start(self, port: int = 0) -> str:
        """启动. 返回本 node multiaddr."""

    @abstractmethod
    async def stop(self) -> None:
        """停止 + 清理资源."""

    @abstractmethod
    async def send(self, to_peer: str, payload: bytes) -> None:
        """发 payload 给 peer."""

    @abstractmethod
    async def recv(self, timeout: float = 1.0) -> Optional[Message]:
        """非阻塞读一条 message. 超时返 None."""

    @property
    @abstractmethod
    def peer_id(self) -> str:
        """本 node peer ID."""

    @property
    @abstractmethod
    def multiaddr(self) -> str:
        """本 node multiaddr."""


# ── InMemoryTransport (兜底 / 测试) ─────────────────────────────────────────────


# 全局 in-memory message bus (同进程多 node 互发)
_IN_MEM_BUS: dict[str, "asyncio.Queue[Message]"] = {}


class InMemoryTransport(Transport):
    """同进程 transport (测试 / daemon 内 echo).

    用一个模块级 dict 当 bus, 各 node 注册 peer_id → Queue.
    """

    name = "inmem"

    def __init__(self, node_label: str = "node") -> None:
        # peer_id 用 node_label + 启动时间 hash, 同 label 多 start 不冲突
        self._label = node_label
        self._peer_id = hashlib.sha256(
            f"{node_label}:{time.time_ns()}".encode("utf-8")
        ).hexdigest()[:16]
        self._queue: asyncio.Queue[Message] = asyncio.Queue()
        self._started = False
        self._port = 0

    async def start(self, port: int = 0) -> str:
        if self._started:
            raise RuntimeError("InMemoryTransport 已 start")
        _IN_MEM_BUS[self._peer_id] = self._queue
        self._started = True
        self._port = port
        return self.multiaddr

    async def stop(self) -> None:
        if not self._started:
            return
        _IN_MEM_BUS.pop(self._peer_id, None)
        self._started = False

    async def send(self, to_peer: str, payload: bytes) -> None:
        if not self._started:
            raise RuntimeError("transport 未 start")
        queue = _IN_MEM_BUS.get(to_peer)
        if queue is None:
            raise ConnectionError(f"peer {to_peer} 不在 in-memory bus (未 start 或已 stop)")
        msg = Message(from_peer=self._peer_id, to_peer=to_peer, payload=bytes(payload))
        await queue.put(msg)

    async def recv(self, timeout: float = 1.0) -> Optional[Message]:
        if not self._started:
            raise RuntimeError("transport 未 start")
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    @property
    def peer_id(self) -> str:
        return self._peer_id

    @property
    def multiaddr(self) -> str:
        return f"inmem://{self._peer_id}"


# ── LibP2PTransport (优先, 不成熟环境 fallback) ─────────────────────────────────


class LibP2PTransport(Transport):
    """py-libp2p (PyPI 包名 ``libp2p``) transport.

    当前 (2026-05) PyPI 上 ``libp2p`` 库对 protobuf 6.x gencode 跟 5.x runtime 不兼容,
    多数环境 import 就 raise. 本类 ``start`` 时再试一次, 失败 raise ``LibP2PUnavailable``,
    上层 ``select_transport`` 捕获 fallback WebRTC.
    """

    name = "libp2p"

    def __init__(self, node_label: str = "node") -> None:
        if not LIBP2P_AVAILABLE:
            raise LibP2PUnavailable("libp2p 不可用 (import 失败, 见 transport._probe_libp2p)")
        self._label = node_label
        self._host: Any = None
        self._peer_id_str: str = ""
        self._multiaddr_str: str = ""
        self._queue: asyncio.Queue[Message] = asyncio.Queue()
        self._started = False

    async def start(self, port: int = 0) -> str:
        # 真用 libp2p 启动 host. 本环境 protobuf 冲突会在这里抛.
        try:
            from libp2p import new_host  # type: ignore[import-not-found]
            # 简化: 用默认 transport (TCP) + 默认 muxer + 默认 security
            self._host = await new_host()  # type: ignore[misc]
            self._peer_id_str = str(self._host.get_id())
            addrs = self._host.get_addrs()
            self._multiaddr_str = str(addrs[0]) if addrs else f"/libp2p/{self._peer_id_str}"
            self._started = True
            return self._multiaddr_str
        except Exception as e:  # noqa: BLE001
            raise LibP2PUnavailable(f"libp2p start 失败: {type(e).__name__}: {e}") from e

    async def stop(self) -> None:
        if self._host is not None:
            try:
                await self._host.close()
            except Exception as e:  # noqa: BLE001
                log.warning("libp2p host close 出错: %s", e)
            self._host = None
        self._started = False

    async def send(self, to_peer: str, payload: bytes) -> None:
        # 真路径需要 stream multiplexing + protocol negotiation, 本 phase 简版
        raise LibP2PUnavailable("libp2p 真 send 未在本 phase 实现 (protobuf 冲突 + fallback 主路径已选 WebRTC)")

    async def recv(self, timeout: float = 1.0) -> Optional[Message]:
        raise LibP2PUnavailable("libp2p 真 recv 未在本 phase 实现")

    @property
    def peer_id(self) -> str:
        return self._peer_id_str

    @property
    def multiaddr(self) -> str:
        return self._multiaddr_str


class LibP2PUnavailable(RuntimeError):
    """libp2p 库不可用 / 启动失败. select_transport 捕获后 fallback."""


# ── STUN/TURN ICE servers 配置 (Wave A #16 · §F.4) ─────────────────────────────


def _default_ice_servers() -> list[dict[str, Any]]:
    """ICE server 列表 (Wave A #16 改: 5 STUN 池替单点 Google, 砍 Twilio NTS 默认).

    §32 §F.4 #16 设计:
    - 默认 5 STUN 公共池 (Google / Cloudflare / Nextcloud / STUN protocol / Mozilla)
    - 5 独立 org, 全挂概率 ~0 (R-09: P=0.05, 缓解后接近 0)
    - **TURN 默认不开** (砍 Twilio NTS 中心化依赖); 5% NAT 失败用户可:
      a. 自部 coturn 走 ``SISOUL_TURN_URL`` env 填
      b. 朋友 daemon 当 TURN relay (``sisoul peer relay-mode on``)

    env 覆盖:
      - ``SISOUL_STUN_URLS``: 逗号分隔, 完全替换 default pool
      - ``SISOUL_TURN_URL``: 单个 TURN URL (默认空)
      - ``SISOUL_TURN_USERNAME`` + ``SISOUL_TURN_CREDENTIAL``: TURN 认证 (RFC 5766)

    返回值未做 STUN 探活, 仅静态 list. 真启动时 daemon 可调 ``probe_stun_pool()``
    排序选 top alive (§F.4.3 数据流).
    """
    from sisoul.p2p.stun_pool import (
        load_stun_pool_from_env,
        stun_pool_to_ice_servers,
    )

    stun_urls = load_stun_pool_from_env()
    servers: list[dict[str, Any]] = stun_pool_to_ice_servers(stun_urls)

    # TURN: 默认空, 用户自填. 不再默认 Twilio NTS.
    turn_url = os.environ.get("SISOUL_TURN_URL", "").strip()
    if turn_url:
        entry: dict[str, Any] = {"urls": turn_url}
        username = os.environ.get("SISOUL_TURN_USERNAME")
        credential = os.environ.get("SISOUL_TURN_CREDENTIAL")
        if username:
            entry["username"] = username
        if credential:
            entry["credential"] = credential
        servers.append(entry)

    return servers


def build_rtc_configuration(ice_servers: list[dict[str, Any]] | None = None) -> Any:
    """build aiortc RTCConfiguration from dict list. 失败返 None (aiortc 缺时)."""
    if not AIORTC_AVAILABLE:
        return None
    from aiortc import RTCConfiguration, RTCIceServer  # type: ignore[import-not-found]

    cfg_servers: list[Any] = []
    for s in ice_servers or []:
        kw: dict[str, Any] = {"urls": s["urls"]}
        if "username" in s:
            kw["username"] = s["username"]
        if "credential" in s:
            kw["credential"] = s["credential"]
        cfg_servers.append(RTCIceServer(**kw))
    return RTCConfiguration(iceServers=cfg_servers) if cfg_servers else RTCConfiguration()


# ── WebRTCTransport (实际主路径, aiortc) ───────────────────────────────────────


class WebRTCTransport(Transport):
    """aiortc DataChannel transport.

    简化设计 (Phase 3 W31-W36 范围):
    - 信令通过外部 (daemon HTTP / 手动文件 / 测试 dict) 交换 SDP offer/answer.
    - DataChannel binary mode, 收消息入 Queue.
    - 本 phase 用同进程 mock signaling (test_p2p_two_instance_integration); 真 NAT 穿透 Phase 4+.

    真 NAT 穿透 / TURN / STUN: 通过 ``ice_servers`` 参数注入 (默认从 env 读).
    """

    name = "webrtc"

    def __init__(
        self,
        node_label: str = "node",
        ice_servers: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        if not AIORTC_AVAILABLE:
            raise RuntimeError("aiortc 不可用, 装 ``uv pip install aiortc`` 后再用")
        self._label = node_label
        self._peer_id_str = hashlib.sha256(
            f"webrtc:{node_label}:{time.time_ns()}".encode("utf-8")
        ).hexdigest()[:16]
        self._queue: asyncio.Queue[Message] = asyncio.Queue()
        self._pc: Any = None  # RTCPeerConnection
        self._channels: dict[str, Any] = {}  # peer_id → DataChannel
        self._started = False
        self._port = 0
        self._ice_servers = ice_servers if ice_servers is not None else _default_ice_servers()

    async def start(self, port: int = 0) -> str:
        # 本 phase aiortc 走信令简化路径 (daemon-mediated 或测试 mock).
        # RTCPeerConnection 在真 connect 时按需建.
        self._started = True
        self._port = port
        # 注册到 in-memory bus 兼容层 (同机测试用 aiortc 真 connect 太重, 用 inmem 信令路径)
        _IN_MEM_BUS[self._peer_id_str] = self._queue
        return self.multiaddr

    async def stop(self) -> None:
        if self._pc is not None:
            try:
                await self._pc.close()
            except Exception as e:  # noqa: BLE001
                log.warning("aiortc pc close 出错: %s", e)
            self._pc = None
        self._channels.clear()
        _IN_MEM_BUS.pop(self._peer_id_str, None)
        self._started = False

    async def send(self, to_peer: str, payload: bytes) -> None:
        """发 payload.

        Phase 3 W31-W36: 同机 daemon-mediated, 走 in-memory bus 路径
        (aiortc DataChannel 在 Phase 4 真 NAT 穿透时切回).
        """
        if not self._started:
            raise RuntimeError("transport 未 start")
        queue = _IN_MEM_BUS.get(to_peer)
        if queue is None:
            raise ConnectionError(
                f"peer {to_peer} 未连接 (Phase 3 走 in-memory bus, "
                f"Phase 4 切真 WebRTC NAT 穿透时改 DataChannel)"
            )
        msg = Message(from_peer=self._peer_id_str, to_peer=to_peer, payload=bytes(payload))
        await queue.put(msg)

    async def recv(self, timeout: float = 1.0) -> Optional[Message]:
        if not self._started:
            raise RuntimeError("transport 未 start")
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    @property
    def peer_id(self) -> str:
        return self._peer_id_str

    @property
    def multiaddr(self) -> str:
        return f"webrtc://{self._peer_id_str}:{self._port}"


# ── 自动选 transport ──────────────────────────────────────────────────────────


def select_transport(node_label: str = "node", prefer: Optional[str] = None) -> Transport:
    """选 transport: 优先 libp2p, fail → webrtc, fail → inmem.

    Args:
        node_label: 节点标签 (peer_id 派生用).
        prefer: 强制选 ("libp2p" | "webrtc" | "inmem"); None = 自动.

    Returns:
        Transport 实例 (未 start).

    Raises:
        RuntimeError: 全部 fallback 都不可用 (理论不可能, inmem 一定可用).
    """
    order: list[str]
    if prefer is not None:
        if prefer not in ("libp2p", "webrtc", "inmem"):
            raise ValueError(f"prefer 必须 ∈ libp2p|webrtc|inmem, 实际 {prefer}")
        order = [prefer]
    else:
        order = ["libp2p", "webrtc", "inmem"]

    last_err: Exception | None = None
    for choice in order:
        try:
            if choice == "libp2p":
                if not LIBP2P_AVAILABLE:
                    last_err = LibP2PUnavailable("libp2p 库不可用")
                    continue
                return LibP2PTransport(node_label=node_label)
            elif choice == "webrtc":
                if not AIORTC_AVAILABLE:
                    last_err = RuntimeError("aiortc 不可用")
                    continue
                return WebRTCTransport(node_label=node_label)
            elif choice == "inmem":
                return InMemoryTransport(node_label=node_label)
        except Exception as e:  # noqa: BLE001
            log.debug("select_transport %s 失败 fallback: %s", choice, e)
            last_err = e
            continue

    raise RuntimeError(
        f"全部 transport 不可用 (prefer={prefer}, last={last_err}). "
        f"装 ``uv pip install aiortc`` 启用 WebRTC fallback."
    )


__all__ = [
    "AIORTC_AVAILABLE",
    "InMemoryTransport",
    "LIBP2P_AVAILABLE",
    "LibP2PTransport",
    "LibP2PUnavailable",
    "Message",
    "PeerInfo",
    "Transport",
    "WebRTCTransport",
    "select_transport",
]
