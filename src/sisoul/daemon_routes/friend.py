"""sisoul daemon · 朋友关系层 HTTP API (Phase 4 W51-W53 · 波 5 dev-A).

⚠️ **Router 命名强制规范**:
本文件 ship ``friend_relationship_router`` (单独 export, prefix=/sisoul/friend).
dev-D 波 5 后会 ship ``friend_router`` 统一 export (合并 dev-A/B/C/D 4 dev 的 routes).
主集成 daemon.py 由父会话整合时, 优先 include ``friend_router`` (dev-D 整合版),
回退 include ``friend_relationship_router`` (单 dev-A 路径, dev-D 未 ship 时兜底).

§28 §2.1 + 波 5 task spec endpoints (本文件 ship dev-A 段):
- POST /sisoul/friend/request          发 FRIEND_REQUEST
- POST /sisoul/friend/accept           accept inbound request → 双向 attestation
- POST /sisoul/friend/receive          (内部) 对方 daemon 推 inbound request, 落本端 cache
- POST /sisoul/friend/confirm-mutual   (内部) 对方 FRIEND_ACCEPT 收到后标 mutual
- GET  /sisoul/friend/list             列本地 friends
- POST /sisoul/friend/revoke           revoke FRIEND
- GET  /sisoul/friend/info/{did}       查 friend + score + ledger 摘要
- GET  /sisoul/friend/requests         列 friend_requests (inbound/outbound)
- POST /sisoul/friend/score/manual     手动覆盖某 friend 强连接评分
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from sisoul.friend.relationship import (
    Friend,
    FriendError,
    FriendNotFoundError,
    FriendRelationship,
    FriendRequest,
    FriendRequestError,
    FriendRequestNotFoundError,
    compute_strong_tie_score,
    resolve_own_did,
)

# 主集成强制命名: friend_relationship_router (单 dev-A); dev-D 会 ship friend_router 合并版.
friend_relationship_router = APIRouter(
    prefix="/sisoul/friend", tags=["friend-relationship"]
)


# ── 内部 helper ──────────────────────────────────────────────────────────────


def _rel(
    own_did: Optional[str] = None,
    vault_dir: Optional[str] = None,
    friend_db: Optional[str] = None,
    attest_queue_db: Optional[str] = None,
) -> FriendRelationship:
    if own_did:
        own = own_did
    else:
        registry = Path(vault_dir) / "identity" / "dids.json" if vault_dir else None
        try:
            own = resolve_own_did(registry_path=registry)
        except FriendError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return FriendRelationship(
        own_did=own,
        db_path=Path(friend_db) if friend_db else None,
        attest_queue_db=Path(attest_queue_db) if attest_queue_db else None,
    )


# ── schemas ──────────────────────────────────────────────────────────────────


class _RequestBody(BaseModel):
    target_did: str = Field(..., description="目标 friend DID")
    message: str = Field("", description="可选个人留言")
    own_did: Optional[str] = Field(None, description="覆盖本端 DID")
    vault_dir: Optional[str] = None
    friend_db: Optional[str] = None
    attest_queue_db: Optional[str] = None


class _RequestResponse(BaseModel):
    request_id: str
    requester_did: str
    target_did: str
    direction: str
    message: str
    created_at: str
    attestation_uid: Optional[str]
    status: str


class _ReceiveBody(BaseModel):
    requester_did: str
    message: str = ""
    attestation_uid: Optional[str] = None
    own_did: Optional[str] = None
    vault_dir: Optional[str] = None
    friend_db: Optional[str] = None


class _AcceptBody(BaseModel):
    request_id: str
    own_did: Optional[str] = None
    vault_dir: Optional[str] = None
    friend_db: Optional[str] = None
    attest_queue_db: Optional[str] = None


class _ConfirmMutualBody(BaseModel):
    friend_did: str
    mutual_attestation_uid: str
    own_did: Optional[str] = None
    vault_dir: Optional[str] = None
    friend_db: Optional[str] = None


class _RevokeBody(BaseModel):
    did: str
    own_did: Optional[str] = None
    vault_dir: Optional[str] = None
    friend_db: Optional[str] = None
    attest_queue_db: Optional[str] = None


class _ManualScoreBody(BaseModel):
    did: str
    score: Optional[float] = Field(None, description="None 取消手动覆盖")
    own_did: Optional[str] = None
    vault_dir: Optional[str] = None
    friend_db: Optional[str] = None


class _FriendOut(BaseModel):
    did: str
    handle: str
    status: str
    strong_tie_score: float
    manual_score_override: Optional[float]
    is_mutual: bool
    created_at: str
    became_active_at: Optional[str]
    last_interaction: Optional[str]
    interaction_count: int
    request_attestation_uid: Optional[str]
    accept_attestation_uid: Optional[str]
    mutual_attestation_uid: Optional[str]
    revoke_attestation_uid: Optional[str]
    notes: str


def _friend_out(f: Friend) -> _FriendOut:
    # 解历史双前缀残留 (did:sisoul:did:key:… — 老 _normalize_did bug 写进库的),
    # 否则 PWA 拿 list 里的 did 去 borrow 解不出 X25519 pubkey 必失败.
    did = f.did
    while did.startswith("did:sisoul:did:"):
        did = did[len("did:sisoul:"):]
    return _FriendOut(
        did=did,
        handle=f.handle,
        status=f.status,
        strong_tie_score=f.strong_tie_score,
        manual_score_override=f.manual_score_override,
        is_mutual=f.is_mutual,
        created_at=f.created_at,
        became_active_at=f.became_active_at,
        last_interaction=f.last_interaction,
        interaction_count=f.interaction_count,
        request_attestation_uid=f.request_attestation_uid,
        accept_attestation_uid=f.accept_attestation_uid,
        mutual_attestation_uid=f.mutual_attestation_uid,
        revoke_attestation_uid=f.revoke_attestation_uid,
        notes=f.notes,
    )


def _request_out(r: FriendRequest) -> _RequestResponse:
    return _RequestResponse(
        request_id=r.request_id,
        requester_did=r.requester_did,
        target_did=r.target_did,
        direction=r.direction,
        message=r.message,
        created_at=r.created_at,
        attestation_uid=r.attestation_uid,
        status=r.status,
    )


# ── POST /sisoul/friend/request ──────────────────────────────────────────────


@friend_relationship_router.post("/request", response_model=_RequestResponse)
def post_request(body: _RequestBody) -> _RequestResponse:
    """发 FRIEND_REQUEST."""
    rel = _rel(body.own_did, body.vault_dir, body.friend_db, body.attest_queue_db)
    try:
        req = rel.send_friend_request(body.target_did, message=body.message)
    except (FriendError, FriendRequestError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _request_out(req)


# ── POST /sisoul/friend/receive (内部) ───────────────────────────────────────


@friend_relationship_router.post("/receive", response_model=_RequestResponse)
def post_receive(body: _ReceiveBody) -> _RequestResponse:
    """对方 daemon → 本 daemon 推 inbound FRIEND_REQUEST.

    本 wave: P2P 真路径未接, 集成测试同机 2 实例手工 POST 模拟.
    Phase 5: 由 sisoul.p2p recv hook 调.
    """
    rel = _rel(body.own_did, body.vault_dir, body.friend_db, None)
    try:
        req = rel.receive_friend_request(
            body.requester_did,
            message=body.message,
            attestation_uid=body.attestation_uid,
        )
    except (FriendError, FriendRequestError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _request_out(req)


# ── POST /sisoul/friend/accept ───────────────────────────────────────────────


@friend_relationship_router.post("/accept", response_model=_FriendOut)
def post_accept(body: _AcceptBody) -> _FriendOut:
    """accept inbound FRIEND_REQUEST."""
    rel = _rel(body.own_did, body.vault_dir, body.friend_db, body.attest_queue_db)
    try:
        friend = rel.accept_friend_request(body.request_id)
    except FriendRequestNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (FriendError, FriendRequestError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _friend_out(friend)


# ── POST /sisoul/friend/confirm-mutual (内部) ────────────────────────────────


@friend_relationship_router.post("/confirm-mutual", response_model=_FriendOut)
def post_confirm_mutual(body: _ConfirmMutualBody) -> _FriendOut:
    """对方 FRIEND_ACCEPT attestation 收到后, 标本端 friend.mutual_attestation_uid.

    集成测试同机 2 实例双向 accept 用; Phase 5 P2P recv hook 自动调.
    """
    rel = _rel(body.own_did, body.vault_dir, body.friend_db, None)
    try:
        friend = rel.confirm_mutual_attestation(
            body.friend_did, body.mutual_attestation_uid
        )
    except FriendNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FriendError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _friend_out(friend)


# ── GET /sisoul/friend/list ──────────────────────────────────────────────────


@friend_relationship_router.get("/list", response_model=list[_FriendOut])
def get_list(
    status: Optional[str] = Query(None, description="pending/active/revoked"),
    recompute_score: bool = Query(False),
    own_did: Optional[str] = Query(None),
    vault_dir: Optional[str] = Query(None),
    friend_db: Optional[str] = Query(None),
) -> list[_FriendOut]:
    rel = _rel(own_did, vault_dir, friend_db, None)
    if status and status not in {"pending", "active", "revoked"}:
        raise HTTPException(status_code=400, detail="status 必须 pending/active/revoked")
    items = rel.list_friends(status=status, recompute_score=recompute_score)  # type: ignore[arg-type]
    out = [_friend_out(f) for f in items]

    # PWA Friends 调本 endpoint, 同时合并 didkey_friends.json (sisoul friend add
    # 轻量路径写入), 避免 sqlite EAS-attestation table 为空时 PWA UI 空白.
    try:
        from pathlib import Path
        import json as _json
        from datetime import datetime, timezone

        vd = Path(vault_dir).expanduser() if vault_dir else Path.home() / ".sisoul"
        dk_path = vd / "identity" / "didkey_friends.json"
        if dk_path.exists():
            entries = _json.loads(dk_path.read_text(encoding="utf-8"))
            existing_dids = {f.did for f in out}
            for e in entries:
                did = e.get("did", "")
                if not did or did in existing_dids:
                    continue
                added = e.get("added_at", "")
                ts = 0
                try:
                    ts = int(datetime.fromisoformat(added.replace("Z","+00:00")).timestamp() * 1000)
                except Exception:
                    pass
                out.append(_FriendOut(
                    did=did,
                    handle=e.get("nickname") or did[:20],
                    status="active",
                    strong_tie_score=0.5,
                    manual_score_override=None,
                    is_mutual=False,
                    created_at=added or "",
                    became_active_at=added or None,
                    last_interaction=added or "",
                    interaction_count=0,
                    request_attestation_uid=None,
                    accept_attestation_uid=None,
                    mutual_attestation_uid=None,
                    revoke_attestation_uid=None,
                    notes=f"did:key 朋友 (via {e.get('method','sisoul friend add')})",
                ))
    except Exception as _e:  # noqa: BLE001
        import sys as _sys
        print(f"[friend/list] didkey inject 失败: {type(_e).__name__}: {_e}", file=_sys.stderr)

    return out


# ── GET /sisoul/friend/requests ──────────────────────────────────────────────


@friend_relationship_router.get("/requests", response_model=list[_RequestResponse])
def get_requests(
    direction: Optional[str] = Query(None, description="inbound / outbound"),
    status: Optional[str] = Query(None),
    own_did: Optional[str] = Query(None),
    vault_dir: Optional[str] = Query(None),
    friend_db: Optional[str] = Query(None),
) -> list[_RequestResponse]:
    rel = _rel(own_did, vault_dir, friend_db, None)
    if direction and direction not in {"inbound", "outbound"}:
        raise HTTPException(
            status_code=400, detail="direction 必须 inbound / outbound"
        )
    items = rel.list_requests(direction=direction, status=status)  # type: ignore[arg-type]
    return [_request_out(r) for r in items]


# ── POST /sisoul/friend/revoke ───────────────────────────────────────────────


@friend_relationship_router.post("/revoke", response_model=_FriendOut)
def post_revoke(body: _RevokeBody) -> _FriendOut:
    rel = _rel(body.own_did, body.vault_dir, body.friend_db, body.attest_queue_db)
    try:
        friend = rel.revoke_friend(body.did)
    except FriendNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FriendError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _friend_out(friend)


# ── GET /sisoul/friend/info/{did} ────────────────────────────────────────────


@friend_relationship_router.get("/info/{did:path}", response_model=dict)
def get_info(
    did: str,
    own_did: Optional[str] = Query(None),
    vault_dir: Optional[str] = Query(None),
    friend_db: Optional[str] = Query(None),
) -> dict[str, Any]:
    """查 friend + 强连接评分细分 + (dev-D ship 后) 互惠 ledger 摘要."""
    rel = _rel(own_did, vault_dir, friend_db, None)
    try:
        friend = rel.get_friend(did)
    except FriendNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FriendError as e:
        raise HTTPException(status_code=400, detail=str(e))
    score = compute_strong_tie_score(friend)

    ledger_summary: dict[str, Any] = {
        "available": False,
        "reason": "dev-D 波 5 ledger 未 ship",
    }
    try:
        from sisoul.friend.ledger import summarize_friend_ledger  # type: ignore[attr-defined]

        ledger_summary = summarize_friend_ledger(
            own_did=rel.own_did, friend_did=friend.did
        )
    except Exception:  # noqa: BLE001
        pass

    return {
        "friend": _friend_out(friend).model_dump(),
        "score_breakdown": score.to_dict(),
        "ledger_summary": ledger_summary,
    }


# ── POST /sisoul/friend/score/manual ─────────────────────────────────────────


@friend_relationship_router.post("/score/manual", response_model=_FriendOut)
def post_manual_score(body: _ManualScoreBody) -> _FriendOut:
    rel = _rel(body.own_did, body.vault_dir, body.friend_db, None)
    try:
        friend = rel.set_manual_score(body.did, body.score)
    except FriendNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FriendError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _friend_out(friend)


__all__ = ["friend_relationship_router", "friend_router"]


# ════════════════════════════════════════════════════════════════════════════
# dev-D 波 5 W66-W76 ship: 统一 ``friend_router`` (LLM quota borrow/lend +
#   互惠 ledger + borrow-proxy session) + 整合 dev-A/B/C 子 router.
# ════════════════════════════════════════════════════════════════════════════
#
# 主 daemon.py 集成时只需:
#     from sisoul.daemon_routes.friend import friend_router
#     app.include_router(friend_router)
# 一行即可整合 4 dev (A/B/C/D) 所有 friend 相关 endpoints.
#
# dev-D 自加 endpoints (prefix=/sisoul):
# - POST /sisoul/borrow                     发起 borrow
# - POST /sisoul/lend/request               daemon 内部, 对端 borrower 发到本机 lender
# - POST /sisoul/lend/approve               Bob 批准
# - POST /sisoul/lend/deny                  Bob 拒绝
# - GET  /sisoul/lend/pending               Bob 列待批准
# - GET  /sisoul/lend/all                   全 (含 history)
# - GET  /sisoul/ledger/{friend_did}        Alice 看跟某 friend ledger
# - GET  /sisoul/ledger/imbalance           列所有不平衡 warning
# - GET  /sisoul/ledger/stats               全局 stats
# - POST /sisoul/borrow-proxy/start         起 proxy session (ANTHROPIC_BASE_URL 用)
# - POST /sisoul/borrow-proxy/{sid}/stop    停 proxy session
# - GET  /sisoul/borrow-proxy/list          列所有 active sessions
# - GET  /sisoul/borrow-proxy/{sid}         查 session

import sys as _sys
from typing import Any as _Any

from fastapi import APIRouter as _APIRouter
from pydantic import BaseModel as _BaseModel, Field as _Field

from sisoul.friend.borrow import (
    BorrowSession as _BorrowSession,
    borrow_resource as _borrow_resource,
    get_proxy_session as _get_proxy_session,
    list_proxy_sessions as _list_proxy_sessions,
    start_proxy_session as _start_proxy_session,
    stop_proxy_session as _stop_proxy_session,
)
from sisoul.friend.ledger import (
    DEFAULT_IMBALANCE_THRESHOLD as _DEFAULT_IMBALANCE_THRESHOLD,
    ReciprocityLedger as _ReciprocityLedger,
)
from sisoul.friend.lend import (
    LendStore as _LendStore,
    RequestNotFoundError as _RequestNotFoundError,
    RequestStateError as _RequestStateError,
    approve_lend as __approve_lend,
    deny_lend as __deny_lend,
    list_pending_requests as __list_pending,
    request_lend as __request_lend,
)


# 统一 router (无 prefix, 由子 router + dev-D path 各自带).
friend_router = _APIRouter(tags=["friend"])

# 1) include dev-A friend_relationship_router (本文件内已 ship)
friend_router.include_router(friend_relationship_router)


def _dev_d_try_include(router_attr: str, module_path: str, label: str) -> None:
    """try-include dev-B / dev-C 子 router. 未 ship 不阻塞 dev-D 自己 endpoints."""
    try:
        mod = __import__(module_path, fromlist=[router_attr])
        sub = getattr(mod, router_attr, None)
        if sub is None:
            print(
                f"[friend_router] {label}: {module_path}.{router_attr} 不存在, skip",
                file=_sys.stderr,
            )
            return
        friend_router.include_router(sub)
        print(
            f"[friend_router] {label}: included {module_path}.{router_attr}",
            file=_sys.stderr,
        )
    except Exception as _e:  # noqa: BLE001
        print(
            f"[friend_router] {label} 未 ship 或 import 失败 "
            f"({type(_e).__name__}: {_e}), degrade 跳过",
            file=_sys.stderr,
        )


# 2) dev-B 加密 proxy
_dev_d_try_include("proxy_router", "sisoul.daemon_routes.proxy", "dev-B proxy_router")
# 3) dev-C 3 档授权
_dev_d_try_include(
    "permissions_router",
    "sisoul.daemon_routes.permissions",
    "dev-C permissions_router",
)


# ── pydantic 模型 ────────────────────────────────────────────────────────────


class _BorrowRequestBody(_BaseModel):
    borrower_did: str = _Field(..., description="Alice DID")
    lender_did: str = _Field(..., description="Bob DID")
    resource_type: str = _Field("llm_quota", description="llm_quota / ai_skill / compute")
    amount: int = _Field(..., description="tokens 或 minutes 或 1 (per skill use)")
    model: str = _Field(..., description="claude-opus-4-7 / gpt-5 / <skill-id>")
    provider: str = _Field("openai", description="anthropic / openai / … (lender 端 get_adapter)")
    prompt: str = _Field("", description="给 LLM 的 prompt (proxy stub 也接受)")
    force_mode: Optional[str] = _Field(
        None,
        description="跳过 permissions check 强制: strong-tie-auto/per-request/emergency-only",
    )
    emergency_flag: bool = _Field(False)
    per_request_timeout_sec: float = _Field(30.0)
    enqueue_onchain: bool = _Field(True)
    lend_db: Optional[str] = None
    pending_file: Optional[str] = None
    ledger_db: Optional[str] = None


class _BorrowResponseBody(_BaseModel):
    session: dict[str, _Any]


class _LendRequestBody(_BaseModel):
    borrower_did: str
    lender_did: str
    resource_type: str = "llm_quota"
    amount: int
    model: str
    mode: str = "per-request"
    ttl_sec: int = 30
    emergency_flag: bool = False
    lend_db: Optional[str] = None
    pending_file: Optional[str] = None


class _LendApproveBody(_BaseModel):
    request_id: str
    lend_db: Optional[str] = None
    pending_file: Optional[str] = None


class _LendDenyBody(_BaseModel):
    request_id: str
    reason: Optional[str] = None
    lend_db: Optional[str] = None
    pending_file: Optional[str] = None


class _ProxySessionStartBody(_BaseModel):
    borrower_did: str
    lender_did: str
    model: str
    base_url: str = "http://127.0.0.1:9876"


# ── /sisoul/borrow ───────────────────────────────────────────────────────────


@friend_router.post("/sisoul/borrow", response_model=_BorrowResponseBody)
def _post_borrow(body: _BorrowRequestBody) -> _BorrowResponseBody:
    fm = body.force_mode
    if fm is not None and fm not in (
        "strong-tie-auto", "per-request", "emergency-only"
    ):
        raise HTTPException(400, f"force_mode 非法: {fm}")
    try:
        sess: _BorrowSession = _borrow_resource(
            borrower_did=body.borrower_did,
            lender_did=body.lender_did,
            resource_type=body.resource_type,
            amount=body.amount,
            model=body.model,
            prompt=body.prompt,
            provider=body.provider,
            force_mode=fm,  # type: ignore[arg-type]
            emergency_flag=body.emergency_flag,
            per_request_timeout_sec=body.per_request_timeout_sec,
            lend_db=body.lend_db,
            pending_file=body.pending_file,
            ledger_db=body.ledger_db,
            enqueue_onchain=body.enqueue_onchain,
        )
        return _BorrowResponseBody(session=sess.to_dict())
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"borrow 失败 ({type(e).__name__}): {e}") from e


# ── /sisoul/lend/* ───────────────────────────────────────────────────────────


@friend_router.post("/sisoul/lend/request")
def _post_lend_request(body: _LendRequestBody) -> dict[str, _Any]:
    if body.mode not in ("strong-tie-auto", "per-request", "emergency-only"):
        raise HTTPException(400, f"mode 非法: {body.mode}")
    try:
        req = __request_lend(
            borrower_did=body.borrower_did,
            lender_did=body.lender_did,
            resource_type=body.resource_type,
            amount=body.amount,
            model=body.model,
            mode=body.mode,  # type: ignore[arg-type]
            ttl_sec=body.ttl_sec,
            emergency_flag=body.emergency_flag,
            db_path=body.lend_db,
            pending_file=body.pending_file,
        )
        return {"request": req.to_dict()}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"lend request 失败: {e}") from e


def _publish_lend_decision(req: _Any, decision: str, reason: str | None = None) -> None:
    """P1 2026-06-10: 批/拒后回发 GossipSub ack 给借入方 + 推本机 SSE.

    借入方 daemon 的 ack loop 收到后落地它本地 LendStore, 解锁 per-request
    borrow 轮询. request_id 用借入方原始 id (ingest 时存进 note json 的
    lend_request_id), 本机 id 只是 fallback.

    Never raises (决策已落库, 推送失败不回滚).
    """
    import asyncio as _aio
    import json as _json
    import sys as _sys

    remote_rid = req.id
    try:
        note = _json.loads(req.note or "{}")
        remote_rid = note.get("lend_request_id") or req.id
    except Exception:  # noqa: BLE001
        pass

    # SSE (本机 PWA Lend 页刷新)
    try:
        from sisoul.daemon_events import publish as _ev_publish
        _ev_publish("lend.update", {
            "request_id": req.id,
            "decision": decision,
            "borrower_did": req.borrower_did,
            "reason": reason,
        })
    except Exception:  # noqa: BLE001
        pass

    # GossipSub ack → 借入方 (threadpool 线程: 自建 transport + asyncio.run)
    try:
        from sisoul.chat.transport import KuboGossipSubTransport
        from sisoul.friend.lend_gossipsub import publish_lend_ack

        async def _send() -> None:
            transport = KuboGossipSubTransport()
            await publish_lend_ack(
                transport,
                lender_did=req.lender_did,
                borrower_did=req.borrower_did,
                request_id=remote_rid,
                decision=decision,
                reason=reason,
            )

        _aio.run(_send())
    except Exception as e:  # noqa: BLE001
        print(f"[lend/{decision}] gossipsub ack publish failed: {type(e).__name__}: {e}",
              file=_sys.stderr)


@friend_router.post("/sisoul/lend/approve")
def _post_lend_approve(body: _LendApproveBody) -> dict[str, _Any]:
    try:
        req = __approve_lend(
            body.request_id, db_path=body.lend_db, pending_file=body.pending_file
        )
        _publish_lend_decision(req, "approved")
        return {"request": req.to_dict()}
    except _RequestNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except _RequestStateError as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"approve 失败: {e}") from e


@friend_router.post("/sisoul/lend/deny")
def _post_lend_deny(body: _LendDenyBody) -> dict[str, _Any]:
    try:
        req = __deny_lend(
            body.request_id,
            reason=body.reason,
            db_path=body.lend_db,
            pending_file=body.pending_file,
        )
        _publish_lend_decision(req, "denied", reason=body.reason)
        return {"request": req.to_dict()}
    except _RequestNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except _RequestStateError as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"deny 失败: {e}") from e


@friend_router.get("/sisoul/lend/pending")
def _get_lend_pending(
    lend_db: Optional[str] = Query(None),
    pending_file: Optional[str] = Query(None),
) -> dict[str, _Any]:
    try:
        reqs = __list_pending(db_path=lend_db, pending_file=pending_file)
        return {"count": len(reqs), "pending": [r.to_dict() for r in reqs]}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"list pending 失败: {e}") from e


@friend_router.get("/sisoul/lend/all")
def _get_lend_all(
    status: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=5000),
    lend_db: Optional[str] = Query(None),
    pending_file: Optional[str] = Query(None),
) -> dict[str, _Any]:
    try:
        store = _LendStore(db_path=lend_db, pending_file=pending_file)
        try:
            rs = store.list_all(limit=limit, status=status)
            return {"count": len(rs), "requests": [r.to_dict() for r in rs]}
        finally:
            store.close()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"list all 失败: {e}") from e


# ── /sisoul/ledger/* ─────────────────────────────────────────────────────────


@friend_router.get("/sisoul/ledger/imbalance")
def _get_ledger_imbalance(
    self_did: str = Query(..., description="本机 self DID"),
    threshold: float = Query(_DEFAULT_IMBALANCE_THRESHOLD, gt=0),
    ledger_db: Optional[str] = Query(None),
) -> dict[str, _Any]:
    try:
        led = _ReciprocityLedger(db_path=ledger_db, self_did=self_did)
        try:
            warnings = led.list_imbalance_warnings(threshold=threshold)
            return {
                "self_did": self_did,
                "threshold": threshold,
                "count": len(warnings),
                "warnings": [w.to_dict() for w in warnings],
            }
        finally:
            led.close()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"imbalance 查询失败: {e}") from e


@friend_router.get("/sisoul/ledger/stats")
def _get_ledger_stats(
    ledger_db: Optional[str] = Query(None),
) -> dict[str, _Any]:
    try:
        led = _ReciprocityLedger(db_path=ledger_db)
        try:
            return {"stats": led.stats()}
        finally:
            led.close()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"stats 失败: {e}") from e


@friend_router.get("/sisoul/ledger/{friend_did:path}")
def _get_ledger_friend(
    friend_did: str,
    self_did: str = Query(..., description="本机 self DID"),
    threshold: float = Query(_DEFAULT_IMBALANCE_THRESHOLD, gt=0),
    ledger_db: Optional[str] = Query(None),
    entries_limit: int = Query(50, ge=0, le=2000),
) -> dict[str, _Any]:
    try:
        led = _ReciprocityLedger(db_path=ledger_db, self_did=self_did)
        try:
            bal = led.query_balance(friend_did, threshold=threshold)
            entries = led.list_entries(
                friend_did=friend_did, self_did=self_did, limit=entries_limit
            )
            return {
                "balance": bal.to_dict(),
                "entries": [e.to_dict() for e in entries],
            }
        finally:
            led.close()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"ledger 查询失败: {e}") from e


# ── /sisoul/borrow-proxy/* ───────────────────────────────────────────────────


@friend_router.post("/sisoul/borrow-proxy/start")
def _post_borrow_proxy_start(body: _ProxySessionStartBody) -> dict[str, _Any]:
    sess = _start_proxy_session(
        borrower_did=body.borrower_did,
        lender_did=body.lender_did,
        model=body.model,
        base_url=body.base_url,
    )
    return {"session": sess.to_dict()}


@friend_router.post("/sisoul/borrow-proxy/{session_id}/stop")
def _post_borrow_proxy_stop(session_id: str) -> dict[str, _Any]:
    sess = _stop_proxy_session(session_id)
    if not sess:
        raise HTTPException(404, f"proxy session {session_id} 不存在")
    return {"session": sess.to_dict()}


@friend_router.get("/sisoul/borrow-proxy/list")
def _get_borrow_proxy_list() -> dict[str, _Any]:
    sessions = _list_proxy_sessions()
    return {"count": len(sessions), "sessions": [s.to_dict() for s in sessions]}


@friend_router.get("/sisoul/borrow-proxy/{session_id}")
def _get_borrow_proxy(session_id: str) -> dict[str, _Any]:
    sess = _get_proxy_session(session_id)
    if not sess:
        raise HTTPException(404, f"proxy session {session_id} 不存在")
    return {"session": sess.to_dict()}
