"""sisoul · Helios light client subprocess wrapper.

实现 §31 §2 模块 #4 (v1.0-decentralized · Wave A agent-1 · 2026-05-19 ship).

启 helios CLI (a16z https://github.com/a16z/helios) 做 subprocess, 暴露 trustless
JSON-RPC at 127.0.0.1:854X. Python 客户端通过 httpx HTTP 调用. 所有 read 都被 helios
本地 Merkle proof 验过 → 即使 untrusted execution RPC 撒谎也会被拒.

不用 pyo3 / helios-py binding (社区 PR 不稳定, FFI 复杂), 走 subprocess.Popen + HTTP
是简单且可靠的路径. helios 0.11.1 binary ~80MB, mainnet sync ~15-60s.

链支持矩阵 (helios 0.11.1 CLI):
    | 命令路径         | 链                              | 我们用法                     |
    |------------------|---------------------------------|------------------------------|
    | helios ethereum  | mainnet / sepolia / holesky     | mainnet head sync (ETH+EAS)  |
    | helios opstack   | op-mainnet / base / base-sepolia| base sepolia EAS attestation |
    |                  | / worldchain / zora / unichain  |                              |
    | helios linea     | linea mainnet                   | 未用                         |
    | 未支持            | arbitrum / op-sepolia / zksync  | fallback 公共 RPC + 警告     |

集成路径:
- `eas.py._verify_testnet_rpc()` 走 helios.call() (chain 在 HELIOS_NATIVE_CHAINS) 或
  fallback httpx 直连公共 RPC + 警告
- `arweave.py` 跟 helios 关系弱 (Arweave gateway 是 web2, 不归 helios 管). 仅 eth-wallet
  签名相关 web3 调用走 helios (本 wave 暂不实测真签).

约束 (本 wave):
- 不真打 mainnet tx (helios 本身 read-only 是 OK 的)
- subprocess.Popen 必须能干净停 (terminate + wait, 防 zombie)
- helios binary 缺失 → fallback 公共 RPC + warn (default allow_fallback=True)
- 4 链并行启 RAM 1.5-2GB, pytest smoke 只启 1 链 ethereum mainnet (RAM <300MB)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

import httpx

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────


# helios 0.11.1 CLI 原生支持的 chain key → (subcommand, --network 值).
# 缺失链 (arbitrum / op-sepolia / zksync) 自动走 fallback 公共 RPC.
HELIOS_NATIVE_CHAINS: dict[str, tuple[str, str]] = {
    "ethereum": ("ethereum", "mainnet"),
    "ethereum-sepolia": ("ethereum", "sepolia"),
    "ethereum-holesky": ("ethereum", "holesky"),
    "op-mainnet": ("opstack", "op-mainnet"),
    "base": ("opstack", "base"),
    "base-sepolia": ("opstack", "base-sepolia"),
    "worldchain": ("opstack", "worldchain"),
    "zora": ("opstack", "zora"),
    "unichain": ("opstack", "unichain"),
    "linea": ("linea", "linea"),
}

# 默认 execution-rpc (untrusted, helios 会 Merkle proof 验). 用户应替换为自己跑的 geth
# 或更可信源. 这些是"任意 1 个公共 endpoint, 撒谎被拒"的 fallback list.
DEFAULT_EXECUTION_RPCS: dict[str, list[str]] = {
    "ethereum": [
        "https://ethereum.publicnode.com",
        "https://eth.llamarpc.com",
    ],
    "ethereum-sepolia": [
        "https://ethereum-sepolia.publicnode.com",
        "https://sepolia.drpc.org",
    ],
    "op-mainnet": [
        "https://mainnet.optimism.io",
        "https://op-pokt.nodies.app",
    ],
    "base": [
        "https://mainnet.base.org",
        "https://base.llamarpc.com",
    ],
    "base-sepolia": [
        "https://sepolia.base.org",
        "https://base-sepolia.publicnode.com",
    ],
    "linea": [
        "https://rpc.linea.build",
    ],
}

# helios 默认 consensus RPC. a16z 默认 lightclientdata.org 经常 503;
# beaconcha.in 路径格式 helios 也不直接吃. 实测**关键 flag = -l (--load-external-fallback)**
# 让 helios 自动从 ethpandaops/checkpoint-sync-health-checks 列表轮询. 我们启 helios 时
# 默认带 -l, consensus_rpc 仅作为首选.
DEFAULT_CONSENSUS_RPC: str = "https://www.lightclientdata.org"

# helios binary 默认查路径.
_BINARY_LOOKUP_PATHS = (
    "helios",  # $PATH
    str(Path.home() / ".cargo" / "bin" / "helios"),
    "/usr/local/bin/helios",
    "/opt/homebrew/bin/helios",
)

# 默认 sync 超时 (单 chain). mainnet 实测 ~15-60s, 给 180s buffer.
DEFAULT_SYNC_TIMEOUT_SEC = 180.0

# 默认 RPC bind base port (multi-chain → 8545+i).
DEFAULT_BASE_PORT = 8545


# ─────────────────────────────────────────────────────────────────────────────
# 异常
# ─────────────────────────────────────────────────────────────────────────────


class HeliosError(Exception):
    """Helios 通用异常."""


class HeliosBinaryMissing(HeliosError):
    """helios binary 在 $PATH / ~/.cargo/bin/helios 都找不到."""


class HeliosSyncTimeout(HeliosError):
    """超过 sync_timeout_sec 仍未 in_sync."""


class HeliosChainNotSupported(HeliosError):
    """链不在 HELIOS_NATIVE_CHAINS 且 allow_fallback=False."""


class HeliosRpcError(HeliosError):
    """JSON-RPC 返 error 或 HTTP 失败."""


# ─────────────────────────────────────────────────────────────────────────────
# 数据
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ChainStatus:
    """单链运行时状态."""

    chain: str
    mode: Literal["helios", "fallback", "stopped"] = "stopped"
    rpc_port: int | None = None
    rpc_url: str | None = None
    pid: int | None = None
    in_sync: bool = False
    head: int = 0
    started_at: float = 0.0
    last_check_at: float = 0.0
    error: str | None = None
    untrusted_rpc: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# helper
# ─────────────────────────────────────────────────────────────────────────────


def find_helios_binary() -> str | None:
    """查 helios binary 路径. 返 abs path 或 None.

    顺序: $PATH (shutil.which) → ~/.cargo/bin/helios → /usr/local/bin/helios →
    /opt/homebrew/bin/helios.
    """
    for candidate in _BINARY_LOOKUP_PATHS:
        path = shutil.which(candidate) if "/" not in candidate else candidate
        if path and Path(path).exists() and os.access(path, os.X_OK):
            return path
    return None


def _free_port_near(base: int, used: Iterable[int] = ()) -> int:
    """给 multi-chain 分端口. 简单方案: base + i (跳过 used)."""
    used_set = set(used)
    for i in range(50):
        port = base + i
        if port not in used_set:
            return port
    raise HeliosError("无法分配端口 (50 都用完了)")


def _hex_to_int(hex_str: str | int | None) -> int:
    """0x... → int. None → 0."""
    if hex_str is None:
        return 0
    if isinstance(hex_str, int):
        return hex_str
    if not hex_str:
        return 0
    return int(hex_str, 16) if hex_str.startswith("0x") else int(hex_str)


# ─────────────────────────────────────────────────────────────────────────────
# 主类
# ─────────────────────────────────────────────────────────────────────────────


class HeliosClient:
    """Helios subprocess + HTTP JSON-RPC 客户端.

    Args:
        chains: 启的链 key list (见 HELIOS_NATIVE_CHAINS). 不在原生表的 chain →
                如果 allow_fallback=True, 走 fallback 公共 RPC + 警告; 否则抛
                HeliosChainNotSupported.
        execution_rpcs: 每链的 untrusted execution RPC (helios -e 参数; fallback
                时直接当 RPC). None = 用 DEFAULT_EXECUTION_RPCS.
        consensus_rpc: helios 的 -c 参数 (mainnet beacon LCS). 默认
                DEFAULT_CONSENSUS_RPC. 注意 helios 启时附加 -l, 自动 fallback.
        base_port: 第一个 chain 用此端口, 后续 +1.
        data_dir: helios -d (checkpoint 缓存). 默认 ~/.sisoul/helios.
        binary_path: 显式指定 helios binary, None = find_helios_binary().
        allow_fallback: helios 缺失 / chain 不支持 → 走公共 RPC + 警告.
        sync_timeout_sec: 单链 sync 等待上限.
        log_dir: helios stdout/stderr 写到这里 (chain-<key>.log). 默认 ~/.sisoul/log.
        load_external_fallback: 启时加 -l (helios CLI) 让 consensus 自动 fallback.
                                默认 True (实测 a16z 默认 endpoint 经常 503,
                                此 flag 是 sync 通的关键).

    生命周期:
        await client.start()        # spawn helios + 等 sync
        await client.wait_synced("ethereum", timeout=60)
        block = await client.eth_block_number("ethereum")
        await client.call("ethereum", "eth_call", [...])
        await client.stop()
    """

    def __init__(
        self,
        chains: list[str],
        *,
        execution_rpcs: dict[str, list[str]] | None = None,
        consensus_rpc: str = DEFAULT_CONSENSUS_RPC,
        base_port: int = DEFAULT_BASE_PORT,
        data_dir: Path | None = None,
        binary_path: str | None = None,
        allow_fallback: bool = True,
        sync_timeout_sec: float = DEFAULT_SYNC_TIMEOUT_SEC,
        log_dir: Path | None = None,
        load_external_fallback: bool = True,
    ) -> None:
        if not chains:
            raise HeliosError("chains 不能空")
        self.chains: list[str] = list(chains)
        self.execution_rpcs: dict[str, list[str]] = execution_rpcs or {
            c: list(DEFAULT_EXECUTION_RPCS.get(c, [])) for c in self.chains
        }
        self.consensus_rpc = consensus_rpc
        self.base_port = base_port
        self.data_dir = Path(data_dir or Path.home() / ".sisoul" / "helios")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.binary_path = binary_path or find_helios_binary()
        self.allow_fallback = allow_fallback
        self.sync_timeout_sec = sync_timeout_sec
        self.log_dir = Path(log_dir or Path.home() / ".sisoul" / "log")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.load_external_fallback = load_external_fallback

        # 运行时
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._status: dict[str, ChainStatus] = {c: ChainStatus(chain=c) for c in self.chains}
        self._started = False
        self._lock = asyncio.Lock()

        # validate chains 提前 (allow_fallback=False 时硬拒)
        if not self.allow_fallback:
            for c in self.chains:
                if c not in HELIOS_NATIVE_CHAINS:
                    raise HeliosChainNotSupported(
                        f"chain '{c}' 不在 helios 0.11.1 原生支持 "
                        f"({sorted(HELIOS_NATIVE_CHAINS.keys())}); "
                        "allow_fallback=True 可降级公共 RPC."
                    )

    # ── 启动 / 停止 ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """启所有 chain. helios subprocess 异步并发启, 全启后立即返 (不等 sync;
        sync 等用 wait_synced).

        binary 缺失:
            - allow_fallback=True: 全链走 fallback. status.mode='fallback'.
              warn log.
            - allow_fallback=False: HeliosBinaryMissing.
        """
        if self._started:
            return
        async with self._lock:
            if self._started:
                return

            # binary missing handling
            if self.binary_path is None:
                if not self.allow_fallback:
                    raise HeliosBinaryMissing(
                        "helios binary 未找到 ($PATH / ~/.cargo/bin/helios). "
                        "装: `cargo install --git https://github.com/a16z/helios --bin helios` "
                        "或允许 fallback (allow_fallback=True 走公共 RPC + 警告)."
                    )
                logger.warning(
                    "helios binary 缺失, 全部 chain 走 fallback 公共 RPC (不 trustless, "
                    "信任 untrusted RPC 商, 类似 Alchemy 模型). 装 helios 升级."
                )
                for c in self.chains:
                    self._setup_fallback(c)
                self._started = True
                return

            used_ports: list[int] = []
            for idx, chain in enumerate(self.chains):
                port = _free_port_near(self.base_port + idx, used_ports)
                used_ports.append(port)
                if chain in HELIOS_NATIVE_CHAINS:
                    self._spawn_helios(chain, port)
                else:
                    if self.allow_fallback:
                        logger.warning(
                            "chain '%s' helios 0.11.1 不原生支持, 走 fallback 公共 RPC + 警告. "
                            "支持列表: %s",
                            chain,
                            sorted(HELIOS_NATIVE_CHAINS.keys()),
                        )
                        self._setup_fallback(chain, port=None)
                    else:
                        raise HeliosChainNotSupported(chain)
            self._started = True

    def _spawn_helios(self, chain: str, port: int) -> None:
        """启一个 helios subprocess."""
        assert self.binary_path is not None
        subcommand, network = HELIOS_NATIVE_CHAINS[chain]
        rpcs = self.execution_rpcs.get(chain) or DEFAULT_EXECUTION_RPCS.get(chain) or []
        if not rpcs:
            raise HeliosError(f"chain '{chain}' 无 execution_rpcs 配置")
        execution_rpc = rpcs[0]  # MVP: 取第一个; v1.1 加 rotation
        cmd: list[str] = [
            self.binary_path,
            subcommand,
        ]
        # ethereum subcommand 用 -n; opstack 用 --network; linea 不需要 network 参数
        if subcommand == "opstack":
            cmd += ["--network", network]
        elif subcommand == "ethereum":
            cmd += ["-n", network]
        cmd += [
            "-e", execution_rpc,
            "-p", str(port),
            "-b", "127.0.0.1",
            "-d", str(self.data_dir / chain),
        ]
        # ethereum subcommand 接受 -c (consensus rpc); opstack 不需要 (走 ETH L1).
        # 加 -l (--load-external-fallback): consensus endpoint 失败 (e.g.
        # lightclientdata.org 经常 503) 时自动从 ethpandaops/checkpoint-sync-health-checks
        # 社区列表轮询. **实测**: 显式 -c (即使指给 a16z 自家) + -l 一起用反而易 503;
        # 不带 -c 让 helios 用 default + -l fallback 才稳 (~15s sync).
        # 用户显式传非默认 consensus_rpc 才加 -c.
        if subcommand == "ethereum":
            if self.consensus_rpc and self.consensus_rpc != DEFAULT_CONSENSUS_RPC:
                cmd += ["-c", self.consensus_rpc]
            if self.load_external_fallback:
                cmd += ["-l"]

        log_path = self.log_dir / f"helios-{chain}.log"
        log_fh = log_path.open("ab")
        env = os.environ.copy()
        # 防 helios 抓到 sisoul daemon 自身的 8545 (我们已经分配 base+i)
        env.pop("RPC_PORT", None)
        env.pop("RPC_BIND_IP", None)

        logger.info("启 helios subprocess: %s", " ".join(cmd))
        proc = subprocess.Popen(  # noqa: S603 (我们构造 cmd 列表, 无 shell)
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,  # 给独立 process group, terminate 不影响 parent
        )
        self._procs[chain] = proc
        s = self._status[chain]
        s.mode = "helios"
        s.rpc_port = port
        s.rpc_url = f"http://127.0.0.1:{port}"
        s.pid = proc.pid
        s.started_at = time.time()
        s.untrusted_rpc = execution_rpc

    def _setup_fallback(self, chain: str, port: int | None = None) -> None:
        """fallback 模式: 直接用公共 RPC, 不真启 subprocess."""
        rpcs = self.execution_rpcs.get(chain) or DEFAULT_EXECUTION_RPCS.get(chain) or []
        if not rpcs:
            # 没 RPC 就 status.error 标 unsupported, 不抛 (允许 client 整体 start 完, 个别
            # chain call 时再报错)
            s = self._status[chain]
            s.mode = "fallback"
            s.error = (
                f"chain '{chain}' 无默认 execution_rpcs, 也未自定义. "
                "请提供 execution_rpcs[chain]=[<url>]."
            )
            return
        s = self._status[chain]
        s.mode = "fallback"
        s.rpc_url = rpcs[0]
        s.rpc_port = port
        s.pid = None
        s.started_at = time.time()
        s.untrusted_rpc = rpcs[0]

    async def stop(self) -> None:
        """停所有 helios subprocess. SIGTERM → wait 5s → SIGKILL."""
        if not self._started:
            return
        async with self._lock:
            for chain, proc in list(self._procs.items()):
                if proc.poll() is not None:
                    continue
                try:
                    proc.terminate()
                except ProcessLookupError:
                    continue
                # async wait
                deadline = time.time() + 5.0
                while time.time() < deadline and proc.poll() is None:
                    await asyncio.sleep(0.1)
                if proc.poll() is None:
                    # 还在 → SIGKILL
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        proc.kill()
                self._status[chain].mode = "stopped"
                self._status[chain].pid = None
            self._procs.clear()
            self._started = False

    async def __aenter__(self) -> HeliosClient:
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    # ── sync 等待 ─────────────────────────────────────────────────────────────

    async def wait_synced(
        self, chain: str, *, timeout: float | None = None
    ) -> None:
        """等单链 in_sync. fallback 模式直接返 (公共 RPC 总是"in_sync").

        判 helios in_sync 标准: `eth_blockNumber` 返非 0 hex (即非 "out of sync" error).
        """
        timeout = timeout if timeout is not None else self.sync_timeout_sec
        deadline = time.time() + timeout
        s = self._status.get(chain)
        if s is None:
            raise HeliosError(f"chain '{chain}' 未注册 (start 前传入)")
        if s.mode == "fallback":
            # 公共 RPC 假设可用, 不等
            try:
                head = await self._raw_call(s.rpc_url, "eth_blockNumber", [])
                s.head = _hex_to_int(head)
                s.in_sync = s.head > 0
                s.last_check_at = time.time()
            except Exception as e:  # noqa: BLE001
                s.error = f"fallback RPC 失败: {e}"
                s.in_sync = False
            return

        # helios mode: poll eth_blockNumber 直到 >0 或 timeout
        last_err: str | None = None
        while time.time() < deadline:
            # check process alive
            proc = self._procs.get(chain)
            if proc and proc.poll() is not None:
                raise HeliosError(
                    f"helios {chain} subprocess exit code={proc.returncode}, "
                    f"see {self.log_dir / f'helios-{chain}.log'}"
                )
            try:
                hex_block = await self._raw_call(s.rpc_url, "eth_blockNumber", [], timeout=5.0)
                head = _hex_to_int(hex_block)
                if head > 0:
                    s.head = head
                    s.in_sync = True
                    s.last_check_at = time.time()
                    s.error = None
                    return
            except Exception as e:  # noqa: BLE001
                # helios "out of sync: N seconds behind" 错误是正常 sync 过程,
                # 此 except 把它视为 "继续 poll".
                last_err = str(e)
            await asyncio.sleep(2.0)
        raise HeliosSyncTimeout(
            f"chain '{chain}' helios sync 超时 ({timeout}s). last_err={last_err}. "
            f"log: {self.log_dir / f'helios-{chain}.log'}"
        )

    async def wait_all_synced(self, timeout: float | None = None) -> dict[str, ChainStatus]:
        """并发等所有 chain in_sync. 单链 fail 不阻塞其他."""
        results: dict[str, ChainStatus] = {}
        async def _one(c: str) -> None:
            try:
                await self.wait_synced(c, timeout=timeout)
            except Exception as e:  # noqa: BLE001
                self._status[c].in_sync = False
                self._status[c].error = str(e)
            results[c] = self._status[c]
        await asyncio.gather(*[_one(c) for c in self.chains])
        return results

    # ── RPC 调用 ──────────────────────────────────────────────────────────────

    async def call(
        self,
        chain: str,
        method: str,
        params: list[Any] | None = None,
        *,
        timeout: float = 15.0,
    ) -> Any:
        """trustless JSON-RPC call. helios 自带 Merkle proof.

        mode=helios: 走 127.0.0.1:port (Helios 验过, 是 trustless).
        mode=fallback: 走公共 RPC (不 trustless, 仅可用性 fallback).

        返 result 字段值. RPC error → HeliosRpcError.
        """
        s = self._status.get(chain)
        if s is None:
            raise HeliosError(f"chain '{chain}' 未注册")
        if s.mode == "stopped":
            raise HeliosError(f"chain '{chain}' 还没 start (or 已 stop)")
        if not s.rpc_url:
            raise HeliosError(f"chain '{chain}' 无 rpc_url (fallback 缺 RPC 配置)")
        return await self._raw_call(s.rpc_url, method, params or [], timeout=timeout)

    async def eth_block_number(self, chain: str) -> int:
        """便捷: 当前 head block number (decoded int)."""
        hex_ = await self.call(chain, "eth_blockNumber", [])
        return _hex_to_int(hex_)

    async def eth_chain_id(self, chain: str) -> int:
        """便捷: chain_id (验 RPC 一致用)."""
        hex_ = await self.call(chain, "eth_chainId", [])
        return _hex_to_int(hex_)

    async def call_with_fallback(
        self,
        chain: str,
        method: str,
        params: list[Any] | None = None,
        *,
        timeout: float = 15.0,
        public_rpc_url: str | None = None,
    ) -> tuple[Any, Literal["trustless", "trusted"]]:
        """调 helios; 失败 → 公共 RPC + warn, 返 (result, "trusted").

        public_rpc_url: 显式指定 fallback. None = 用 execution_rpcs[chain][0].
        """
        try:
            r = await self.call(chain, method, params, timeout=timeout)
            s = self._status[chain]
            verified: Literal["trustless", "trusted"] = (
                "trustless" if s.mode == "helios" else "trusted"
            )
            return r, verified
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "helios.call(%s, %s) 失败 (%s), fallback 公共 RPC", chain, method, e
            )
            fallback = public_rpc_url or (
                self.execution_rpcs.get(chain) or DEFAULT_EXECUTION_RPCS.get(chain) or [None]
            )[0]
            if not fallback:
                raise
            r = await self._raw_call(fallback, method, params or [], timeout=timeout)
            return r, "trusted"

    # ── 内部 HTTP ─────────────────────────────────────────────────────────────

    @staticmethod
    async def _raw_call(
        rpc_url: str, method: str, params: list[Any], *, timeout: float = 15.0
    ) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(rpc_url, json=payload)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPError as e:
            raise HeliosRpcError(f"HTTP error: {e}") from e
        except (json.JSONDecodeError, ValueError) as e:
            raise HeliosRpcError(f"JSON decode error: {e}") from e
        if "error" in data:
            err = data["error"]
            raise HeliosRpcError(
                f"JSON-RPC error: {err.get('code')} {err.get('message', 'unknown')}"
            )
        return data.get("result")

    # ── 状态 ─────────────────────────────────────────────────────────────────

    def status(self, chain: str | None = None) -> ChainStatus | dict[str, ChainStatus]:
        """单链 / 全链 status."""
        if chain is not None:
            return self._status[chain]
        return dict(self._status)

    @property
    def in_sync(self) -> dict[str, bool]:
        return {c: s.in_sync for c, s in self._status.items()}

    @property
    def head(self) -> dict[str, int]:
        return {c: s.head for c, s in self._status.items()}

    # ── sync HTTP 同步版 (给非 async 调用方) ──────────────────────────────

    def call_sync(
        self,
        chain: str,
        method: str,
        params: list[Any] | None = None,
        *,
        timeout: float = 15.0,
    ) -> Any:
        """同步版 call (给 eas.py 这种纯 sync 代码用)."""
        s = self._status.get(chain)
        if s is None:
            raise HeliosError(f"chain '{chain}' 未注册")
        if not s.rpc_url:
            raise HeliosError(f"chain '{chain}' 无 rpc_url")
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        try:
            r = httpx.post(s.rpc_url, json=payload, timeout=timeout)
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError as e:
            raise HeliosRpcError(f"HTTP error: {e}") from e
        except (json.JSONDecodeError, ValueError) as e:
            raise HeliosRpcError(f"JSON decode error: {e}") from e
        if "error" in data:
            err = data["error"]
            raise HeliosRpcError(
                f"JSON-RPC error: {err.get('code')} {err.get('message', 'unknown')}"
            )
        return data.get("result")


# ─────────────────────────────────────────────────────────────────────────────
# 全局 singleton (给 eas.py / arweave.py 共享一个 helios 实例)
# ─────────────────────────────────────────────────────────────────────────────


_global_client: HeliosClient | None = None


def get_default_client() -> HeliosClient | None:
    """读 SISOUL_HELIOS_DISABLE env 是否禁用. 默认开."""
    if os.environ.get("SISOUL_HELIOS_DISABLE") == "1":
        return None
    return _global_client


def set_default_client(client: HeliosClient | None) -> None:
    global _global_client
    _global_client = client


__all__ = [
    "HeliosClient",
    "HeliosError",
    "HeliosBinaryMissing",
    "HeliosSyncTimeout",
    "HeliosChainNotSupported",
    "HeliosRpcError",
    "ChainStatus",
    "find_helios_binary",
    "HELIOS_NATIVE_CHAINS",
    "DEFAULT_EXECUTION_RPCS",
    "DEFAULT_CONSENSUS_RPC",
    "DEFAULT_SYNC_TIMEOUT_SEC",
    "DEFAULT_BASE_PORT",
    "get_default_client",
    "set_default_client",
]
