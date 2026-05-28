"""sisoul p2p · WakuTransport (Wave B' agent-B1 · §32 §F.1 #9 · §36 P0-2).

Waku v2 store-and-forward transport, 替 in-mem bus + WebRTC daemon-mediated 简化路径.

# 设计 (§32 §F.1 / §36 P0-2)

Waku v2 (https://waku.org) 是 libp2p 上的 4 协议消息层:
- **Relay** (waku/2/relay/0.0.0): GossipSub pubsub, 在线双方实时投递
- **Store** (waku/2/store/0.0.0): 离线消息缓存, 公共 store node TTL 24-72h (sisoul 朋友 daemon
  互转兜底)
- **Filter** (waku/2/filter/0.0.0): 资源受限节点订指定 content topic
- **Lightpush** (waku/2/lightpush/0.0.0): 资源受限节点不入 relay mesh, push 1 条到全网

sisoul daemon 内嵌 ``nwaku`` (Nim, 主) 或 ``go-waku`` (Go) binary subprocess, REST API
(:8645 默认), Python 走 httpx 调. 跟 Wave A #6 kubo 同模式.

# 4 mode (按优先级)

1. ``nwaku-subprocess``: PATH 上 ``nwaku`` / ``waku`` / ``go-waku`` 命中 → fork daemon
2. ``external-daemon``: 用户已自跑 nwaku/go-waku, 配 ``external_rest_url=http://127.0.0.1:8645``
3. ``libp2p-pubsub``: 全没装 → 退化纯 libp2p GossipSub (py-libp2p, protobuf 不兼容则继续退)
4. ``mock``: 全没 → 同进程 bus, dev/test 兜底

# Content topic 路由

DID-to-DID: ``/sisoul/<did_alice_short>_<did_bob_short>/v1/<purpose>``
- ``did_alice_short`` = ``did:key:z6Mk...`` 末 16 字符 (省 topic 长度, 双方算法一致)
- ``purpose`` = ``borrow | proxy | ledger | heartbeat | vault-sync``

# Store-and-forward

Alice send to Bob (Bob 离线):
1. ``send()`` → Relay PubSub publish
2. 公共 store node (沿 PubSub mesh 自动订 topic) 收到 → INSERT INTO waku_messages
3. Bob 上线 → ``query_store(peer_id=bob, since_ts=last_sync)`` →
   收所有 ``/sisoul/*_<bob_did_short>/*`` topic 累计 message

TTL 24h (本模块强约束, 比 Waku 公共 fleet 30d 更保守, 避免占空间).

# 加密

⚠️ WakuTransport 是 **裸 byte 通道**, 加密由上层 (sync.py / friend/encrypted_proxy.py)
用 libsodium Box 包裹明文后再 send. payload bytes 直进 WakuMessage.payload, content topic
本身明文 (Waku 看流量但不知内容).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import time
import urllib.parse
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import httpx

from sisoul.p2p.transport import Message, Transport

log = logging.getLogger(__name__)


# ── 常量 ──────────────────────────────────────────────────────────────────────


WakuMode = Literal["nwaku-subprocess", "external-daemon", "libp2p-pubsub", "mock"]

DEFAULT_REST_PORT = 8645
DEFAULT_P2P_TCP_PORT = 60000
DEFAULT_DISCV5_UDP_PORT = 9000

NWAKU_DEFAULT_VERSION = "0.32.0"
NWAKU_DIST_BASE = "https://github.com/waku-org/nwaku/releases/download"

DEFAULT_BOOTSTRAP: tuple[str, ...] = (
    "/dns4/node-01.do-ams3.waku.sandbox.status.im/tcp/30303/p2p/16Uiu2HAmNaeL4p3WEYzC9mgXBmBWSgWjPHRvatZTXnp8Jgv3iKsb",
    "/dns4/node-02.do-ams3.waku.sandbox.status.im/tcp/30303/p2p/16Uiu2HAmRSxJZxJ9HG7TtVrSMBhJqcjyDfL2sUbCqXmTwAaXM1KZ",
    "/dns4/node-01.gc-us-central1-a.waku.sandbox.status.im/tcp/30303/p2p/16Uiu2HAm9w9eP9Q5BkGfRgWwSpHFq8RR7Eze7q5y3aMRJxBpfHfP",
    "/dns4/node-01.ac-cn-hongkong-c.waku.sandbox.status.im/tcp/30303/p2p/16Uiu2HAmHFypwbA9zKqYjTpDqFvJpVqzqAUqVfzfvT5gG4hPmvND",
)

DEFAULT_STORE_NODES: tuple[str, ...] = (
    "/dns4/node-01.do-ams3.waku.sandbox.status.im/tcp/30303/p2p/16Uiu2HAmNaeL4p3WEYzC9mgXBmBWSgWjPHRvatZTXnp8Jgv3iKsb",
    "/dns4/node-02.do-ams3.waku.sandbox.status.im/tcp/30303/p2p/16Uiu2HAmRSxJZxJ9HG7TtVrSMBhJqcjyDfL2sUbCqXmTwAaXM1KZ",
)

DEFAULT_PUBSUB_TOPIC = "/waku/2/default-waku/proto"
SISOUL_CONTENT_TOPIC_PREFIX = "/sisoul"
SISOUL_CONTENT_TOPIC_VERSION = "v1"

MAX_WAKU_PAYLOAD_BYTES = 1 * 1024 * 1024
DEFAULT_STORE_TTL_SEC = 24 * 3600
DEFAULT_STARTUP_TIMEOUT_SEC = 30.0
DEFAULT_SHUTDOWN_GRACE_SEC = 5.0
DEFAULT_HTTP_TIMEOUT_SEC = 30.0
DEFAULT_RECV_QUEUE_SIZE = 10000
DEFAULT_FILTER_LONG_POLL_SEC = 5.0


# ── 异常 ──────────────────────────────────────────────────────────────────────


class WakuError(Exception):
    code: int = 7000


class WakuNotStarted(WakuError):
    code = 7001


class WakuBinaryNotFound(WakuError):
    code = 7002


class WakuDaemonStartTimeout(WakuError):
    code = 7003


class WakuMessageTooLarge(WakuError):
    code = 7004


class WakuStoreTTLExceeded(WakuError):
    code = 7005


class WakuTopicInvalid(WakuError):
    code = 7006


class WakuPeerNotFound(WakuError):
    code = 7007


# ── 数据类 ────────────────────────────────────────────────────────────────────


@dataclass
class WakuMessage:
    """Waku v2 wire format (https://rfc.vac.dev/spec/14/)."""

    payload: bytes
    content_topic: str
    timestamp: float
    meta: Optional[bytes] = None
    version: int = 0
    ephemeral: bool = False

    def to_json(self) -> dict[str, Any]:
        import base64

        d: dict[str, Any] = {
            "payload": base64.b64encode(self.payload).decode("ascii"),
            "contentTopic": self.content_topic,
            "timestamp": int(self.timestamp * 1e9),
            "version": self.version,
        }
        if self.meta is not None:
            d["meta"] = base64.b64encode(self.meta).decode("ascii")
        if self.ephemeral:
            d["ephemeral"] = True
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "WakuMessage":
        import base64

        payload_b64 = d.get("payload", "")
        meta_b64 = d.get("meta")
        ts_ns = d.get("timestamp", 0)
        ts = ts_ns / 1e9 if ts_ns > 1e12 else float(ts_ns)
        return cls(
            payload=base64.b64decode(payload_b64) if payload_b64 else b"",
            content_topic=d.get("contentTopic", ""),
            timestamp=ts,
            meta=base64.b64decode(meta_b64) if meta_b64 else None,
            version=int(d.get("version", 0)),
            ephemeral=bool(d.get("ephemeral", False)),
        )


@dataclass
class WakuStatus:
    """状态快照."""

    mode: WakuMode
    peer_id: Optional[str] = None
    multiaddr: Optional[str] = None
    running: bool = False
    rest_url: Optional[str] = None
    connected_peers: int = 0
    subscribed_topics: int = 0
    recv_queue_size: int = 0
    sent_count: int = 0
    recv_count: int = 0
    store_query_count: int = 0
    error: Optional[str] = None


# ── content topic helpers ─────────────────────────────────────────────────────


def did_to_short(did: str, n: int = 16) -> str:
    """did:key:z6MkXXX... → 末 n 字符."""
    if not did:
        raise ValueError("did 不能空")
    short = did.replace(":", "_").split("_")[-1][-n:]
    if len(short) < 4:
        raise ValueError(f"did 太短: {did!r}")
    return short


def build_content_topic(
    did_from: str, did_to: str, purpose: str = "borrow", version: str = SISOUL_CONTENT_TOPIC_VERSION
) -> str:
    """造 sisoul content topic: ``/sisoul/<a>_<b>/v1/<purpose>``."""
    if not purpose or not purpose.replace("-", "").isalnum():
        raise WakuTopicInvalid(f"purpose 非法 (仅 alnum + dash): {purpose!r}")
    a = did_to_short(did_from)
    b = "any" if did_to == "*" else did_to_short(did_to)
    topic = f"{SISOUL_CONTENT_TOPIC_PREFIX}/{a}_{b}/{version}/{purpose}"
    if len(topic) > 250:
        raise WakuTopicInvalid(f"content topic 太长: {topic}")
    return topic


def parse_content_topic(topic: str) -> dict[str, str]:
    """反解 ``/sisoul/<a>_<b>/v1/<purpose>``."""
    parts = topic.strip("/").split("/")
    if len(parts) != 4 or parts[0] != "sisoul":
        raise WakuTopicInvalid(f"不是 sisoul topic: {topic!r}")
    pair = parts[1]
    if "_" not in pair:
        raise WakuTopicInvalid(f"pair 段缺 _: {pair!r}")
    a, b = pair.split("_", 1)
    return {"did_a": a, "did_b": b, "version": parts[2], "purpose": parts[3]}


def topic_matches_peer(topic: str, peer_did_short: str) -> bool:
    """该 topic 是否寄给 peer_did_short."""
    try:
        parsed = parse_content_topic(topic)
    except WakuTopicInvalid:
        return False
    return (
        parsed["did_a"] == peer_did_short
        or parsed["did_b"] == peer_did_short
        or parsed["did_b"] == "any"
    )


# ── binary 检测 ───────────────────────────────────────────────────────────────


def find_nwaku_binary(custom_path: Optional[Path] = None) -> Optional[Path]:
    """按优先级找 nwaku/go-waku binary."""
    if custom_path:
        p = Path(custom_path).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p

    for name in ("nwaku", "wakunode2", "waku", "go-waku"):
        which = shutil.which(name)
        if which:
            return Path(which)

    private = Path.home() / ".sisoul" / "bin" / "nwaku"
    if private.is_file() and os.access(private, os.X_OK):
        return private

    return None


def detect_nwaku_version(bin_path: Path) -> Optional[str]:
    """跑 ``nwaku --version`` 拿版本."""
    try:
        out = subprocess.run(
            [str(bin_path), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        log.debug("nwaku --version 跑挂: %s", e)
        return None
    if out.returncode != 0:
        return None
    text = (out.stdout + out.stderr).strip()
    for line in text.splitlines():
        low = line.lower()
        if "version" in low or "wakunode" in low:
            for tok in line.split():
                if tok.startswith("v") and tok[1:2].isdigit():
                    return tok
                if tok[:1].isdigit() and "." in tok:
                    return f"v{tok}"
    return None


def nwaku_static_download_url(version: str = NWAKU_DEFAULT_VERSION) -> str:
    """生成 nwaku 静态二进制下载 URL."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        os_tag = "macos"
    elif system == "linux":
        os_tag = "linux"
    else:
        raise WakuBinaryNotFound(f"nwaku 不支持平台: {system}")

    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        raise WakuBinaryNotFound(f"nwaku 不支持架构: {machine}")

    return f"{NWAKU_DIST_BASE}/v{version}/nwaku-v{version}-{os_tag}-{arch}.tar.gz"


# ── libp2p PubSub fallback 探测 ───────────────────────────────────────────────


def _probe_libp2p_pubsub() -> bool:
    """探 py-libp2p 是否真能起 gossipsub."""
    try:
        import libp2p  # noqa: F401
        from libp2p.pubsub.gossipsub import GossipSub  # type: ignore[import-not-found]  # noqa: F401

        return True
    except Exception as e:  # noqa: BLE001
        log.debug("py-libp2p PubSub 不可用, fallback: %s", e)
        return False


LIBP2P_PUBSUB_AVAILABLE: bool = _probe_libp2p_pubsub()


# ── same-process bus (mock + libp2p-pubsub fallback 共用) ─────────────────────


_WAKU_BUS_LOCK = asyncio.Lock()
_WAKU_TOPIC_SUBSCRIBERS: dict[str, list[tuple[str, asyncio.Queue]]] = defaultdict(list)
_WAKU_GLOBAL_STORE: dict[str, deque] = defaultdict(lambda: deque(maxlen=10000))


def _bus_clear() -> None:
    """test 用: 清干净 bus state."""
    _WAKU_TOPIC_SUBSCRIBERS.clear()
    _WAKU_GLOBAL_STORE.clear()


# ── WakuTransport ─────────────────────────────────────────────────────────────


class WakuTransport(Transport):
    """Waku v2 store-and-forward transport (§32 §F.1 #9 主路径)."""

    name = "waku"

    def __init__(
        self,
        node_label: str,
        *,
        my_did: Optional[str] = None,
        nwaku_binary: Optional[str] = None,
        bootstrap_nodes: Optional[list[str]] = None,
        store_node: Optional[str] = None,
        mode: Optional[WakuMode] = None,
        rest_port: int = DEFAULT_REST_PORT,
        p2p_tcp_port: int = DEFAULT_P2P_TCP_PORT,
        external_rest_url: Optional[str] = None,
        startup_timeout_sec: float = DEFAULT_STARTUP_TIMEOUT_SEC,
        http_timeout_sec: float = DEFAULT_HTTP_TIMEOUT_SEC,
        store_ttl_sec: float = DEFAULT_STORE_TTL_SEC,
        recv_queue_size: int = DEFAULT_RECV_QUEUE_SIZE,
    ) -> None:
        self._label = node_label
        self._my_did = my_did
        self._my_did_short = did_to_short(my_did) if my_did else hashlib.sha256(
            f"waku:{node_label}:{time.time_ns()}".encode("utf-8")
        ).hexdigest()[:16]
        self._nwaku_bin_override = Path(nwaku_binary).expanduser() if nwaku_binary else None
        self._bootstrap_nodes = (
            list(bootstrap_nodes) if bootstrap_nodes is not None else list(DEFAULT_BOOTSTRAP)
        )
        self._store_node = store_node or (DEFAULT_STORE_NODES[0] if DEFAULT_STORE_NODES else None)
        self._mode_override = mode
        self._rest_port = rest_port
        self._p2p_tcp_port = p2p_tcp_port
        self._external_rest_url = external_rest_url
        self._startup_timeout = startup_timeout_sec
        self._http_timeout = http_timeout_sec
        self._store_ttl_sec = store_ttl_sec
        self._recv_queue_size = recv_queue_size

        self._mode: WakuMode = "mock"
        self._peer_id_str: str = ""
        self._multiaddr_str: str = ""
        self._proc: Optional[subprocess.Popen] = None
        self._http: Optional[httpx.AsyncClient] = None
        self._queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=recv_queue_size)
        self._subscribed_topics: set[str] = set()
        self._known_peers: dict[str, str] = {}
        self._filter_tasks: list[asyncio.Task] = []
        self._bus_subs: list[tuple[str, asyncio.Queue]] = []
        self._started = False
        self._sent_count = 0
        self._recv_count = 0
        self._store_query_count = 0

    @property
    def peer_id(self) -> str:
        return self._peer_id_str

    @property
    def multiaddr(self) -> str:
        return self._multiaddr_str

    @property
    def mode(self) -> WakuMode:
        return self._mode

    @property
    def my_did_short(self) -> str:
        return self._my_did_short

    @property
    def rest_url(self) -> str:
        if self._mode == "external-daemon" and self._external_rest_url:
            return self._external_rest_url.rstrip("/")
        return f"http://127.0.0.1:{self._rest_port}"

    def status(self) -> WakuStatus:
        return WakuStatus(
            mode=self._mode,
            peer_id=self._peer_id_str or None,
            multiaddr=self._multiaddr_str or None,
            running=self._started,
            rest_url=self.rest_url if self._mode in ("nwaku-subprocess", "external-daemon") else None,
            connected_peers=len(self._known_peers),
            subscribed_topics=len(self._subscribed_topics),
            recv_queue_size=self._queue.qsize(),
            sent_count=self._sent_count,
            recv_count=self._recv_count,
            store_query_count=self._store_query_count,
        )

    def _decide_mode(self) -> WakuMode:
        """按优先级决议 mode."""
        if self._mode_override is not None:
            return self._mode_override
        if self._external_rest_url:
            return "external-daemon"
        if find_nwaku_binary(self._nwaku_bin_override) is not None:
            return "nwaku-subprocess"
        if LIBP2P_PUBSUB_AVAILABLE:
            return "libp2p-pubsub"
        return "mock"

    async def start(self, port: int = 0) -> str:
        """启 Waku transport."""
        if self._started:
            raise RuntimeError("WakuTransport 已 start")
        if port > 0:
            self._rest_port = port

        self._mode = self._decide_mode()
        log.info("WakuTransport %s 启动, mode=%s", self._label, self._mode)

        if self._mode == "nwaku-subprocess":
            await self._start_nwaku_subprocess()
        elif self._mode == "external-daemon":
            await self._start_external_daemon()
        elif self._mode == "libp2p-pubsub":
            await self._start_libp2p_pubsub()
        else:
            await self._start_mock()

        self._started = True
        return self._multiaddr_str

    async def _start_nwaku_subprocess(self) -> None:
        """fork nwaku binary, 等 REST :8645 起."""
        bin_path = find_nwaku_binary(self._nwaku_bin_override)
        if bin_path is None:
            raise WakuBinaryNotFound("nwaku binary 找不到")

        args = [
            str(bin_path),
            "--rest=true",
            f"--rest-port={self._rest_port}",
            "--rest-address=127.0.0.1",
            "--rest-relay-cache-capacity=100",
            f"--tcp-port={self._p2p_tcp_port}",
            "--relay=true",
            "--store=false",
            "--filter=true",
            "--lightpush=true",
            f"--pubsub-topic={DEFAULT_PUBSUB_TOPIC}",
        ]
        for bn in self._bootstrap_nodes[:4]:
            args.append(f"--discv5-bootstrap-node={bn}")
        if self._store_node:
            args.append(f"--storenode={self._store_node}")

        log.debug("启 nwaku: %s", " ".join(args))
        try:
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=os.environ.copy(),
            )
        except (FileNotFoundError, PermissionError) as e:
            raise WakuBinaryNotFound(f"启 nwaku 失败: {e}") from e

        self._http = httpx.AsyncClient(timeout=self._http_timeout)
        deadline = time.monotonic() + self._startup_timeout
        last_err: Optional[Exception] = None
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                stderr = self._proc.stderr.read().decode("utf-8", errors="replace") if self._proc.stderr else ""
                raise WakuDaemonStartTimeout(f"nwaku 进程提前退出: {stderr[:500]}")
            try:
                resp = await self._http.get(f"{self.rest_url}/debug/v1/info", timeout=2.0)
                if resp.status_code == 200:
                    info = resp.json()
                    self._peer_id_str = info.get("listenAddresses", ["unknown"])[0].split("/")[-1] if info.get("listenAddresses") else "unknown"
                    addrs = info.get("listenAddresses", [])
                    self._multiaddr_str = addrs[0] if addrs else f"waku://{self._peer_id_str}"
                    return
            except (httpx.HTTPError, KeyError, IndexError) as e:
                last_err = e
            await asyncio.sleep(0.5)

        await self._kill_proc()
        raise WakuDaemonStartTimeout(
            f"nwaku REST {self.rest_url} 在 {self._startup_timeout}s 内未起 (last_err={last_err})"
        )

    async def _start_external_daemon(self) -> None:
        """连用户自跑的 nwaku/go-waku."""
        if not self._external_rest_url:
            raise WakuError("external-daemon mode 需 external_rest_url")
        self._http = httpx.AsyncClient(timeout=self._http_timeout)
        try:
            resp = await self._http.get(f"{self.rest_url}/debug/v1/info", timeout=5.0)
            resp.raise_for_status()
            info = resp.json()
            addrs = info.get("listenAddresses", [])
            self._peer_id_str = addrs[0].split("/")[-1] if addrs else "external"
            self._multiaddr_str = addrs[0] if addrs else self.rest_url
        except (httpx.HTTPError, KeyError) as e:
            await self._http.aclose()
            raise WakuDaemonStartTimeout(f"外部 nwaku {self.rest_url} 连不上: {e}") from e

    async def _start_libp2p_pubsub(self) -> None:
        """退化纯 py-libp2p GossipSub. 占位走 same-process bus."""
        if not LIBP2P_PUBSUB_AVAILABLE:
            raise WakuError("libp2p PubSub 不可用")
        self._peer_id_str = f"libp2p-{self._my_did_short}-{time.time_ns() % 100000}"
        self._multiaddr_str = f"/libp2p-pubsub/{self._peer_id_str}"

    async def _start_mock(self) -> None:
        """mock: same-process bus."""
        self._peer_id_str = f"mock-{self._my_did_short}-{time.time_ns() % 100000}"
        self._multiaddr_str = f"waku-mock://{self._peer_id_str}"

    async def stop(self) -> None:
        if not self._started:
            return

        for t in self._filter_tasks:
            t.cancel()
        if self._filter_tasks:
            await asyncio.gather(*self._filter_tasks, return_exceptions=True)
        self._filter_tasks.clear()

        async with _WAKU_BUS_LOCK:
            for topic, q in self._bus_subs:
                subs = _WAKU_TOPIC_SUBSCRIBERS.get(topic, [])
                _WAKU_TOPIC_SUBSCRIBERS[topic] = [(nid, qq) for nid, qq in subs if qq is not q]
                if not _WAKU_TOPIC_SUBSCRIBERS[topic]:
                    _WAKU_TOPIC_SUBSCRIBERS.pop(topic, None)
        self._bus_subs.clear()

        if self._mode == "nwaku-subprocess":
            await self._kill_proc()

        if self._http is not None:
            await self._http.aclose()
            self._http = None

        self._started = False

    async def _kill_proc(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=DEFAULT_SHUTDOWN_GRACE_SEC)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2.0)
        except Exception as e:  # noqa: BLE001
            log.warning("nwaku kill 出错: %s", e)
        self._proc = None

    async def send(self, to_peer: str, payload: bytes) -> None:
        """发 payload 给 to_peer (peer_id 或 did:key)."""
        if not self._started:
            raise WakuNotStarted("WakuTransport 未 start")
        if len(payload) > MAX_WAKU_PAYLOAD_BYTES:
            raise WakuMessageTooLarge(
                f"payload {len(payload)} > {MAX_WAKU_PAYLOAD_BYTES}"
            )

        to_short = did_to_short(to_peer) if to_peer.startswith("did:") else to_peer[-16:]
        topic = build_content_topic(
            self._my_did or self._my_did_short,
            to_peer if to_peer.startswith("did:") else f"did:key:{to_peer}",
            purpose="borrow",
        )
        wm = WakuMessage(
            payload=payload,
            content_topic=topic,
            timestamp=time.time(),
        )

        if self._mode in ("nwaku-subprocess", "external-daemon"):
            await self._send_via_rest(wm)
        else:
            await self._send_via_bus(wm, from_peer=self._peer_id_str, to_short=to_short)

        self._sent_count += 1

    async def send_to_topic(
        self, content_topic: str, payload: bytes, ephemeral: bool = False
    ) -> None:
        """显式指定 content topic 发."""
        if not self._started:
            raise WakuNotStarted("WakuTransport 未 start")
        if len(payload) > MAX_WAKU_PAYLOAD_BYTES:
            raise WakuMessageTooLarge(f"payload {len(payload)} > {MAX_WAKU_PAYLOAD_BYTES}")
        parse_content_topic(content_topic)

        wm = WakuMessage(
            payload=payload,
            content_topic=content_topic,
            timestamp=time.time(),
            ephemeral=ephemeral,
        )

        if self._mode in ("nwaku-subprocess", "external-daemon"):
            await self._send_via_rest(wm)
        else:
            await self._send_via_bus(wm, from_peer=self._peer_id_str, to_short=None)

        self._sent_count += 1

    async def _send_via_rest(self, wm: WakuMessage) -> None:
        """REST publish."""
        if self._http is None:
            raise WakuNotStarted("http client 未起")
        topic_enc = urllib.parse.quote(wm.content_topic, safe="")
        url = f"{self.rest_url}/relay/v1/auto/messages/{topic_enc}"
        try:
            resp = await self._http.post(url, json=wm.to_json(), timeout=self._http_timeout)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise WakuError(f"REST publish 失败 ({url}): {e}") from e

    async def _send_via_bus(
        self, wm: WakuMessage, from_peer: str, to_short: Optional[str]
    ) -> None:
        """same-process bus: fan-out + 入全局 store."""
        if not wm.ephemeral:
            _WAKU_GLOBAL_STORE[wm.content_topic].append((wm, time.time()))

        async with _WAKU_BUS_LOCK:
            subscribers = list(_WAKU_TOPIC_SUBSCRIBERS.get(wm.content_topic, []))

        msg = Message(
            from_peer=from_peer,
            to_peer=to_short or "*",
            payload=wm.payload,
            ts=wm.timestamp,
        )
        for sub_node_id, sub_q in subscribers:
            if sub_node_id == self._peer_id_str:
                continue
            try:
                sub_q.put_nowait(msg)
            except asyncio.QueueFull:
                log.warning("bus subscriber %s queue full, drop", sub_node_id)

    async def recv(self, timeout: float = 1.0) -> Optional[Message]:
        """非阻塞读一条 message."""
        if not self._started:
            raise WakuNotStarted("WakuTransport 未 start")
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def subscribe_topic(self, content_topic: str) -> None:
        """订 content topic."""
        if not self._started:
            raise WakuNotStarted("WakuTransport 未 start")
        parse_content_topic(content_topic)
        if content_topic in self._subscribed_topics:
            return

        if self._mode in ("nwaku-subprocess", "external-daemon"):
            await self._subscribe_via_rest(content_topic)
        else:
            await self._subscribe_via_bus(content_topic)

        self._subscribed_topics.add(content_topic)

    async def _subscribe_via_rest(self, content_topic: str) -> None:
        """REST filter subscribe + 后台 long-poll."""
        if self._http is None:
            raise WakuNotStarted("http client 未起")
        sub_body = {
            "requestId": hashlib.sha256(f"{content_topic}:{time.time_ns()}".encode()).hexdigest()[:16],
            "contentFilters": [content_topic],
            "pubsubTopic": DEFAULT_PUBSUB_TOPIC,
        }
        try:
            resp = await self._http.post(
                f"{self.rest_url}/filter/v2/subscriptions",
                json=sub_body,
                timeout=self._http_timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("filter subscribe %s 失败: %s", content_topic, e)

        task = asyncio.create_task(self._filter_long_poll_loop(content_topic))
        self._filter_tasks.append(task)

    async def _filter_long_poll_loop(self, content_topic: str) -> None:
        """后台轮 filter messages."""
        topic_enc = urllib.parse.quote(content_topic, safe="")
        url = f"{self.rest_url}/filter/v2/messages/{topic_enc}"
        backoff = 0.5
        while self._started:
            try:
                if self._http is None:
                    return
                resp = await self._http.get(url, timeout=DEFAULT_FILTER_LONG_POLL_SEC + 2)
                if resp.status_code == 200:
                    msgs = resp.json()
                    if isinstance(msgs, list):
                        for m in msgs:
                            wm = WakuMessage.from_json(m)
                            self._enqueue_message(wm)
                    backoff = 0.5
                elif resp.status_code == 404:
                    await asyncio.sleep(0.5)
                else:
                    await asyncio.sleep(min(backoff, 10.0))
                    backoff *= 2
            except asyncio.CancelledError:
                return
            except (httpx.HTTPError, json.JSONDecodeError) as e:
                log.debug("filter long-poll %s 错: %s", content_topic, e)
                await asyncio.sleep(min(backoff, 10.0))
                backoff *= 2

    async def _subscribe_via_bus(self, content_topic: str) -> None:
        """bus 模式: 加进 routing table."""
        async with _WAKU_BUS_LOCK:
            _WAKU_TOPIC_SUBSCRIBERS[content_topic].append((self._peer_id_str, self._queue))
            self._bus_subs.append((content_topic, self._queue))

    def _enqueue_message(self, wm: WakuMessage) -> None:
        """REST 来的 WakuMessage → Message 入本地 queue."""
        try:
            parsed = parse_content_topic(wm.content_topic)
            from_short = parsed["did_a"] if parsed["did_a"] != self._my_did_short else parsed["did_b"]
        except WakuTopicInvalid:
            from_short = "unknown"
        msg = Message(
            from_peer=from_short,
            to_peer=self._my_did_short,
            payload=wm.payload,
            ts=wm.timestamp,
        )
        try:
            self._queue.put_nowait(msg)
            self._recv_count += 1
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(msg)
                self._recv_count += 1
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    async def query_store(
        self,
        peer_id: str,
        since_ts: float,
        *,
        end_ts: Optional[float] = None,
        purposes: Optional[list[str]] = None,
        page_size: int = 100,
    ) -> list[Message]:
        """拉 store node 历史消息. 24h TTL 上限."""
        if not self._started:
            raise WakuNotStarted("WakuTransport 未 start")

        now = time.time()
        if end_ts is None:
            end_ts = now
        if now - since_ts > self._store_ttl_sec:
            raise WakuStoreTTLExceeded(
                f"since_ts {since_ts} 超 {self._store_ttl_sec}s ({now - since_ts:.0f}s ago)"
            )

        self._store_query_count += 1

        peer_short = did_to_short(peer_id) if peer_id.startswith("did:") else (peer_id[-16:] if peer_id != "*" else "*")
        topics: list[str] = []
        for purpose in (purposes or ["borrow", "proxy", "ledger", "heartbeat", "vault-sync"]):
            if peer_short == "*":
                topics.append(f"{SISOUL_CONTENT_TOPIC_PREFIX}/*_{self._my_did_short}/v1/{purpose}")
                topics.append(f"{SISOUL_CONTENT_TOPIC_PREFIX}/{self._my_did_short}_*/v1/{purpose}")
            else:
                topics.append(build_content_topic(
                    self._my_did or f"did:key:{self._my_did_short}",
                    peer_id if peer_id.startswith("did:") else f"did:key:{peer_id}",
                    purpose=purpose,
                ))
                topics.append(build_content_topic(
                    peer_id if peer_id.startswith("did:") else f"did:key:{peer_id}",
                    self._my_did or f"did:key:{self._my_did_short}",
                    purpose=purpose,
                ))

        if self._mode in ("nwaku-subprocess", "external-daemon"):
            return await self._query_store_via_rest(topics, since_ts, end_ts, page_size)
        else:
            return self._query_store_via_bus(topics, since_ts, end_ts, peer_short)

    async def _query_store_via_rest(
        self, topics: list[str], since_ts: float, end_ts: float, page_size: int
    ) -> list[Message]:
        """REST store query."""
        if self._http is None:
            raise WakuNotStarted("http client 未起")
        results: list[Message] = []
        for topic in topics:
            if "*" in topic:
                continue
            params = {
                "contentTopics": topic,
                "startTime": int(since_ts * 1e9),
                "endTime": int(end_ts * 1e9),
                "pageSize": page_size,
            }
            url = f"{self.rest_url}/store/v1/messages"
            try:
                resp = await self._http.get(url, params=params, timeout=self._http_timeout)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                msgs = data.get("messages", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                for m in msgs:
                    wm = WakuMessage.from_json(m)
                    try:
                        parsed = parse_content_topic(wm.content_topic)
                        from_short = parsed["did_a"] if parsed["did_a"] != self._my_did_short else parsed["did_b"]
                    except WakuTopicInvalid:
                        from_short = "unknown"
                    results.append(Message(
                        from_peer=from_short,
                        to_peer=self._my_did_short,
                        payload=wm.payload,
                        ts=wm.timestamp,
                    ))
            except (httpx.HTTPError, json.JSONDecodeError) as e:
                log.debug("store query %s 失败: %s", topic, e)

        results.sort(key=lambda m: m.ts)
        return results

    def _query_store_via_bus(
        self, topics: list[str], since_ts: float, end_ts: float, peer_short: str
    ) -> list[Message]:
        """bus 模式: 从全局 store 拉."""
        results: list[Message] = []
        wildcard_topics = [t for t in topics if "*" in t]
        exact_topics = [t for t in topics if "*" not in t]

        for topic in exact_topics:
            for wm, insert_ts in _WAKU_GLOBAL_STORE.get(topic, []):
                if since_ts <= wm.timestamp <= end_ts:
                    self._add_bus_msg_to_results(wm, results)

        if wildcard_topics:
            for topic, entries in _WAKU_GLOBAL_STORE.items():
                if not topic_matches_peer(topic, self._my_did_short):
                    continue
                wildcard_purposes = {t.rsplit("/", 1)[-1] for t in wildcard_topics if "*" in t}
                try:
                    parsed = parse_content_topic(topic)
                except WakuTopicInvalid:
                    continue
                if wildcard_purposes and parsed["purpose"] not in wildcard_purposes:
                    continue
                for wm, insert_ts in entries:
                    if since_ts <= wm.timestamp <= end_ts:
                        self._add_bus_msg_to_results(wm, results)

        results.sort(key=lambda m: m.ts)
        return results

    def _add_bus_msg_to_results(self, wm: WakuMessage, results: list[Message]) -> None:
        try:
            parsed = parse_content_topic(wm.content_topic)
            from_short = parsed["did_a"] if parsed["did_a"] != self._my_did_short else parsed["did_b"]
        except WakuTopicInvalid:
            from_short = "unknown"
        results.append(Message(
            from_peer=from_short,
            to_peer=self._my_did_short,
            payload=wm.payload,
            ts=wm.timestamp,
        ))

    async def gc_store(self) -> int:
        """清超 TTL 条目 (bus 模式)."""
        if self._mode in ("nwaku-subprocess", "external-daemon"):
            return 0
        cutoff = time.time() - self._store_ttl_sec
        cleaned = 0
        for topic, entries in list(_WAKU_GLOBAL_STORE.items()):
            new_entries = deque((wm, ts) for wm, ts in entries if ts >= cutoff)
            cleaned += len(entries) - len(new_entries)
            if new_entries:
                _WAKU_GLOBAL_STORE[topic] = new_entries
            else:
                _WAKU_GLOBAL_STORE.pop(topic, None)
        return cleaned

    def register_peer(self, peer_did: str, multiaddr: Optional[str] = None) -> None:
        """注册已知 peer."""
        short = did_to_short(peer_did)
        self._known_peers[short] = multiaddr or short


def select_transport_with_waku(
    node_label: str = "node",
    prefer: Optional[str] = None,
    my_did: Optional[str] = None,
) -> Transport:
    """选 transport: waku > libp2p > webrtc > inmem.

    可单独使用而不改原 ``transport.select_transport`` (用户/linter 还原).
    """
    from sisoul.p2p.transport import (
        AIORTC_AVAILABLE,
        LIBP2P_AVAILABLE,
        InMemoryTransport,
        LibP2PTransport,
        LibP2PUnavailable,
        WebRTCTransport,
    )

    order: list[str]
    if prefer is not None:
        if prefer not in ("waku", "libp2p", "webrtc", "inmem"):
            raise ValueError(f"prefer 必须 ∈ waku|libp2p|webrtc|inmem, 实际 {prefer}")
        order = [prefer]
    else:
        order = ["waku", "libp2p", "webrtc", "inmem"]

    last_err: Optional[Exception] = None
    for choice in order:
        try:
            if choice == "waku":
                return WakuTransport(node_label=node_label, my_did=my_did)
            elif choice == "libp2p":
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
            log.debug("select_transport_with_waku %s 失败 fallback: %s", choice, e)
            last_err = e
            continue

    raise RuntimeError(
        f"全部 transport 不可用 (prefer={prefer}, last={last_err})."
    )


__all__ = [
    "DEFAULT_BOOTSTRAP",
    "DEFAULT_REST_PORT",
    "DEFAULT_STORE_NODES",
    "DEFAULT_STORE_TTL_SEC",
    "LIBP2P_PUBSUB_AVAILABLE",
    "MAX_WAKU_PAYLOAD_BYTES",
    "NWAKU_DEFAULT_VERSION",
    "WakuBinaryNotFound",
    "WakuDaemonStartTimeout",
    "WakuError",
    "WakuMessage",
    "WakuMessageTooLarge",
    "WakuMode",
    "WakuNotStarted",
    "WakuPeerNotFound",
    "WakuStatus",
    "WakuStoreTTLExceeded",
    "WakuTopicInvalid",
    "WakuTransport",
    "_bus_clear",
    "build_content_topic",
    "detect_nwaku_version",
    "did_to_short",
    "find_nwaku_binary",
    "nwaku_static_download_url",
    "parse_content_topic",
    "select_transport_with_waku",
    "topic_matches_peer",
]
