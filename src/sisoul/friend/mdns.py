"""sisoul friend · mDNS 局域网朋友发现 (P2-CD).

ServiceType ``_sisoul._tcp.local.``. TXT: did_key / multiaddr / petname_hint.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import asdict, dataclass
from typing import Optional

__all__ = [
    "FriendPeer",
    "MDNSAnnouncer",
    "MDNSScanner",
    "SERVICE_TYPE",
    "ZEROCONF_AVAILABLE",
    "scan",
]

log = logging.getLogger(__name__)

SERVICE_TYPE = "_sisoul._tcp.local."
DEFAULT_PORT = 4001
DEFAULT_SCAN_TIMEOUT = 5.0


def _probe_zeroconf() -> bool:
    try:
        import zeroconf  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


ZEROCONF_AVAILABLE: bool = _probe_zeroconf()


@dataclass
class FriendPeer:
    did_key: str
    multiaddr: str
    petname_hint: str
    hostname: str
    port: int = DEFAULT_PORT

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _local_ip() -> str:
    """获取本机非 loopback IP, 失败 fallback 127.0.0.1."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.3)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]  # type: ignore[no-any-return]
    except OSError:
        return "127.0.0.1"


def _make_service_name(did_key: str) -> str:
    # mDNS instance name 不能太长 (≤ 63 字节 per label); did:key 完整 ~ 56B 一般 ok
    # 用 did:key 末尾 + 唯一 timestamp 防同机多实例冲突.
    short = did_key.split(":")[-1][-16:] if did_key else f"sisoul-{int(time.time())}"
    return f"sisoul-{short}-{int(time.time()*1000) % 100000}.{SERVICE_TYPE}"


def _props_to_str(props: object) -> dict[str, str]:
    """zeroconf props dict[bytes, bytes|None] → dict[str, str]."""
    out: dict[str, str] = {}
    if not isinstance(props, dict):
        return out
    for k, v in props.items():
        try:
            key = k.decode("utf-8") if isinstance(k, (bytes, bytearray)) else str(k)
            if v is None:
                out[key] = ""
            elif isinstance(v, (bytes, bytearray)):
                out[key] = v.decode("utf-8", errors="replace")
            else:
                out[key] = str(v)
        except Exception:  # noqa: BLE001
            continue
    return out


class MDNSAnnouncer:
    """注册 sisoul mDNS service. 用 ``with`` 块或显式 start/stop."""

    def __init__(
        self,
        did_key: str,
        *,
        multiaddr: Optional[str] = None,
        petname_hint: str = "",
        port: int = DEFAULT_PORT,
        ip: Optional[str] = None,
        interfaces: Optional[list[str]] = None,
    ) -> None:
        if not ZEROCONF_AVAILABLE:
            raise RuntimeError("zeroconf 不可用, 装 'pip install zeroconf' 后再 announce")
        if not did_key or not did_key.startswith("did:"):
            raise ValueError(f"did_key 必须 did: 开头, 拿到: {did_key!r}")
        self.did_key = did_key
        self.port = port
        self.ip = ip or _local_ip()
        self.multiaddr = multiaddr or f"/ip4/{self.ip}/tcp/{port}"
        self.petname_hint = petname_hint
        self._interfaces = interfaces
        self._zc: object | None = None
        self._info: object | None = None

    def start(self) -> "MDNSAnnouncer":
        from zeroconf import ServiceInfo, Zeroconf  # type: ignore[import-not-found]

        zc_kwargs: dict[str, object] = {}
        if self._interfaces is not None:
            zc_kwargs["interfaces"] = self._interfaces
        zc = Zeroconf(**zc_kwargs)
        props = {
            b"did_key": self.did_key.encode("utf-8"),
            b"multiaddr": self.multiaddr.encode("utf-8"),
            b"petname_hint": self.petname_hint.encode("utf-8"),
        }
        info = ServiceInfo(
            type_=SERVICE_TYPE,
            name=_make_service_name(self.did_key),
            addresses=[socket.inet_aton(self.ip)],
            port=self.port,
            properties=props,
            server=f"{socket.gethostname()}.local.",
        )
        zc.register_service(info)  # type: ignore[attr-defined]
        self._zc = zc
        self._info = info
        log.info("mDNS announced: %s @ %s:%s", self.did_key, self.ip, self.port)
        return self

    def stop(self) -> None:
        try:
            if self._zc is not None and self._info is not None:
                self._zc.unregister_service(self._info)  # type: ignore[attr-defined]
            if self._zc is not None:
                self._zc.close()  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            log.debug("mDNS stop cleanup err: %s", e)
        finally:
            self._zc = None
            self._info = None

    def __enter__(self) -> "MDNSAnnouncer":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()


class MDNSScanner:
    """扫局域网 sisoul peer (同步阻塞 scan)."""

    def __init__(
        self,
        *,
        own_did_key: Optional[str] = None,
        interfaces: Optional[list[str]] = None,
    ) -> None:
        if not ZEROCONF_AVAILABLE:
            raise RuntimeError("zeroconf 不可用, 装 'pip install zeroconf' 后再 scan")
        self.own_did_key = own_did_key
        self._interfaces = interfaces
        self._peers: dict[str, FriendPeer] = {}
        self._lock = threading.Lock()

    def _on_service(self, zc: object, type_: str, name: str) -> None:
        try:
            info = zc.get_service_info(type_, name, timeout=1500)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            log.debug("get_service_info err for %s: %s", name, e)
            return
        if info is None:
            return
        props = _props_to_str(getattr(info, "properties", None) or {})
        did_key = props.get("did_key", "")
        if not did_key:
            return
        if self.own_did_key and did_key == self.own_did_key:
            return  # 跳过自己
        try:
            parsed = info.parsed_addresses() if hasattr(info, "parsed_addresses") else []  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            parsed = []
        host = parsed[0] if parsed else "127.0.0.1"
        port = int(getattr(info, "port", DEFAULT_PORT) or DEFAULT_PORT)
        multiaddr = props.get("multiaddr") or f"/ip4/{host}/tcp/{port}"
        peer = FriendPeer(
            did_key=did_key,
            multiaddr=multiaddr,
            petname_hint=props.get("petname_hint", ""),
            hostname=getattr(info, "server", host) or host,
            port=port,
        )
        with self._lock:
            self._peers[did_key] = peer

    def scan(self, timeout: float = DEFAULT_SCAN_TIMEOUT) -> list[FriendPeer]:
        from zeroconf import ServiceBrowser, Zeroconf  # type: ignore[import-not-found]

        zc_kwargs: dict[str, object] = {}
        if self._interfaces is not None:
            zc_kwargs["interfaces"] = self._interfaces
        zc = Zeroconf(**zc_kwargs)
        outer = self

        class _Listener:
            def add_service(self, zc_: object, type_: str, name: str) -> None:  # noqa: D401
                outer._on_service(zc_, type_, name)

            def update_service(self, zc_: object, type_: str, name: str) -> None:
                outer._on_service(zc_, type_, name)

            def remove_service(self, zc_: object, type_: str, name: str) -> None:
                pass  # 一次性 scan 不关心 removal

        browser = ServiceBrowser(zc, SERVICE_TYPE, _Listener())  # noqa: F841 # keep alive
        try:
            time.sleep(max(0.1, float(timeout)))
        finally:
            try:
                zc.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            return list(self._peers.values())


def scan(
    timeout: float = DEFAULT_SCAN_TIMEOUT,
    *,
    own_did_key: Optional[str] = None,
    interfaces: Optional[list[str]] = None,
) -> list[dict[str, object]]:
    """One-shot scan, 返回 list[dict] (CLI 友好)."""
    scanner = MDNSScanner(own_did_key=own_did_key, interfaces=interfaces)
    peers = scanner.scan(timeout=timeout)
    return [p.to_dict() for p in peers]
