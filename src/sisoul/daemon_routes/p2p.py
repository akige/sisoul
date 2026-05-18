"""sisoul daemon · P2P HTTP API (Phase 3 W31-W36 · 波 4 dev-A).

§28 §1.1 模块 9 / §28 §2.1.

Endpoints:
- GET  /sisoul/p2p/status        node 状态 + peer 列表
- POST /sisoul/p2p/start         启动 P2P node (body: vault_dir / port / transport)
- POST /sisoul/p2p/stop          停 P2P node
- POST /sisoul/p2p/sync          强制 sync (body: optional peer_id, timeout)
- POST /sisoul/p2p/add-peer      手动加 peer (body: multiaddr)
- GET  /sisoul/p2p/peers         已知 peer 列表

⚠️ **router 命名强制规范**: 模块级变量 ``p2p_router``, 主集成 ``daemon.py`` 用
``from sisoul.daemon_routes.p2p import p2p_router; app.include_router(p2p_router)``.
波 3 dev-C ``router`` 命名陷阱 → 本文件锁死 ``p2p_router``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from sisoul.p2p import (
    AIORTC_AVAILABLE,
    LIBP2P_AVAILABLE,
    get_node,
    set_node,
    start_node as _start_node,
    stop_node as _stop_node,
    sync_with_peer as _sync_with_peer,
)
from sisoul.vault import DEFAULT_VAULT_DIR

# 主集成强制命名: p2p_router (不要改 'router')
p2p_router = APIRouter(prefix="/sisoul/p2p", tags=["p2p"])


# ── schemas ─────────────────────────────────────────────────────────────────


class StartRequest(BaseModel):
    vault_dir: Optional[str] = Field(None, description="vault 路径; 默认 ~/.sisoul/")
    port: int = Field(0, description="bind port (0 = OS 分配)")
    transport: Optional[str] = Field(None, description="libp2p / webrtc / inmem; 默认自动")


class StartResponse(BaseModel):
    ok: bool
    transport: str
    peer_id: str
    multiaddr: str
    libp2p_available: bool
    aiortc_available: bool


class StopResponse(BaseModel):
    ok: bool
    message: str


class SyncRequest(BaseModel):
    peer_id: Optional[str] = Field(None, description="目标 peer_id; 空 = 所有已知 peer")
    timeout: float = Field(5.0, description="单 peer 超时秒")


class SyncResult(BaseModel):
    peer_id: str
    ok: bool
    pulled: int
    pushed: int
    conflicts: int
    error: Optional[str] = None


class SyncResponse(BaseModel):
    results: list[SyncResult]


class AddPeerRequest(BaseModel):
    multiaddr: str = Field(..., description="例 inmem://abc / webrtc://xxx:9876")
    peer_id: Optional[str] = Field(None, description="显式 peer_id (默认从 multiaddr 解)")
    transport: str = Field("manual", description="标记 transport 类型")


class PeerItem(BaseModel):
    peer_id: str
    multiaddr: str
    transport: str
    last_seen_ts: float


class PeersResponse(BaseModel):
    peers: list[PeerItem]


class StatsResponse(BaseModel):
    syncs_total: int
    syncs_ok: int
    syncs_failed: int
    last_sync_ts: Optional[float] = None
    last_sync_peer: Optional[str] = None
    last_sync_pulled: int = 0
    last_sync_pushed: int = 0
    last_sync_conflicts: int = 0


class StatusResponse(BaseModel):
    running: bool
    transport: str
    peer_id: str
    multiaddr: str
    port: int
    libp2p_available: bool
    aiortc_available: bool
    peers: list[PeerItem]
    stats: StatsResponse


# ── routes ────────────────────────────────────────────────────────────────────


@p2p_router.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    node = get_node()
    if node is None:
        return StatusResponse(
            running=False,
            transport="none",
            peer_id="",
            multiaddr="",
            port=0,
            libp2p_available=LIBP2P_AVAILABLE,
            aiortc_available=AIORTC_AVAILABLE,
            peers=[],
            stats=StatsResponse(syncs_total=0, syncs_ok=0, syncs_failed=0),
        )
    st = node.status()
    return StatusResponse(
        running=st.running,
        transport=st.transport,
        peer_id=st.peer_id,
        multiaddr=st.multiaddr,
        port=st.port,
        libp2p_available=st.libp2p_available,
        aiortc_available=st.aiortc_available,
        peers=[
            PeerItem(peer_id=p.peer_id, multiaddr=p.multiaddr, transport=p.transport,
                     last_seen_ts=p.last_seen_ts)
            for p in st.peers
        ],
        stats=StatsResponse(
            syncs_total=st.stats.syncs_total,
            syncs_ok=st.stats.syncs_ok,
            syncs_failed=st.stats.syncs_failed,
            last_sync_ts=st.stats.last_sync_ts,
            last_sync_peer=st.stats.last_sync_peer,
            last_sync_pulled=st.stats.last_sync_pulled,
            last_sync_pushed=st.stats.last_sync_pushed,
            last_sync_conflicts=st.stats.last_sync_conflicts,
        ),
    )


@p2p_router.post("/start", response_model=StartResponse, status_code=201)
async def post_start(body: StartRequest) -> StartResponse:
    if get_node() is not None and get_node()._running:  # noqa: SLF001
        raise HTTPException(status_code=409, detail="P2P node 已 running, 先 POST /stop")
    vault_path = Path(body.vault_dir).expanduser() if body.vault_dir else DEFAULT_VAULT_DIR
    if not vault_path.exists():
        raise HTTPException(status_code=404, detail=f"vault 不存在: {vault_path}")
    try:
        node = await _start_node(
            vault_dir=vault_path, port=body.port, transport_prefer=body.transport
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"P2P start 失败: {e}")
    st = node.status()
    return StartResponse(
        ok=True,
        transport=st.transport,
        peer_id=st.peer_id,
        multiaddr=st.multiaddr,
        libp2p_available=st.libp2p_available,
        aiortc_available=st.aiortc_available,
    )


@p2p_router.post("/stop", response_model=StopResponse)
async def post_stop() -> StopResponse:
    node = get_node()
    if node is None:
        return StopResponse(ok=True, message="P2P node 未 running, no-op")
    await _stop_node()
    return StopResponse(ok=True, message="P2P node stopped")


@p2p_router.post("/sync", response_model=SyncResponse)
async def post_sync(body: SyncRequest) -> SyncResponse:
    node = get_node()
    if node is None:
        raise HTTPException(status_code=409, detail="P2P node 未 running, 先 POST /start")
    if body.peer_id:
        targets = [body.peer_id]
    else:
        targets = [p.peer_id for p in node.list_peers()]
    if not targets:
        return SyncResponse(results=[])
    results: list[SyncResult] = []
    for pid in targets:
        try:
            res = await _sync_with_peer(pid, timeout=body.timeout)
        except RuntimeError as e:
            results.append(SyncResult(
                peer_id=pid, ok=False, pulled=0, pushed=0, conflicts=0, error=str(e)
            ))
            continue
        results.append(SyncResult(
            peer_id=pid,
            ok=res["ok"],
            pulled=res["pulled"],
            pushed=res["pushed"],
            conflicts=res["conflicts"],
            error=res.get("error"),
        ))
    return SyncResponse(results=results)


@p2p_router.post("/add-peer", response_model=PeerItem, status_code=201)
async def post_add_peer(body: AddPeerRequest) -> PeerItem:
    node = get_node()
    if node is None:
        raise HTTPException(status_code=409, detail="P2P node 未 running, 先 POST /start")
    try:
        peer = node.add_peer(body.multiaddr, peer_id=body.peer_id, transport=body.transport)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PeerItem(
        peer_id=peer.peer_id,
        multiaddr=peer.multiaddr,
        transport=peer.transport,
        last_seen_ts=peer.last_seen_ts,
    )


@p2p_router.get("/peers", response_model=PeersResponse)
async def get_peers() -> PeersResponse:
    node = get_node()
    if node is None:
        return PeersResponse(peers=[])
    return PeersResponse(peers=[
        PeerItem(peer_id=p.peer_id, multiaddr=p.multiaddr, transport=p.transport,
                 last_seen_ts=p.last_seen_ts)
        for p in node.list_peers()
    ])


__all__ = ["p2p_router"]
