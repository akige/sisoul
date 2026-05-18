"""sisoul p2p · SisoulP2PNode 整合 (Phase 3 W31-W36 · 波 4 dev-A).

§28 §1.1 模块 9.

封装 transport + discovery + encryption + sync 协议, 给 CLI / daemon 暴露简单 API:

- ``start_node(vault_dir, port)``        启动 P2P node (binds, 启 discoverer)
- ``stop_node()``                        停止
- ``sync_with_peer(peer_id)``            主动跟某 peer sync vault
- ``list_peers()``                       已知 peer (Manual + mDNS + DHT 汇总)
- ``add_peer(multiaddr)``                手动加 peer
- ``status()``                           当前状态 + 同步统计

⚠️ 全局单例: ``get_node() / set_node()`` 维护一个 module-level 实例 (daemon / CLI 共用).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sisoul.identity import (
    InvalidMnemonicError,
    load_mnemonic_from_file,
    mnemonic_to_master_key,
)
from sisoul.p2p.discovery import (
    Discoverer,
    ManualDiscoverer,
    build_default_discoverer,
)
from sisoul.p2p.encryption import (
    DecryptionError,
    derive_p2p_key,
    decrypt as p2p_decrypt,
    encrypt as p2p_encrypt,
)
from sisoul.p2p.sync import (
    FileMeta,
    Inventory,
    apply_pull,
    build_inventory,
    compute_diff,
    decode_message,
    encode_message,
    record_conflict,
)
from sisoul.p2p.transport import (
    AIORTC_AVAILABLE,
    LIBP2P_AVAILABLE,
    Message,
    PeerInfo,
    Transport,
    select_transport,
)

log = logging.getLogger(__name__)


# ── 状态 ──────────────────────────────────────────────────────────────────────


@dataclass
class SyncStats:
    syncs_total: int = 0
    syncs_ok: int = 0
    syncs_failed: int = 0
    last_sync_ts: Optional[float] = None
    last_sync_peer: Optional[str] = None
    last_sync_pulled: int = 0
    last_sync_pushed: int = 0
    last_sync_conflicts: int = 0


@dataclass
class NodeStatus:
    running: bool
    transport: str
    peer_id: str
    multiaddr: str
    port: int
    libp2p_available: bool
    aiortc_available: bool
    peers: list[PeerInfo] = field(default_factory=list)
    stats: SyncStats = field(default_factory=SyncStats)


# ── SisoulP2PNode ─────────────────────────────────────────────────────────────


class SisoulP2PNode:
    """单设备 P2P node 实例.

    生命周期:
        node = SisoulP2PNode(vault_dir, seed_path)
        await node.start(port=9876)
        await node.sync_with(peer_id)
        await node.stop()
    """

    def __init__(
        self,
        vault_dir: Path,
        seed_path: Optional[Path] = None,
        transport_prefer: Optional[str] = None,
    ) -> None:
        self.vault_dir = Path(vault_dir).expanduser()
        self.seed_path = Path(seed_path).expanduser() if seed_path else self.vault_dir / "seed.txt"
        self._transport_prefer = transport_prefer  # None = 自动 (libp2p → webrtc → inmem)

        self._transport: Optional[Transport] = None
        self._discoverer: Optional[Discoverer] = None
        self._p2p_key: Optional[bytes] = None
        self._running = False
        self._port = 0
        self._recv_task: Optional[asyncio.Task] = None
        self.stats = SyncStats()
        # 进行中的 sync session 用 (peer_id → 待回收 inventory etc)
        self._pending_inventory: dict[str, Inventory] = {}
        self._pending_file_data: dict[tuple[str, str], bytes] = {}  # (peer_id, rel) → bytes
        self._sync_waiters: dict[str, asyncio.Future] = {}  # peer_id → Future for inventory
        self._waiting_for_files: set[tuple[str, str]] = set()  # (peer_id, rel) 正在等的 file

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def start(self, port: int = 0) -> str:
        """启动 transport + discoverer + 派 p2p key.

        Args:
            port: bind port (0 = OS 分配 / inmem 忽略).

        Returns:
            本 node multiaddr.
        """
        if self._running:
            raise RuntimeError("node 已 running, 先 stop")

        # 派 p2p key (需要 seed)
        self._p2p_key = self._load_p2p_key()

        # 选 transport
        self._transport = select_transport(
            node_label=self.vault_dir.name or "sisoul",
            prefer=self._transport_prefer,
        )
        multiaddr = await self._transport.start(port=port)
        self._port = port

        # discoverer (manual + 可选 mDNS)
        # mDNS 测试 / inmem transport 时关掉 (loopback 用不上 mDNS, 且 zeroconf 跨 loop event 不稳)
        manual_store = self.vault_dir / "p2p" / "peers.json"
        enable_mdns = self._transport.name != "inmem"
        self._discoverer = build_default_discoverer(
            my_peer_id=self._transport.peer_id,
            my_port=port or 0,
            manual_store_path=manual_store,
            enable_mdns=enable_mdns,
        )
        await self._discoverer.start()

        # 启动 recv loop
        self._recv_task = asyncio.create_task(self._recv_loop())
        self._running = True
        log.info(
            "sisoul P2P node started: transport=%s peer_id=%s multiaddr=%s",
            self._transport.name,
            self._transport.peer_id,
            multiaddr,
        )
        return multiaddr

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._recv_task = None
        if self._discoverer is not None:
            await self._discoverer.stop()
            self._discoverer = None
        if self._transport is not None:
            await self._transport.stop()
            self._transport = None
        self._p2p_key = None

    # ── 派 key 帮助 ────────────────────────────────────────────────────────

    def _load_p2p_key(self) -> bytes:
        """从 seed.txt 派 p2p key. seed 不在 → 报错 (P2P 必须有身份)."""
        if not self.seed_path.exists():
            raise FileNotFoundError(
                f"seed 不存在: {self.seed_path}. P2P 必须先 ``sisoul init`` 或 ``sisoul restore <seed>`` 建身份"
            )
        try:
            mnemonic = load_mnemonic_from_file(self.seed_path)
        except InvalidMnemonicError as e:
            raise RuntimeError(f"seed 文件非法: {e}") from e
        master = mnemonic_to_master_key(mnemonic)
        return derive_p2p_key(master)

    # ── peer 管理 ─────────────────────────────────────────────────────────

    def list_peers(self) -> list[PeerInfo]:
        if self._discoverer is None:
            return []
        return self._discoverer.list_peers()

    def add_peer(self, multiaddr: str, peer_id: Optional[str] = None, transport: str = "manual") -> PeerInfo:
        """手动加 peer.

        Args:
            multiaddr: peer multiaddr 字符串 (例 ``inmem://abc123`` / ``webrtc://...``).
            peer_id: 显式 peer_id; None 则从 multiaddr 末段 parse.
            transport: 标记 (manual / webrtc / libp2p / inmem).

        Returns:
            PeerInfo.
        """
        if self._discoverer is None:
            raise RuntimeError("node 未 start, 先 start 再 add_peer")
        if peer_id is None:
            # 解 multiaddr 末段当 peer_id (例 "inmem://abc123" → "abc123")
            peer_id = multiaddr.rsplit("/", 1)[-1].split("://")[-1]
            if not peer_id:
                raise ValueError(f"无法从 multiaddr 解 peer_id: {multiaddr}")
        peer = PeerInfo(peer_id=peer_id, multiaddr=multiaddr, transport=transport)
        self._discoverer.add_peer(peer)
        return peer

    def remove_peer(self, peer_id: str) -> bool:
        """移除手动 peer (仅 ManualDiscoverer 子列表)."""
        if self._discoverer is None:
            return False
        # 遍历找 manual
        from sisoul.p2p.discovery import CompositeDiscoverer
        if isinstance(self._discoverer, CompositeDiscoverer):
            for d in self._discoverer._discoverers:  # noqa: SLF001
                if isinstance(d, ManualDiscoverer):
                    return d.remove_peer(peer_id)
        elif isinstance(self._discoverer, ManualDiscoverer):
            return self._discoverer.remove_peer(peer_id)
        return False

    # ── status ────────────────────────────────────────────────────────────

    def status(self) -> NodeStatus:
        if not self._running or self._transport is None:
            return NodeStatus(
                running=False,
                transport="none",
                peer_id="",
                multiaddr="",
                port=0,
                libp2p_available=LIBP2P_AVAILABLE,
                aiortc_available=AIORTC_AVAILABLE,
                peers=[],
                stats=self.stats,
            )
        return NodeStatus(
            running=True,
            transport=self._transport.name,
            peer_id=self._transport.peer_id,
            multiaddr=self._transport.multiaddr,
            port=self._port,
            libp2p_available=LIBP2P_AVAILABLE,
            aiortc_available=AIORTC_AVAILABLE,
            peers=self.list_peers(),
            stats=self.stats,
        )

    # ── recv loop ────────────────────────────────────────────────────────

    async def _recv_loop(self) -> None:
        """后台收消息 + dispatch."""
        assert self._transport is not None
        while self._running:
            try:
                msg = await self._transport.recv(timeout=0.5)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                log.debug("recv 出错 (continuing): %s", e)
                await asyncio.sleep(0.1)
                continue
            if msg is None:
                continue
            try:
                await self._handle_message(msg)
            except Exception as e:  # noqa: BLE001
                log.warning("handle_message 失败 from=%s: %s", msg.from_peer, e)

    async def _handle_message(self, msg: Message) -> None:
        """解码 + 分发."""
        assert self._p2p_key is not None
        try:
            decrypted = p2p_decrypt(self._p2p_key, msg.payload)
        except DecryptionError as e:
            log.warning("from=%s 解密失败 (key 不匹配?): %s", msg.from_peer, e)
            return
        msg_type, payload = decode_message(decrypted)
        log.debug("收 from=%s type=%s", msg.from_peer, msg_type)

        if msg_type == "INVENTORY_REQUEST":
            await self._handle_inventory_request(msg.from_peer)
        elif msg_type == "INVENTORY_RESPONSE":
            self._handle_inventory_response(msg.from_peer, payload)
        elif msg_type == "FILE_REQUEST":
            await self._handle_file_request(msg.from_peer, payload)
        elif msg_type == "FILE_CHUNK":
            self._handle_file_chunk(msg.from_peer, payload)
        else:
            log.debug("未知 type %s, 忽略", msg_type)

    async def _send(self, to_peer: str, msg_type: str, payload: dict) -> None:
        assert self._transport is not None and self._p2p_key is not None
        raw = encode_message(msg_type, payload)
        encrypted = p2p_encrypt(self._p2p_key, raw)
        await self._transport.send(to_peer, encrypted)

    # ── handler 实现 ──────────────────────────────────────────────────────

    async def _handle_inventory_request(self, from_peer: str) -> None:
        inv = build_inventory(self.vault_dir)
        await self._send(from_peer, "INVENTORY_RESPONSE", inv.to_dict())

    def _handle_inventory_response(self, from_peer: str, payload: dict) -> None:
        inv = Inventory.from_dict(payload)
        self._pending_inventory[from_peer] = inv
        fut = self._sync_waiters.pop(from_peer, None)
        if fut is not None and not fut.done():
            fut.set_result(inv)

    async def _handle_file_request(self, from_peer: str, payload: dict) -> None:
        rel = payload.get("rel_path", "")
        target = self.vault_dir / rel
        if not target.exists():
            await self._send(
                from_peer,
                "FILE_CHUNK",
                {"rel_path": rel, "content_b64": "", "missing": True},
            )
            return
        import base64
        data = target.read_bytes()
        st = target.stat()
        await self._send(
            from_peer,
            "FILE_CHUNK",
            {
                "rel_path": rel,
                "content_b64": base64.b64encode(data).decode("ascii"),
                "mtime_ns": st.st_mtime_ns,
                "size": st.st_size,
            },
        )

    def _handle_file_chunk(self, from_peer: str, payload: dict) -> None:
        """收到 FILE_CHUNK 两种来源:
        1. 本端发了 FILE_REQUEST → 对方回 (主动拉, 走 _pending_file_data buffer)
        2. 对方主动 push → 本端没问, 应直接落盘 (newer-wins 由对方算好才推)

        用 ``_waiting_for_files`` set 区分: sync_with 拉时先标记 rel 在 waiting set;
        收到后取走并 pop. 不在 set 中的是 push 来的, 直接落盘.
        """
        import base64
        rel = payload.get("rel_path", "")
        b64 = payload.get("content_b64", "")
        data = base64.b64decode(b64) if b64 else b""
        mtime_ns = payload.get("mtime_ns")

        # 本端正在等这个 rel (sync_with pull/conflict 路径) → 进 buffer
        if (from_peer, rel) in self._waiting_for_files:
            self._pending_file_data[(from_peer, rel)] = data
            meta_key = (from_peer, rel, "mtime")
            if mtime_ns is not None:
                self._pending_file_data[meta_key] = str(mtime_ns).encode("ascii")  # type: ignore[assignment]
            return

        # 对方主动 push → 直接落盘 (但要查 mtime, 不能盲覆盖本地更新的)
        if not rel or payload.get("missing"):
            return
        target = self.vault_dir / rel
        if target.exists():
            try:
                local_mtime = target.stat().st_mtime_ns
                if mtime_ns is not None and local_mtime > mtime_ns:
                    log.debug("push %s 跳过 (local mtime %s > remote %s)", rel, local_mtime, mtime_ns)
                    return
            except OSError:
                pass
        try:
            apply_pull(self.vault_dir, rel, data, mtime_ns=mtime_ns)
            log.debug("push 落盘: %s", rel)
        except Exception as e:  # noqa: BLE001
            log.warning("push 落盘失败 %s: %s", rel, e)

    # ── sync_with 主流程 ──────────────────────────────────────────────────

    async def sync_with(self, peer_id: str, timeout: float = 5.0) -> dict:
        """主动跟 peer sync vault.

        Returns:
            dict {pulled: N, pushed: N, conflicts: N, ok: bool, error: str | None}
        """
        if not self._running or self._transport is None:
            raise RuntimeError("node 未 start")

        self.stats.syncs_total += 1
        result: dict = {"pulled": 0, "pushed": 0, "conflicts": 0, "ok": False, "error": None}
        try:
            # 1. 请 inventory
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._sync_waiters[peer_id] = fut
            await self._send(peer_id, "INVENTORY_REQUEST", {})
            try:
                remote_inv: Inventory = await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                raise TimeoutError(f"等 INVENTORY_RESPONSE 超时 ({timeout}s)")

            # 2. 算 diff
            local_inv = build_inventory(self.vault_dir)
            diff = compute_diff(local_inv, remote_inv)

            # 3. pull: 请远端文件落盘
            for rel in diff.pull:
                self._waiting_for_files.add((peer_id, rel))
                await self._send(peer_id, "FILE_REQUEST", {"rel_path": rel})
                # 等 file_chunk 回 (poll _pending_file_data, 简单 timeout-based)
                end_t = time.time() + timeout
                while (peer_id, rel) not in self._pending_file_data:
                    if time.time() > end_t:
                        log.warning("等 file %s 超时", rel)
                        break
                    await asyncio.sleep(0.05)
                self._waiting_for_files.discard((peer_id, rel))
                data = self._pending_file_data.pop((peer_id, rel), None)
                meta_key = (peer_id, rel, "mtime")
                mtime_raw = self._pending_file_data.pop(meta_key, None)  # type: ignore[arg-type]
                mtime_ns: Optional[int] = None
                if mtime_raw:
                    try:
                        mtime_ns = int(mtime_raw.decode("ascii"))
                    except Exception:  # noqa: BLE001
                        mtime_ns = None
                if data is not None:
                    apply_pull(self.vault_dir, rel, data, mtime_ns=mtime_ns)
                    result["pulled"] += 1

            # 4. push: 主动发 FILE_CHUNK
            import base64
            for rel in diff.push:
                target = self.vault_dir / rel
                if not target.exists():
                    continue
                data = target.read_bytes()
                st = target.stat()
                await self._send(
                    peer_id,
                    "FILE_CHUNK",
                    {
                        "rel_path": rel,
                        "content_b64": base64.b64encode(data).decode("ascii"),
                        "mtime_ns": st.st_mtime_ns,
                        "size": st.st_size,
                    },
                )
                result["pushed"] += 1

            # 5. conflict 处理: 请远端 content 落 .conflict 副本
            for rel in diff.conflicts:
                self._waiting_for_files.add((peer_id, rel))
                await self._send(peer_id, "FILE_REQUEST", {"rel_path": rel})
                end_t = time.time() + timeout
                while (peer_id, rel) not in self._pending_file_data:
                    if time.time() > end_t:
                        break
                    await asyncio.sleep(0.05)
                self._waiting_for_files.discard((peer_id, rel))
                data = self._pending_file_data.pop((peer_id, rel), None)
                if data is not None and rel in local_inv.files and rel in remote_inv.files:
                    record_conflict(
                        self.vault_dir,
                        rel,
                        local=local_inv.files[rel],
                        remote=remote_inv.files[rel],
                        peer_id=peer_id,
                        remote_content=data,
                    )
                    result["conflicts"] += 1

            self.stats.syncs_ok += 1
            self.stats.last_sync_ts = time.time()
            self.stats.last_sync_peer = peer_id
            self.stats.last_sync_pulled = result["pulled"]
            self.stats.last_sync_pushed = result["pushed"]
            self.stats.last_sync_conflicts = result["conflicts"]
            result["ok"] = True
            return result
        except Exception as e:  # noqa: BLE001
            self.stats.syncs_failed += 1
            result["error"] = f"{type(e).__name__}: {e}"
            log.warning("sync_with %s 失败: %s", peer_id, e)
            return result
        finally:
            self._sync_waiters.pop(peer_id, None)


# ── 全局单例 (daemon + CLI 共用) ───────────────────────────────────────────────


_NODE: Optional[SisoulP2PNode] = None


def get_node() -> Optional[SisoulP2PNode]:
    return _NODE


def set_node(node: Optional[SisoulP2PNode]) -> None:
    global _NODE
    _NODE = node


# ── 顶层便利函数 ──────────────────────────────────────────────────────────────


async def start_node(
    vault_dir: Path,
    port: int = 0,
    seed_path: Optional[Path] = None,
    transport_prefer: Optional[str] = None,
) -> SisoulP2PNode:
    """启动并设全局 node. 若已 running, 抛错."""
    existing = get_node()
    if existing is not None and existing._running:  # noqa: SLF001
        raise RuntimeError("P2P node 已 running, 先 stop_node")
    node = SisoulP2PNode(
        vault_dir=vault_dir, seed_path=seed_path, transport_prefer=transport_prefer
    )
    await node.start(port=port)
    set_node(node)
    return node


async def stop_node() -> None:
    """停全局 node."""
    node = get_node()
    if node is None:
        return
    await node.stop()
    set_node(None)


async def sync_with_peer(peer_id: str, timeout: float = 5.0) -> dict:
    node = get_node()
    if node is None:
        raise RuntimeError("P2P node 未 start, 先 ``sisoul p2p start``")
    return await node.sync_with(peer_id, timeout=timeout)


def list_peers() -> list[PeerInfo]:
    node = get_node()
    if node is None:
        return []
    return node.list_peers()


__all__ = [
    "NodeStatus",
    "SisoulP2PNode",
    "SyncStats",
    "get_node",
    "list_peers",
    "set_node",
    "start_node",
    "stop_node",
    "sync_with_peer",
]
