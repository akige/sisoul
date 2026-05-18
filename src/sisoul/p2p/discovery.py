"""sisoul p2p · peer discovery (Phase 3 W31-W36 · 波 4 dev-A).

§28 §1.1 模块 9.

3 路 discovery:
1. **DHT** (libp2p Kademlia DHT) — 真去中心化, libp2p 不可用时跳过.
2. **mDNS** (local network) — 本机房 / 同 WiFi 自动发现 (Bonjour); 装 ``zeroconf`` 库.
3. **manual peer list** — 用户 ``sisoul p2p add-peer <multiaddr>`` 手动加; 持久化到
   ``vault_dir/p2p/peers.json``. mDNS/DHT 失败时这是唯一手段.

设计:
- 抽象 ``Discoverer`` 接口: ``start / stop / list_peers / add_peer``.
- ``CompositeDiscoverer`` 组合多路 (任一找到就 surface).
- ``ManualDiscoverer`` (一定可用, 兜底).
- ``MDNSDiscoverer`` (zeroconf 装上才用).
- ``DHTDiscoverer`` (libp2p 可用才用; 当前 fallback noop).
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from sisoul.p2p.transport import LIBP2P_AVAILABLE, PeerInfo

log = logging.getLogger(__name__)


# ── 探 mDNS (zeroconf) ─────────────────────────────────────────────────────────


def _probe_zeroconf() -> bool:
    try:
        import zeroconf  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


MDNS_AVAILABLE: bool = _probe_zeroconf()


# ── ABC ───────────────────────────────────────────────────────────────────────


class Discoverer(ABC):
    name: str = "abstract"

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    def list_peers(self) -> list[PeerInfo]: ...

    def add_peer(self, peer: PeerInfo) -> None:
        """默认 no-op (只有 ManualDiscoverer 实现写入)."""


# ── ManualDiscoverer (一定可用 + 持久化) ───────────────────────────────────────


class ManualDiscoverer(Discoverer):
    """用户手动加的 peer list, 持久化到 ``peers.json``."""

    name = "manual"

    def __init__(self, store_path: Optional[Path] = None) -> None:
        self._store_path = store_path
        self._peers: dict[str, PeerInfo] = {}
        self._started = False
        if store_path is not None and store_path.exists():
            self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
            for item in data.get("peers", []):
                p = PeerInfo(
                    peer_id=item["peer_id"],
                    multiaddr=item["multiaddr"],
                    transport=item.get("transport", "unknown"),
                    last_seen_ts=item.get("last_seen_ts", time.time()),
                )
                self._peers[p.peer_id] = p
        except Exception as e:  # noqa: BLE001
            log.warning("ManualDiscoverer 加载 peers.json 失败: %s", e)

    def _save(self) -> None:
        if self._store_path is None:
            return
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "peers": [
                {
                    "peer_id": p.peer_id,
                    "multiaddr": p.multiaddr,
                    "transport": p.transport,
                    "last_seen_ts": p.last_seen_ts,
                }
                for p in self._peers.values()
            ]
        }
        self._store_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    def list_peers(self) -> list[PeerInfo]:
        return sorted(self._peers.values(), key=lambda p: p.last_seen_ts, reverse=True)

    def add_peer(self, peer: PeerInfo) -> None:
        self._peers[peer.peer_id] = peer
        self._save()

    def remove_peer(self, peer_id: str) -> bool:
        if peer_id in self._peers:
            del self._peers[peer_id]
            self._save()
            return True
        return False


# ── MDNSDiscoverer (zeroconf 装上才用) ─────────────────────────────────────────


class MDNSDiscoverer(Discoverer):
    """mDNS / Bonjour 本机房 discovery.

    设计 (简化):
    - 启动时 publish 自己 service (_sisoul._tcp.local.)
    - 监听 add/remove, 收集 PeerInfo.
    - 本 phase 不在 unit test 真起 mDNS (跨 OS 不稳, CI 没 multicast); test 用 mock.
    """

    name = "mdns"
    SERVICE_TYPE = "_sisoul._tcp.local."

    def __init__(self, my_peer_id: str, my_port: int) -> None:
        if not MDNS_AVAILABLE:
            raise RuntimeError("zeroconf 不可用, 装 ``uv pip install zeroconf`` 后再用 mDNS")
        self._my_peer_id = my_peer_id
        self._my_port = my_port
        self._peers: dict[str, PeerInfo] = {}
        self._zc: object | None = None  # Zeroconf 实例
        self._info: object | None = None  # ServiceInfo
        self._browser: object | None = None
        self._started = False

    async def start(self) -> None:
        # 真起 mDNS (loopback 也 OK); 失败不致命, log + 降级 manual.
        try:
            from zeroconf import (  # type: ignore[import-not-found]
                ServiceBrowser,
                ServiceInfo,
                Zeroconf,
            )
            import socket
            self._zc = Zeroconf()
            # 注册自己
            host_ip = socket.inet_aton("127.0.0.1")  # 本 phase 只 loopback
            info = ServiceInfo(
                type_=self.SERVICE_TYPE,
                name=f"{self._my_peer_id}.{self.SERVICE_TYPE}",
                addresses=[host_ip],
                port=self._my_port,
                properties={"peer_id": self._my_peer_id.encode("utf-8")},
            )
            self._zc.register_service(info)  # type: ignore[union-attr]
            self._info = info

            # 监听其他
            outer = self

            class _Listener:
                def add_service(self, zc, type_, name):  # noqa: D401, ANN001
                    info = zc.get_service_info(type_, name)
                    if info is None:
                        return
                    props = info.properties or {}
                    peer_id_bytes = props.get(b"peer_id", b"")
                    peer_id = peer_id_bytes.decode("utf-8") if peer_id_bytes else name.split(".")[0]
                    if peer_id == outer._my_peer_id:
                        return  # 自己
                    addr = info.parsed_addresses()[0] if info.parsed_addresses() else "127.0.0.1"
                    outer._peers[peer_id] = PeerInfo(
                        peer_id=peer_id,
                        multiaddr=f"mdns://{addr}:{info.port}/{peer_id}",
                        transport="mdns",
                    )

                def remove_service(self, zc, type_, name):  # noqa: ANN001
                    peer_id = name.split(".")[0]
                    outer._peers.pop(peer_id, None)

                def update_service(self, zc, type_, name):  # noqa: ANN001
                    self.add_service(zc, type_, name)

            self._browser = ServiceBrowser(self._zc, self.SERVICE_TYPE, _Listener())  # type: ignore[arg-type]
            self._started = True
        except Exception as e:  # noqa: BLE001
            log.warning("mDNS start 失败 (降级 manual only): %s", e)

    async def stop(self) -> None:
        try:
            if self._zc is not None and self._info is not None:
                self._zc.unregister_service(self._info)  # type: ignore[attr-defined]
            if self._zc is not None:
                self._zc.close()  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            log.debug("mDNS stop cleanup 出错: %s", e)
        self._zc = None
        self._info = None
        self._browser = None
        self._started = False

    def list_peers(self) -> list[PeerInfo]:
        return list(self._peers.values())


# ── DHTDiscoverer (libp2p 可用才用; 当前 fallback noop) ────────────────────────


class DHTDiscoverer(Discoverer):
    """libp2p Kademlia DHT discovery. 当前 libp2p 不可用 → noop."""

    name = "dht"

    def __init__(self) -> None:
        self._available = LIBP2P_AVAILABLE

    async def start(self) -> None:
        if not self._available:
            log.info("DHTDiscoverer noop (libp2p 不可用)")

    async def stop(self) -> None:
        pass

    def list_peers(self) -> list[PeerInfo]:
        return []  # 当前没真 DHT


# ── CompositeDiscoverer (组合多路) ─────────────────────────────────────────────


class CompositeDiscoverer(Discoverer):
    """汇总多 discoverer 的 peer list."""

    name = "composite"

    def __init__(self, discoverers: list[Discoverer]) -> None:
        if not discoverers:
            raise ValueError("CompositeDiscoverer 至少一个 sub-discoverer")
        self._discoverers = discoverers

    async def start(self) -> None:
        for d in self._discoverers:
            try:
                await d.start()
            except Exception as e:  # noqa: BLE001
                log.warning("子 discoverer %s start 失败: %s", d.name, e)

    async def stop(self) -> None:
        for d in self._discoverers:
            try:
                await d.stop()
            except Exception as e:  # noqa: BLE001
                log.warning("子 discoverer %s stop 失败: %s", d.name, e)

    def list_peers(self) -> list[PeerInfo]:
        merged: dict[str, PeerInfo] = {}
        for d in self._discoverers:
            for p in d.list_peers():
                # 后来者优先 (更新 last_seen)
                merged[p.peer_id] = p
        return sorted(merged.values(), key=lambda p: p.last_seen_ts, reverse=True)

    def add_peer(self, peer: PeerInfo) -> None:
        # 默认加到第一个 Manual 类型
        for d in self._discoverers:
            if isinstance(d, ManualDiscoverer):
                d.add_peer(peer)
                return
        # 无 Manual → 报错
        raise RuntimeError("CompositeDiscoverer 中没有 ManualDiscoverer, add_peer 无目标")


def build_default_discoverer(
    my_peer_id: str,
    my_port: int,
    manual_store_path: Optional[Path] = None,
    enable_mdns: bool = True,
) -> CompositeDiscoverer:
    """构造默认 3 路 composite discoverer.

    - 总含 ManualDiscoverer (兜底).
    - mDNS 装上 + enable_mdns=True 时加.
    - DHT 仅 libp2p 可用时加 (当前 noop).
    """
    discoverers: list[Discoverer] = []
    if LIBP2P_AVAILABLE:
        discoverers.append(DHTDiscoverer())
    if enable_mdns and MDNS_AVAILABLE:
        try:
            discoverers.append(MDNSDiscoverer(my_peer_id=my_peer_id, my_port=my_port))
        except RuntimeError as e:
            log.warning("MDNSDiscoverer 跳过: %s", e)
    discoverers.append(ManualDiscoverer(store_path=manual_store_path))
    return CompositeDiscoverer(discoverers)


__all__ = [
    "CompositeDiscoverer",
    "DHTDiscoverer",
    "Discoverer",
    "MDNS_AVAILABLE",
    "MDNSDiscoverer",
    "ManualDiscoverer",
    "build_default_discoverer",
]
