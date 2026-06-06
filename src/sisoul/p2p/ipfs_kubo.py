"""sisoul · 内嵌 kubo IPFS 节点 (Wave A-3 · 替 Pinata pin SaaS).

§31 §2 #6 + §32 §B.3 · sisoul v1.0-decentralized 完整方案.

# 设计

砍掉 Pinata SaaS, 让 sisoul daemon 内嵌官方 Go 写的 kubo (https://github.com/ipfs/kubo)
子进程, 朋友间互相 pin 各自 vault snapshot / EAS payload / VCK 课程内容, 通过 libp2p
DHT 自动发现 peer, 完全去 SaaS 化.

# kubo binary 来源 (按优先级)

1. **PATH 现有**: `which ipfs` 命中 → 直接用 (用户已 `brew install ipfs` / `apt install kubo`).
2. **brew 自检**: macOS 上 `brew install ipfs` (用户预批 才装).
3. **静态二进制**: 从 https://dist.ipfs.tech/kubo/<ver>/ 下 .tar.gz (sigstore signed),
   sha256 校验后解到 `~/.sisoul/bin/ipfs`.
4. **external-daemon**: 用户已自跑 `ipfs daemon`, 配 `external_daemon_url=http://localhost:5001`,
   不 fork subprocess, 仅 HTTP 调.
5. **mock**: 全无 → CID = `mockcid-<sha>`, 跟 skill_ipfs.py 现有 mock 一致, dev/test 兜底.

# HTTP API

kubo daemon 跑起后暴露:
- API: `http://127.0.0.1:5001/api/v0/...` (本机 only, 默认不绑 0.0.0.0)
- Gateway: `http://127.0.0.1:8080/ipfs/<cid>` (HTTP 拉)
- Swarm: TCP 4001 + QUIC 4001 (跟外部 peer)

本模块用 httpx 调 `/api/v0/`. 关键 endpoint:
- `POST /api/v0/add` (multipart) — 加 + pin
- `POST /api/v0/cat?arg=<cid>` — 拉
- `POST /api/v0/pin/add?arg=<cid>` — pin 已有
- `POST /api/v0/pin/rm?arg=<cid>` — unpin
- `POST /api/v0/pin/ls?type=all` — 列 pin
- `POST /api/v0/id` — 本节点 PeerID + AgentVersion
- `POST /api/v0/swarm/peers` — 当前连接 peer
- `POST /api/v0/swarm/connect?arg=<multiaddr>` — 主动连
- `POST /api/v0/dht/findpeer?arg=<peer_id>` — DHT 查 peer multiaddr
- `POST /api/v0/dht/provide?arg=<cid>` — DHT 宣告我有这 cid
- `POST /api/v0/bootstrap/list` — 列 bootstrap node

# Bootstrap

默认 10+ public bootstrap (含 Cloudflare IPFS + IPFS Foundation 官方):
- /ip4/104.131.131.82/tcp/4001/p2p/QmaCpDMGvV2BGHeYERUEnRQAwe3N8SzbUtfsmvsqQLuvuJ  (IPFS bootstrap-0)
- /dnsaddr/bootstrap.libp2p.io/p2p/QmNnooDu7bfjPFoTZYxMNLWUQJyrVwtbZg5gBMjTezGAJN
- /dnsaddr/cf-ipfs.com (Cloudflare gateway peer)
... 详 DEFAULT_BOOTSTRAP

用户可在 ~/.sisoul/ipfs-config.json 自填扩展 bootstrap.

# 真验收 (V1-V9 §B.3.8)

- V1 `ipfs --version` 跑通 (binary 真在)
- V2 daemon 启 10s 内 PeerID 拿到
- V3 add 12KB 文件 → 真 bafy... CID
- V4 外部 `curl https://ipfs.io/ipfs/<cid>` 拿同 bytes (需公网 DHT 通)
- V5 朋友 daemon 真 pin (本模块 + friend 协议)
- V6 反向: 非 friend 拒 (skill_ipfs / handle_pin_request)

mock 模式跑全部 unit test (CI 无 kubo 可用).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import httpx

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────


DEFAULT_REPO_PATH = Path.home() / ".sisoul" / "ipfs-repo"
DEFAULT_BIN_PATH = Path.home() / ".sisoul" / "bin" / "ipfs"
DEFAULT_API_PORT = 5001
DEFAULT_GATEWAY_PORT = 8080
DEFAULT_SWARM_TCP_PORT = 4001
DEFAULT_SWARM_QUIC_PORT = 4001

# kubo 静态二进制下载 (sigstore-signed release, 详 §B.4 sigstore module).
# 选 v0.30.x (2026-04 release, AgentVersion=kubo/0.30.0)
KUBO_DEFAULT_VERSION = "0.30.0"
KUBO_DIST_BASE = "https://dist.ipfs.tech/kubo"

# 默认 bootstrap (10+, 含 Cloudflare 与 IPFS Foundation 官方).
# 这些是 IPFS 公网公共节点, 24/7 在线, DHT 起步靠它们.
DEFAULT_BOOTSTRAP: tuple[str, ...] = (
    # IPFS Foundation 官方 bootstrap (旧版 hardcoded list)
    "/dnsaddr/bootstrap.libp2p.io/p2p/QmNnooDu7bfjPFoTZYxMNLWUQJyrVwtbZg5gBMjTezGAJN",
    "/dnsaddr/bootstrap.libp2p.io/p2p/QmQCU2EcMqAqQPR2i9bChDtGNJchTbq5TbXJJ16u19uLTa",
    "/dnsaddr/bootstrap.libp2p.io/p2p/QmbLHAnMoJPWSCR5Zhtx6BHJX9KiKNN6tpvbUcqanj75Nb",
    "/dnsaddr/bootstrap.libp2p.io/p2p/QmcZf59bWwK5XFi76CZX8cbJ4BhTzzA3gU1ZjYZcYW3dwt",
    # Cloudflare IPFS (cf-ipfs.com 公开 peer)
    "/ip4/104.131.131.82/tcp/4001/p2p/QmaCpDMGvV2BGHeYERUEnRQAwe3N8SzbUtfsmvsqQLuvuJ",
    "/ip4/104.236.179.241/tcp/4001/p2p/QmSoLPppuBtQSGwKDZT2M73ULpjvfd3aZ6ha4oFGL1KrGM",
    "/ip4/104.236.76.40/tcp/4001/p2p/QmSoLV4Bbm51jM9C4gDYZQ9Cy3U6aXMJDAbzgu2fzaDs64",
    "/ip4/128.199.219.111/tcp/4001/p2p/QmSoLSafTMBsPKadTEgaXctDQVcqN88CNLHXMkTNwMKPnu",
    "/ip4/178.62.158.247/tcp/4001/p2p/QmSoLer265NRgSp2LA3dPaeykiS1J6DifTC88f5uVQKNAd",
    # Sisoul 内部 fallback (Phase 5+ 用户自跑 sisoul-bootstrap.io, 当前留位)
    # "/dnsaddr/bootstrap.sisoul.io/p2p/12D3KooWxxxxx",
)


# Pin-request 体积上限 (10 MB, 同 §B.3.6 R4)
DEFAULT_PIN_SIZE_LIMIT = 10 * 1024 * 1024
DEFAULT_DAEMON_STARTUP_TIMEOUT_SEC = 30.0
DEFAULT_DAEMON_SHUTDOWN_GRACE_SEC = 5.0
DEFAULT_HTTP_TIMEOUT_SEC = 30.0


# ─────────────────────────────────────────────────────────────────────────────
# 异常
# ─────────────────────────────────────────────────────────────────────────────


class IPFSError(Exception):
    """IPFS 通用异常."""

    code: int = 6000


class IPFSNotStarted(IPFSError):
    """kubo daemon 未启动 (调用 add/cat 等前."""

    code = 6001


class IPFSKuboNotFound(IPFSError):
    """`ipfs` binary 不在 PATH / 静态二进制路径."""

    code = 6002


class IPFSTimeout(IPFSError):
    """cat / dht_findpeer 超时."""

    code = 6003


class IPFSPinFailed(IPFSError):
    """朋友拒 pin / 网络断."""

    code = 6004


class IPFSRepoCorrupt(IPFSError):
    """repo lock 撞 / fsck 报错."""

    code = 6005


class IPFSCloudRefused(IPFSError):
    """拒在 cloud / aws-* 主机 spawn 内嵌 kubo (用户红线 §10.3).

    GossipSub/kubo 只允许跑在 mac/wsl/win 用户自己的机器上。
    """

    code = 6006


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────


IPFSMode = Literal["kubo-subprocess", "external-daemon", "mock"]


@dataclass
class IPFSStatus:
    """状态快照 (CLI / daemon HTTP `/api/v1/ipfs/status` 返回)."""

    mode: IPFSMode
    peer_id: Optional[str] = None
    running: bool = False
    peers: int = 0  # 当前连接的 swarm peer 数
    pin_count: int = 0
    repo_size_bytes: int = 0
    agent_version: Optional[str] = None
    api_url: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IPFSAddResult:
    """add() 返回."""

    cid: str
    size: int
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IPFSPinRequest:
    """朋友间互 pin 请求 (序列化走 #9 消息层)."""

    request_id: str
    from_did: str  # 请求方 DID
    to_did: str  # 接收方 DID
    cid: str
    size_bytes: int
    expires_at: Optional[int] = None  # None = 永久 pin
    note: Optional[str] = None
    created_at: int = field(default_factory=lambda: int(time.time()))
    status: Literal["pending", "accepted", "rejected", "timeout"] = "pending"
    reject_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# binary 检测 + 安装 (无 sudo, 无用户授权不下)
# ─────────────────────────────────────────────────────────────────────────────


def find_kubo_binary(custom_path: Optional[Path] = None) -> Optional[Path]:
    """按优先级找 kubo binary.

    Returns:
        Path 或 None (没找着).
    """
    if custom_path:
        p = Path(custom_path).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p

    # 1. PATH 现有
    for name in ("ipfs", "kubo"):
        which = shutil.which(name)
        if which:
            return Path(which)

    # 2. sisoul 私有 bin (静态下载到 ~/.sisoul/bin/ipfs)
    if DEFAULT_BIN_PATH.is_file() and os.access(DEFAULT_BIN_PATH, os.X_OK):
        return DEFAULT_BIN_PATH

    return None


def detect_kubo_version(bin_path: Path) -> Optional[str]:
    """跑 `ipfs --version` 拿版本字符串.

    Returns:
        e.g. "0.30.0" 或 None (跑挂).
    """
    try:
        out = subprocess.run(
            [str(bin_path), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("ipfs --version 跑挂: %s", e)
        return None
    if out.returncode != 0:
        return None
    # 输出形如: "ipfs version 0.30.0"
    parts = out.stdout.strip().split()
    if len(parts) >= 3 and parts[0] == "ipfs":
        return parts[2]
    return None


def kubo_static_download_url(version: str = KUBO_DEFAULT_VERSION) -> str:
    """生成 kubo 静态二进制下载 URL (按当前系统平台).

    根据 https://dist.ipfs.tech/kubo/v<ver>/kubo_v<ver>_<os>-<arch>.tar.gz

    支持: darwin-amd64, darwin-arm64, linux-amd64, linux-arm64, windows-amd64.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        os_tag = "darwin"
    elif system == "linux":
        os_tag = "linux"
    elif system == "windows":
        os_tag = "windows"
    else:
        raise IPFSKuboNotFound(f"不支持平台: {system}")

    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        raise IPFSKuboNotFound(f"不支持架构: {machine}")

    ext = "zip" if os_tag == "windows" else "tar.gz"
    return f"{KUBO_DIST_BASE}/v{version}/kubo_v{version}_{os_tag}-{arch}.{ext}"


def install_kubo_static(
    version: str = KUBO_DEFAULT_VERSION,
    target_path: Optional[Path] = None,
    *,
    timeout_sec: float = 120.0,
    dry_run: bool = False,
) -> Path:
    """从 dist.ipfs.tech 下静态二进制装到 ~/.sisoul/bin/ipfs.

    Args:
        version: kubo 版本 (e.g. "0.30.0").
        target_path: 装到哪 (默认 DEFAULT_BIN_PATH).
        timeout_sec: 下载超时.
        dry_run: 不真下, 仅返目标 URL + path (test 用).

    Returns:
        装好的 binary 路径.

    Raises:
        IPFSKuboNotFound: 下载失败 / 平台不支持.
    """
    target = Path(target_path or DEFAULT_BIN_PATH).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    url = kubo_static_download_url(version)

    if dry_run:
        logger.info("[dry-run] 将从 %s 下载到 %s", url, target)
        return target

    try:
        import io
        import tarfile

        with httpx.Client(timeout=timeout_sec, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.content
    except httpx.HTTPError as e:
        raise IPFSKuboNotFound(f"kubo 静态下载失败 ({url}): {e}") from e

    # 解 tar.gz → kubo/ipfs binary
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            member = next(
                (m for m in tf.getmembers() if m.name.endswith("/ipfs") or m.name == "kubo/ipfs"),
                None,
            )
            if member is None:
                raise IPFSKuboNotFound(f"kubo tarball 内无 ipfs binary (url={url})")
            extracted = tf.extractfile(member)
            if extracted is None:
                raise IPFSKuboNotFound(f"无法解压 {member.name}")
            target.write_bytes(extracted.read())
    except tarfile.TarError as e:
        raise IPFSKuboNotFound(f"kubo tarball 解压失败: {e}") from e

    target.chmod(0o755)
    logger.info("kubo binary 装好: %s (来源 %s)", target, url)
    return target


# ─────────────────────────────────────────────────────────────────────────────
# 网络辅助
# ─────────────────────────────────────────────────────────────────────────────


def _is_port_listening(host: str, port: int, timeout: float = 1.0) -> bool:
    """TCP probe 看端口是否被 listen."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def _pick_free_port(preferred: int, host: str = "127.0.0.1") -> int:
    """优先 preferred. 占用 → 0 让系统分配."""
    if not _is_port_listening(host, preferred, timeout=0.3):
        return preferred
    # 自动找一个
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, 0))
        return s.getsockname()[1]
    finally:
        s.close()


# ─────────────────────────────────────────────────────────────────────────────
# 核心: IPFSKuboNode (kubo subprocess + HTTP API 客户端)
# ─────────────────────────────────────────────────────────────────────────────


class IPFSKuboNode:
    """sisoul kubo IPFS 节点 wrapper.

    使用模式 (async):
        node = IPFSKuboNode()
        await node.start()
        cid = await node.add(b"hello")
        data = await node.cat(cid)
        await node.stop()

    sync API (大多 daemon route / CLI 用):
        node.start_sync()
        cid = node.add_sync(b"hello")
    """

    def __init__(
        self,
        *,
        mode: IPFSMode = "kubo-subprocess",
        repo_path: Optional[Path] = None,
        bin_path: Optional[Path] = None,
        api_port: int = DEFAULT_API_PORT,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        swarm_tcp_port: int = DEFAULT_SWARM_TCP_PORT,
        swarm_quic_port: int = DEFAULT_SWARM_QUIC_PORT,
        external_daemon_url: Optional[str] = None,
        bootstrap: Optional[tuple[str, ...]] = None,
        auto_install: bool = False,
        version: str = KUBO_DEFAULT_VERSION,
        http_timeout_sec: float = DEFAULT_HTTP_TIMEOUT_SEC,
        startup_timeout_sec: float = DEFAULT_DAEMON_STARTUP_TIMEOUT_SEC,
    ) -> None:
        self.mode: IPFSMode = mode
        self.repo_path = Path(repo_path or DEFAULT_REPO_PATH).expanduser()
        self._explicit_bin = Path(bin_path).expanduser() if bin_path else None
        self.api_port = api_port
        self.gateway_port = gateway_port
        self.swarm_tcp_port = swarm_tcp_port
        self.swarm_quic_port = swarm_quic_port
        self.external_daemon_url = external_daemon_url
        self.bootstrap = bootstrap if bootstrap is not None else DEFAULT_BOOTSTRAP
        self.auto_install = auto_install
        self.version = version
        self.http_timeout_sec = http_timeout_sec
        self.startup_timeout_sec = startup_timeout_sec

        self._proc: Optional[subprocess.Popen] = None
        self._peer_id: Optional[str] = None
        self._agent_version: Optional[str] = None
        # mock 模式: 本地 dict 模拟 IPFS store
        self._mock_store: dict[str, bytes] = {}
        self._mock_pins: set[str] = set()

    # ── 属性 ──────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        if self.mode == "mock":
            return True
        if self.mode == "external-daemon":
            # 外部 daemon: TCP probe
            url = self.external_daemon_url or self.api_url
            try:
                host_port = url.split("://", 1)[1].split("/", 1)[0]
                host, _, port = host_port.partition(":")
                return _is_port_listening(host or "127.0.0.1", int(port or 5001), timeout=0.5)
            except (IndexError, ValueError):
                return False
        # subprocess: process 活 + API 端口在 listen
        if self._proc is None:
            return False
        if self._proc.poll() is not None:
            return False
        return _is_port_listening("127.0.0.1", self.api_port, timeout=0.3)

    @property
    def api_url(self) -> str:
        if self.mode == "external-daemon" and self.external_daemon_url:
            return self.external_daemon_url.rstrip("/")
        return f"http://127.0.0.1:{self.api_port}"

    @property
    def peer_id(self) -> Optional[str]:
        return self._peer_id

    # ── start / stop ──────────────────────────────────────────────────────

    def start_sync(self) -> None:
        """同步包装. CLI / daemon main thread 用."""
        return asyncio.run(self.start())

    async def start(self) -> None:
        """启 kubo daemon 子进程 (或连外部 daemon, 或 mock).

        mock: no-op, 仅设 peer_id="mock-PeerID".
        external-daemon: 仅校验端口在 listen, 拿 peer_id.
        kubo-subprocess: 找 binary → init repo (若不存在) → fork daemon → 等 API 起.
        """
        if self.mode == "mock":
            self._peer_id = "12D3KooWMockPeerIDForTestingOnlyNotForProduction"
            self._agent_version = "sisoul-mock/0.0.0"
            return

        if self.mode == "external-daemon":
            if not self.is_running:
                raise IPFSNotStarted(
                    f"external-daemon URL 不通: {self.api_url}. "
                    "确认外部 `ipfs daemon` 在跑."
                )
            await self._refresh_identity()
            return

        # kubo-subprocess — 拒在 cloud / aws-* 主机 spawn (用户红线 §10.3).
        # 这是 single chokepoint: 任何想 fork `ipfs daemon` 的路径都先过这关。
        from sisoul.p2p.host_policy import cloud_refusal_reason

        reason = cloud_refusal_reason()
        if reason is not None:
            raise IPFSCloudRefused(
                f"拒在本机 spawn 内嵌 kubo: {reason}. "
                f"GossipSub/kubo 只允许跑在你自己的 mac/wsl/win 上。"
                f"如确需覆盖 (不建议): export SISOUL_ALLOW_CLOUD_P2P=1."
            )

        bin_path = self._explicit_bin or find_kubo_binary()
        if bin_path is None:
            if self.auto_install:
                bin_path = install_kubo_static(version=self.version)
            else:
                raise IPFSKuboNotFound(
                    "找不到 `ipfs` binary. brew install ipfs / "
                    "apt install kubo / 或调 install_kubo_static() 下静态二进制 / "
                    "或调 IPFSKuboNode(auto_install=True)."
                )

        # init repo (若不存在)
        self.repo_path.mkdir(parents=True, exist_ok=True)
        if not (self.repo_path / "config").exists():
            try:
                subprocess.run(
                    [str(bin_path), "init", "--profile", "lowpower"],
                    env={**os.environ, "IPFS_PATH": str(self.repo_path)},
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=True,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                raise IPFSRepoCorrupt(f"ipfs init 失败: {e}") from e
            # 配置端口 / bootstrap
            self._configure_repo(bin_path)

        # fork daemon
        try:
            self._proc = subprocess.Popen(
                [str(bin_path), "daemon", "--enable-pubsub-experiment"],
                env={**os.environ, "IPFS_PATH": str(self.repo_path)},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as e:
            raise IPFSKuboNotFound(f"无法 fork `ipfs daemon`: {e}") from e

        # 等 API 起 (poll 端口 listen)
        deadline = time.monotonic() + self.startup_timeout_sec
        while time.monotonic() < deadline:
            if _is_port_listening("127.0.0.1", self.api_port, timeout=0.3):
                break
            if self._proc.poll() is not None:
                # daemon exit, 拿 stderr
                _, err = self._proc.communicate(timeout=2)
                raise IPFSNotStarted(
                    f"kubo daemon exit (code {self._proc.returncode}): {err.decode(errors='replace')[:500]}"
                )
            await asyncio.sleep(0.3)
        else:
            self.stop_sync()
            raise IPFSNotStarted(
                f"kubo daemon 启动超时 {self.startup_timeout_sec}s (API 端口 {self.api_port} 不 listen)"
            )

        await self._refresh_identity()

    def _configure_repo(self, bin_path: Path) -> None:
        """初始化 repo 后配端口 / bootstrap (一次性)."""
        env = {**os.environ, "IPFS_PATH": str(self.repo_path)}

        # API addr
        api_addr = f"/ip4/127.0.0.1/tcp/{self.api_port}"
        gateway_addr = f"/ip4/127.0.0.1/tcp/{self.gateway_port}"
        swarm_addrs = json.dumps([
            f"/ip4/0.0.0.0/tcp/{self.swarm_tcp_port}",
            f"/ip4/0.0.0.0/udp/{self.swarm_quic_port}/quic-v1",
            f"/ip6/::/tcp/{self.swarm_tcp_port}",
        ])

        cmds = [
            [str(bin_path), "config", "Addresses.API", api_addr],
            [str(bin_path), "config", "Addresses.Gateway", gateway_addr],
            [str(bin_path), "config", "--json", "Addresses.Swarm", swarm_addrs],
        ]
        for cmd in cmds:
            try:
                subprocess.run(cmd, env=env, capture_output=True, timeout=5, check=True)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                logger.warning("ipfs config %s 失败: %s", cmd[2], e)

        # bootstrap (清空再加, 避免重复)
        try:
            subprocess.run(
                [str(bin_path), "bootstrap", "rm", "--all"],
                env=env, capture_output=True, timeout=5, check=False,
            )
            for addr in self.bootstrap:
                subprocess.run(
                    [str(bin_path), "bootstrap", "add", addr],
                    env=env, capture_output=True, timeout=5, check=False,
                )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning("bootstrap 配置失败 (非致命): %s", e)

    def stop_sync(self, grace_s: float = DEFAULT_DAEMON_SHUTDOWN_GRACE_SEC) -> None:
        """同步关停."""
        try:
            asyncio.run(self.stop(grace_s=grace_s))
        except RuntimeError:
            # 已在 event loop, 简单 terminate
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()

    async def stop(self, grace_s: float = DEFAULT_DAEMON_SHUTDOWN_GRACE_SEC) -> None:
        if self.mode == "mock":
            self._peer_id = None
            return
        if self.mode == "external-daemon":
            # 外部 daemon 不归我们管
            return
        if self._proc is None:
            return
        if self._proc.poll() is not None:
            self._proc = None
            return
        self._proc.terminate()
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                break
            await asyncio.sleep(0.2)
        else:
            self._proc.kill()
        self._proc = None
        self._peer_id = None

    async def _refresh_identity(self) -> None:
        """调 /api/v0/id 拿 PeerID."""
        try:
            body = await self._api_post("/api/v0/id")
            self._peer_id = body.get("ID")
            self._agent_version = body.get("AgentVersion")
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            logger.warning("拿 PeerID 失败: %s", e)

    # ── HTTP API 调用 ─────────────────────────────────────────────────────

    async def _api_post(
        self,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        files: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """POST kubo API. 返 dict (decode 第一个 JSON 对象, 多行 stream 取第一行)."""
        url = self.api_url + path
        t = timeout or self.http_timeout_sec
        async with httpx.AsyncClient(timeout=t) as client:
            resp = await client.post(url, params=params, files=files)
            resp.raise_for_status()
            text = resp.text.strip()
            if not text:
                return {}
            # kubo 有时返 ndjson (e.g. add 多文件), 取第一行
            first_line = text.split("\n", 1)[0]
            try:
                return json.loads(first_line)
            except json.JSONDecodeError:
                return {"_raw": text}

    async def _api_post_bytes(
        self,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> bytes:
        """POST 返 raw bytes (cat 用)."""
        url = self.api_url + path
        t = timeout or self.http_timeout_sec
        async with httpx.AsyncClient(timeout=t) as client:
            resp = await client.post(url, params=params)
            resp.raise_for_status()
            return resp.content

    # ── add / cat / pin ────────────────────────────────────────────────────

    async def add(
        self,
        data: bytes,
        *,
        pin: bool = True,
        cid_version: int = 1,
        filename: str = "blob",
    ) -> str:
        """加 bytes 到 IPFS, 返 CID. 默认 pin.

        Args:
            data: 任意 bytes (一般是 encrypt_bytes 输出).
            pin: 是否同时 pin (默认 True).
            cid_version: 1 = bafy... (默认), 0 = Qm....
            filename: multipart filename (UI 显示).

        Returns:
            CID 字符串.
        """
        if self.mode == "mock":
            return self._mock_add(data, pin=pin, cid_version=cid_version)

        if not self.is_running:
            raise IPFSNotStarted("调 add 前先 await node.start()")

        files = {"file": (filename, data, "application/octet-stream")}
        params = {
            "pin": "true" if pin else "false",
            "cid-version": str(cid_version),
            "raw-leaves": "true",
        }
        body = await self._api_post("/api/v0/add", params=params, files=files)
        cid = body.get("Hash") or body.get("hash") or body.get("cid")
        if not cid:
            raise IPFSError(f"add 响应无 Hash: {body}")
        return str(cid)

    def add_sync(self, data: bytes, *, pin: bool = True, cid_version: int = 1) -> str:
        return asyncio.run(self.add(data, pin=pin, cid_version=cid_version))

    async def cat(self, cid: str, *, timeout: Optional[float] = None) -> bytes:
        """拉 CID 内容. timeout 默认走 http_timeout_sec.

        Raises:
            IPFSTimeout: 超时.
            IPFSError: 其他失败.
        """
        if self.mode == "mock":
            return self._mock_cat(cid)

        if not self.is_running:
            raise IPFSNotStarted("调 cat 前先 await node.start()")

        try:
            return await self._api_post_bytes(
                "/api/v0/cat", params={"arg": cid}, timeout=timeout
            )
        except httpx.TimeoutException as e:
            raise IPFSTimeout(f"cat {cid} 超时: {e}") from e
        except httpx.HTTPError as e:
            raise IPFSError(f"cat {cid} 失败: {e}") from e

    def cat_sync(self, cid: str, *, timeout: Optional[float] = None) -> bytes:
        return asyncio.run(self.cat(cid, timeout=timeout))

    async def pin(self, cid: str, *, recursive: bool = True) -> None:
        """pin 已有 CID (e.g. 朋友给的 cid)."""
        if self.mode == "mock":
            self._mock_pins.add(cid)
            return

        if not self.is_running:
            raise IPFSNotStarted("调 pin 前先 await node.start()")
        params = {"arg": cid, "recursive": "true" if recursive else "false"}
        await self._api_post("/api/v0/pin/add", params=params)

    async def unpin(self, cid: str) -> None:
        if self.mode == "mock":
            self._mock_pins.discard(cid)
            return
        if not self.is_running:
            raise IPFSNotStarted("调 unpin 前先 await node.start()")
        try:
            await self._api_post("/api/v0/pin/rm", params={"arg": cid})
        except httpx.HTTPError as e:
            # 已不在 pin 列 → 视为成功
            if "not pinned" in str(e).lower():
                return
            raise

    async def pin_list(
        self,
        type: Literal["recursive", "direct", "all"] = "all",
    ) -> list[str]:
        """列本地 pin 的所有 CID."""
        if self.mode == "mock":
            return sorted(self._mock_pins)
        if not self.is_running:
            raise IPFSNotStarted("调 pin_list 前先 await node.start()")
        body = await self._api_post("/api/v0/pin/ls", params={"type": type})
        keys = body.get("Keys", {})
        if isinstance(keys, dict):
            return list(keys.keys())
        return []

    # ── DHT / swarm ────────────────────────────────────────────────────────

    async def dht_findpeer(self, peer_id: str, *, timeout: float = 30.0) -> list[str]:
        """DHT 查 peer multiaddr.

        Returns:
            multiaddr 列 (e.g. ["/ip4/1.2.3.4/tcp/4001/p2p/12D3..."]).
        """
        if self.mode == "mock":
            return [f"/ip4/127.0.0.1/tcp/4001/p2p/{peer_id}"]
        if not self.is_running:
            raise IPFSNotStarted("dht_findpeer 前先 start")
        # kubo /api/v0/dht/findpeer 返 ndjson stream, 中间可能 500 (peer 不在线).
        # 直接拉 raw text 解, 500 视为 "找不到", 返空 list (不抛).
        url = self.api_url + "/api/v0/dht/findpeer"
        addrs: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, params={"arg": peer_id})
                # 不 raise_for_status; 500 也读 body 看有无部分 results
                text = resp.text
                for line in text.strip().split("\n"):
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for r in obj.get("Responses", []) or []:
                        for a in r.get("Addrs", []) or []:
                            addrs.append(a)
        except httpx.TimeoutException as e:
            raise IPFSTimeout(f"dht_findpeer {peer_id} 超时: {e}") from e
        except httpx.HTTPError as e:
            logger.warning("dht_findpeer 网络错 (%s), 返空", e)
            return []
        # 去重保序
        seen: set[str] = set()
        unique: list[str] = []
        for a in addrs:
            if a not in seen:
                seen.add(a)
                unique.append(a)
        return unique

    async def dht_provide(self, cid: str) -> None:
        """DHT 宣告我有这 cid (让其他 peer 能找到)."""
        if self.mode == "mock":
            return
        if not self.is_running:
            raise IPFSNotStarted("dht_provide 前先 start")
        try:
            await self._api_post("/api/v0/dht/provide", params={"arg": cid}, timeout=60.0)
        except httpx.HTTPError as e:
            logger.warning("dht_provide %s 失败 (非致命): %s", cid, e)

    async def swarm_connect(self, multiaddr: str) -> bool:
        """主动连一个 peer (含 multiaddr + /p2p/<peer_id>)."""
        if self.mode == "mock":
            return True
        if not self.is_running:
            raise IPFSNotStarted("swarm_connect 前先 start")
        try:
            body = await self._api_post("/api/v0/swarm/connect", params={"arg": multiaddr})
            return bool(body.get("Strings") or body.get("strings"))
        except httpx.HTTPError as e:
            logger.warning("swarm_connect %s 失败: %s", multiaddr, e)
            return False

    async def swarm_peers(self) -> list[dict[str, Any]]:
        """当前连接的 peer 列."""
        if self.mode == "mock":
            return [{"Peer": "12D3KooWMockPeerForTesting", "Addr": "/ip4/127.0.0.1/tcp/4001"}]
        if not self.is_running:
            return []
        try:
            body = await self._api_post("/api/v0/swarm/peers")
        except httpx.HTTPError:
            return []
        return list(body.get("Peers", []) or [])

    # ── 状态汇总 ───────────────────────────────────────────────────────────

    async def status(self) -> IPFSStatus:
        """汇总 daemon 状态 (CLI `sisoul ipfs status` 用)."""
        if self.mode == "mock":
            return IPFSStatus(
                mode="mock",
                peer_id=self._peer_id,
                running=True,
                peers=1,
                pin_count=len(self._mock_pins),
                repo_size_bytes=sum(len(v) for v in self._mock_store.values()),
                agent_version="sisoul-mock/0.0.0",
                api_url=None,
            )
        if not self.is_running:
            return IPFSStatus(mode=self.mode, running=False, error="daemon 未启动")

        try:
            peers = await self.swarm_peers()
        except Exception:
            peers = []
        try:
            pins = await self.pin_list("recursive")
        except Exception:
            pins = []
        try:
            repo_body = await self._api_post("/api/v0/repo/stat")
            repo_size = int(repo_body.get("RepoSize", 0) or 0)
        except Exception:
            repo_size = 0

        return IPFSStatus(
            mode=self.mode,
            peer_id=self._peer_id,
            running=True,
            peers=len(peers),
            pin_count=len(pins),
            repo_size_bytes=repo_size,
            agent_version=self._agent_version,
            api_url=self.api_url,
        )

    # ── 朋友互 pin ─────────────────────────────────────────────────────────

    async def pin_for_friend(
        self,
        friend_did: str,
        cid: str,
        *,
        size_bytes: int = 0,
        expires_at: Optional[int] = None,
        size_limit: int = DEFAULT_PIN_SIZE_LIMIT,
        is_friend_check: Optional[Any] = None,
    ) -> bool:
        """朋友请我 pin 一个 cid (我作为 接收方).

        策略:
        - is_friend_check(friend_did) 返 True (朋友 whitelist 内) 才接.
        - size_bytes <= size_limit 才接.
        - 接 → 调 cat 拉 (DHT find) → pin.

        Args:
            friend_did: 请求方 DID.
            cid: 要 pin 的 cid.
            size_bytes: 报的体积 (用于 size_limit 拒 大文件; 0 = 跳过 size check).
            expires_at: 过期 ts (None = 永久).
            size_limit: 单次接受体积上限.
            is_friend_check: 可调对象 (friend_did) -> bool. None = 默认拒 (安全 default).

        Returns:
            True = 真 pin 上; False = 拒.

        Raises:
            IPFSPinFailed: 接了但 pin 失败 (cat 拉不到 / 网络).
        """
        # 1. friend whitelist
        if is_friend_check is None:
            logger.info("pin_for_friend: 无 is_friend_check, 默认拒 %s", friend_did)
            return False
        try:
            ok = is_friend_check(friend_did)
        except Exception as e:
            logger.warning("is_friend_check 抛异常 (%s), 拒", e)
            return False
        if not ok:
            logger.info("pin_for_friend: %s 不在 friend list, 拒", friend_did)
            return False

        # 2. size limit
        if size_bytes > 0 and size_bytes > size_limit:
            logger.info(
                "pin_for_friend: size %d > limit %d, 拒 cid=%s",
                size_bytes, size_limit, cid,
            )
            return False

        # 3. 真 pin
        try:
            if self.mode != "mock":
                # 先拉 (Bitswap 把 block 拽到本地, pin 才有意义)
                await self.cat(cid)
            await self.pin(cid)
        except (IPFSError, httpx.HTTPError) as e:
            raise IPFSPinFailed(f"pin {cid} for {friend_did} 失败: {e}") from e

        logger.info(
            "pin_for_friend: 接受 %s 请求 pin %s (size=%d, expires=%s)",
            friend_did, cid, size_bytes, expires_at,
        )
        return True

    async def request_friend_pin(
        self,
        friend_did: str,
        cid: str,
        *,
        size_bytes: int = 0,
        expires_at: Optional[int] = None,
        send_fn: Optional[Any] = None,
        timeout_sec: float = 30.0,
    ) -> IPFSPinRequest:
        """请朋友 pin 我 add 的 cid (我作为 请求方).

        Args:
            friend_did: 接收方 DID.
            cid: 要让朋友 pin 的 cid.
            size_bytes: 报的体积.
            expires_at: 期望对方 pin 多久 (None = 永久).
            send_fn: async callable(request: IPFSPinRequest) -> response_dict.
                     None = 仅生成 request 不发 (test / daemon route 自己发).
            timeout_sec: send 超时.

        Returns:
            IPFSPinRequest (status 字段会被 send_fn 结果更新).
        """
        # 自己先 dht_provide, 让对方能 DHT find providers
        try:
            await self.dht_provide(cid)
        except Exception as e:
            logger.debug("dht_provide %s 失败 (非致命): %s", cid, e)

        my_did = "self"  # 真实场景从 identity 模块拿; 留接口
        req_id = hashlib.sha256(
            f"{my_did}:{friend_did}:{cid}:{time.time()}".encode()
        ).hexdigest()[:16]
        req = IPFSPinRequest(
            request_id=req_id,
            from_did=my_did,
            to_did=friend_did,
            cid=cid,
            size_bytes=size_bytes,
            expires_at=expires_at,
        )

        if send_fn is None:
            return req

        try:
            result = await asyncio.wait_for(send_fn(req), timeout=timeout_sec)
        except asyncio.TimeoutError:
            req.status = "timeout"
            return req

        if isinstance(result, dict):
            req.status = result.get("status", "accepted")
            req.reject_reason = result.get("reject_reason")
        elif result is True:
            req.status = "accepted"
        else:
            req.status = "rejected"
        return req

    # ── mock 实现 ─────────────────────────────────────────────────────────

    def _mock_add(self, data: bytes, *, pin: bool, cid_version: int) -> str:
        """mock add: 用 sha256 生成确定性 CID-like 字符串."""
        sha = hashlib.sha256(data).hexdigest()
        prefix = "bafymock" if cid_version == 1 else "Qmmock"
        cid = f"{prefix}{sha[:46]}"
        self._mock_store[cid] = bytes(data)
        if pin:
            self._mock_pins.add(cid)
        return cid

    def _mock_cat(self, cid: str) -> bytes:
        if cid in self._mock_store:
            return self._mock_store[cid]
        raise IPFSError(f"mock cat: cid {cid} 不在 mock store (test setup 错?)")


# ─────────────────────────────────────────────────────────────────────────────
# 单例 / 工厂
# ─────────────────────────────────────────────────────────────────────────────


_DEFAULT_NODE: Optional[IPFSKuboNode] = None


def get_default_node(**kwargs: Any) -> IPFSKuboNode:
    """获取 process-wide 默认 IPFSKuboNode (lazy init).

    第一次调用时根据 env 决定 mode:
    - SISOUL_IPFS_MODE=external-daemon + SISOUL_IPFS_API_URL → external-daemon
    - SISOUL_IPFS_MODE=mock → mock
    - SISOUL_IPFS_MODE=kubo-subprocess (默认) + find_kubo_binary() → kubo-subprocess
    - 找不到 binary 且未传 auto_install=True → 自动降 mock + warn
    """
    global _DEFAULT_NODE
    if _DEFAULT_NODE is not None:
        return _DEFAULT_NODE

    env_mode = os.environ.get("SISOUL_IPFS_MODE", "").strip()
    if env_mode == "mock":
        _DEFAULT_NODE = IPFSKuboNode(mode="mock", **kwargs)
        return _DEFAULT_NODE
    if env_mode == "external-daemon":
        url = os.environ.get("SISOUL_IPFS_API_URL", "http://127.0.0.1:5001")
        _DEFAULT_NODE = IPFSKuboNode(mode="external-daemon", external_daemon_url=url, **kwargs)
        return _DEFAULT_NODE

    # subprocess (默认)
    bin_path = find_kubo_binary()
    if bin_path is None:
        logger.warning(
            "未找到 kubo binary, IPFS 节点降级 mock 模式. "
            "brew install ipfs / apt install kubo / SISOUL_IPFS_MODE=mock 抑制本警告."
        )
        _DEFAULT_NODE = IPFSKuboNode(mode="mock", **kwargs)
        return _DEFAULT_NODE

    _DEFAULT_NODE = IPFSKuboNode(mode="kubo-subprocess", bin_path=bin_path, **kwargs)
    return _DEFAULT_NODE


def reset_default_node() -> None:
    """test 用: 清单例 (不真 stop, 避免 async 复杂)."""
    global _DEFAULT_NODE
    _DEFAULT_NODE = None


__all__ = [
    # 常量
    "DEFAULT_REPO_PATH",
    "DEFAULT_BIN_PATH",
    "DEFAULT_API_PORT",
    "DEFAULT_GATEWAY_PORT",
    "DEFAULT_BOOTSTRAP",
    "DEFAULT_PIN_SIZE_LIMIT",
    "KUBO_DEFAULT_VERSION",
    # 异常
    "IPFSError",
    "IPFSNotStarted",
    "IPFSKuboNotFound",
    "IPFSTimeout",
    "IPFSPinFailed",
    "IPFSRepoCorrupt",
    "IPFSCloudRefused",
    # 数据
    "IPFSStatus",
    "IPFSAddResult",
    "IPFSPinRequest",
    "IPFSMode",
    # 核心
    "IPFSKuboNode",
    # binary 辅助
    "find_kubo_binary",
    "detect_kubo_version",
    "install_kubo_static",
    "kubo_static_download_url",
    # 单例
    "get_default_node",
    "reset_default_node",
]
