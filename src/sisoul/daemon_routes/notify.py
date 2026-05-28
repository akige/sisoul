"""sisoul daemon · notify routes (Wave B' P1-1 · agent-B3).

Endpoints:
    - POST /sisoul/peer/heartbeat        朋友 daemon 互发心跳 (内部 P2P)
    - GET  /sisoul/peer/status?did=...   查朋友 online/offline
    - GET  /sisoul/peer/status           列所有已知 peer 状态
    - GET  /sisoul/notify/recent         REST 拉最近 N 条 notification
    - POST /sisoul/notify/mark-read      标已读 (body: notify_id)
    - WS   /sisoul/notify/stream         PWA 订阅实时推送

主集成强制命名: ``notify_router`` (跟 p2p_router/friend_router 一致).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from sisoul.p2p.push import (
    Notification,
    NotificationStore,
    PeerStatus,
    get_peer_status,
    get_push_service,
    list_recent_notifications,
    record_external_heartbeat,
)

log = logging.getLogger(__name__)

notify_router = APIRouter(tags=["notify"])


# ── schemas ─────────────────────────────────────────────────────────────────


class HeartbeatRequest(BaseModel):
    did: str = Field(..., description="发心跳的朋友 DID")
    ts: Optional[float] = Field(None, description="心跳 ts (默认 server now)")


class HeartbeatResponse(BaseModel):
    ok: bool
    recorded_did: str


class PeerStatusResponse(BaseModel):
    did: str
    state: str
    last_heartbeat_ts: Optional[float]
    last_seen_age_sec: Optional[float]


class PeerStatusListResponse(BaseModel):
    peers: list[PeerStatusResponse]
    count: int


class NotificationResponse(BaseModel):
    notify_id: str
    kind: str
    source_did: str
    target_did: str
    payload: dict[str, Any]
    ts: float
    read: bool
    delivered_via: list[str]


class RecentNotifyResponse(BaseModel):
    notifications: list[NotificationResponse]
    count: int


class MarkReadRequest(BaseModel):
    notify_id: str


class MarkReadResponse(BaseModel):
    ok: bool
    notify_id: str


# ── REST endpoints ─────────────────────────────────────────────────────────


@notify_router.post("/sisoul/peer/heartbeat", response_model=HeartbeatResponse)
def post_heartbeat(req: HeartbeatRequest) -> HeartbeatResponse:
    """朋友 daemon 互发心跳 (内部 P2P).

    朋友 daemon 也可不走 HTTP 直接走 Waku ``HEARTBEAT_TOPIC`` — 本 HTTP 端点是
    LAN / Tailscale 直连 daemon 的 fallback (Waku 不可达时).
    """
    if not req.did:
        raise HTTPException(status_code=400, detail="did required")
    record_external_heartbeat(req.did, req.ts)
    return HeartbeatResponse(ok=True, recorded_did=req.did)


@notify_router.get("/sisoul/peer/status", response_model=PeerStatusListResponse)
def get_status(
    did: Optional[str] = Query(None, description="单个 did; 不传列全部"),
) -> PeerStatusListResponse:
    """查朋友 online/offline. 不传 did 列所有已知 peer."""
    svc = get_push_service()
    if did:
        st = get_peer_status(did)
        return PeerStatusListResponse(
            peers=[PeerStatusResponse(**st.to_dict())], count=1
        )
    if svc is None:
        return PeerStatusListResponse(peers=[], count=0)
    all_peers = svc.tracker.list_all()
    return PeerStatusListResponse(
        peers=[PeerStatusResponse(**p.to_dict()) for p in all_peers],
        count=len(all_peers),
    )


@notify_router.get("/sisoul/notify/recent", response_model=RecentNotifyResponse)
def get_recent(
    limit: int = Query(50, ge=1, le=500),
    target_did: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    unread_only: bool = Query(False),
) -> RecentNotifyResponse:
    """拉最近 N 条 notification (PWA 用 / CLI ``sisoul notify`` 用)."""
    notifs = list_recent_notifications(
        limit=limit,
        target_did=target_did,
        kind=kind,  # type: ignore[arg-type]
        unread_only=unread_only,
    )
    return RecentNotifyResponse(
        notifications=[NotificationResponse(**n.to_dict()) for n in notifs],
        count=len(notifs),
    )


@notify_router.post("/sisoul/notify/mark-read", response_model=MarkReadResponse)
def post_mark_read(req: MarkReadRequest) -> MarkReadResponse:
    svc = get_push_service()
    store = svc.store if svc else NotificationStore()
    ok = store.mark_read(req.notify_id)
    return MarkReadResponse(ok=ok, notify_id=req.notify_id)


# ── WebSocket: /sisoul/notify/stream ───────────────────────────────────────


@notify_router.websocket("/sisoul/notify/stream")
async def notify_stream(ws: WebSocket) -> None:
    """PWA 订阅实时推送.

    协议:
        - client 连接 → server accept → 发 ``{"type":"hello","self_did":...}``
        - 收到 Notification → server 发 ``{"type":"notify","data":<Notification dict>}``
        - client → server ``{"type":"ping"}`` → server ``{"type":"pong"}`` (keep-alive)
        - 断开 → unregister listener
    """
    await ws.accept()
    svc = get_push_service()
    if svc is None:
        await ws.send_json(
            {"type": "error", "error": "push service 未初始化 (daemon 未起 P2P)"}
        )
        await ws.close()
        return

    await ws.send_json({"type": "hello", "self_did": svc.self_did})

    # listener: 把 Notification 推 WS
    async def _push_to_ws(n: Notification) -> None:
        try:
            await ws.send_json({"type": "notify", "data": n.to_dict()})
        except Exception as e:  # noqa: BLE001
            log.debug("ws send failed (likely disconnected): %s", e)

    unregister = await svc.register_listener(_push_to_ws)

    try:
        while True:
            # 等 client ping / 断开
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
            elif msg.get("type") == "subscribe_recent":
                # client 进入页面时拉历史
                limit = int(msg.get("limit", 20))
                notifs = list_recent_notifications(
                    limit=limit, target_did=svc.self_did
                )
                await ws.send_json(
                    {
                        "type": "recent",
                        "data": [n.to_dict() for n in notifs],
                    }
                )
    except WebSocketDisconnect:
        pass
    finally:
        await unregister()


__all__ = ["notify_router"]
