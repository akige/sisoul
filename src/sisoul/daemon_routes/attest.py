"""sisoul daemon · EAS attestation HTTP API (Phase 3 W37-W40, 波 4 dev-B).

⚠️ Router 命名强制规范: ``attest_router = APIRouter(prefix="/sisoul/attest", tags=["attest"])``
(波 3 dev-C 用 `router` 集成 bug, 父会话强制规范本 wave 起所有 router 都用 `<topic>_router`.)

Endpoints (§28 §2.1 + 波 4 task spec):
- POST /sisoul/audit              波 2 dev-D 写 hook 调这, 接收 + 写 queue (跨 prefix 故单独 register)
- GET  /sisoul/attest/queue       当前 queue
- POST /sisoul/attest/flush       强制 batch
- GET  /sisoul/attest/history     历史 (local / onchain)
- GET  /sisoul/attest/verify/{uid} verify

由主 daemon.py 通过 ``app.include_router(attest_router)`` + ``app.include_router(audit_router)``
两 router 整合 (audit 在 /sisoul/audit 不在 /sisoul/attest, 跨 prefix 单独 router 实现).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from sisoul.onchain.eas import (
    AttestConfig,
    AttestQueue,
    AuditAttestation,
    AttestationNotFoundError,
    EASError,
    NetworkNotSupportedError,
    QueueEmptyError,
    list_history_local,
    list_history_onchain,
    load_config,
    resolve_attester_did,
    resolve_chain,
    upload_batch,
    verify_attestation_local,
    verify_attestation_onchain,
)


def _apply_chain(cfg: AttestConfig, chain: Optional[str]) -> AttestConfig:
    """P3-5: ?chain=optimism|arbitrum|base|zksync 覆盖 cfg.network + rpc_url."""
    if not chain:
        return cfg
    cc = resolve_chain(chain)
    cfg.network = cc.name  # type: ignore[assignment]
    cfg.rpc_url = cc.rpc_url
    return cfg

attest_router = APIRouter(prefix="/sisoul/attest", tags=["attest"])

# POST /sisoul/audit 跨 prefix → 单独 router, 主 daemon.py 也得 include 两个.
audit_router = APIRouter(prefix="/sisoul", tags=["audit"])


# ── 内部 helper ──────────────────────────────────────────────────────────────


def _queue(db_path: Optional[str] = None) -> AttestQueue:
    return AttestQueue(db_path=Path(db_path) if db_path else None)


def _config(config_path: Optional[str] = None) -> AttestConfig:
    return load_config(Path(config_path) if config_path else None)


# ── schemas ──────────────────────────────────────────────────────────────────


class AuditRequest(BaseModel):
    """POST /sisoul/audit 入参 (波 2 dev-D hook 发).

    actor_did 可省 (走本地 registry 默认 DID); prompt 由 server 端算 sha256.
    """

    action_type: str = Field(..., description="rm / git-push / chmod / curl-post / ...")
    target: str = Field(..., description="file path / URL / host")
    prompt: str = Field("", description="user prompt 文本 (server 算 sha256)")
    tool_name: str = Field("unknown", description="claude-code / codex / aider / ...")
    actor_did: Optional[str] = Field(
        None, description="覆盖默认 attester DID (省略则用 config / registry 第一条)"
    )
    timestamp: Optional[int] = Field(None, description="unix epoch, 默认当前时间")
    # 控制开关
    config_path: Optional[str] = Field(None, description="可选 config 路径")
    queue_db: Optional[str] = Field(None, description="可选 queue DB 路径")
    auto_flush: bool = Field(
        True, description="入队后, 若达 batch 条件自动 flush (默认 True)"
    )


class AuditResponse(BaseModel):
    queue_id: str
    actor_did: str
    queued_at: str
    auto_flushed: bool = False
    batch_uid: Optional[str] = None
    tx_hash: Optional[str] = None


class QueueItemSummary(BaseModel):
    queue_id: str
    actor_did: str
    action_type: str
    target: str
    tool_name: str
    queued_at: str
    status: str
    attestation_uid: Optional[str] = None
    tx_hash: Optional[str] = None


class QueueListResponse(BaseModel):
    stats: dict[str, int]
    items: list[QueueItemSummary]


class FlushRequest(BaseModel):
    force: bool = False
    max_items: Optional[int] = None
    config_path: Optional[str] = None
    queue_db: Optional[str] = None
    # P3-5: 跨链 query (POST body 也支持)
    chain: Optional[str] = None


class FlushResponse(BaseModel):
    batch_uid: str
    tx_hash: str
    network: str
    schema_uid: str
    attestation_uids: list[str]
    count: int
    method: str
    gas_used_estimate: int
    gas_cost_wei_estimate: int
    confirmed_at: str


class HistoryItem(BaseModel):
    # local batch view
    batch_uid: Optional[str] = None
    tx_hash: Optional[str] = None
    network: Optional[str] = None
    count: Optional[int] = None
    method: Optional[str] = None
    confirmed_at: Optional[str] = None
    # onchain raw passthrough
    id: Optional[str] = None
    attester: Optional[str] = None
    recipient: Optional[str] = None
    schemaId: Optional[str] = None
    time: Optional[Any] = None


class HistoryResponse(BaseModel):
    source: str
    items: list[HistoryItem]


class VerifyResponse(BaseModel):
    uid: str
    local: dict[str, Any]
    onchain: Optional[dict[str, Any]] = None


# ── POST /sisoul/audit ───────────────────────────────────────────────────────


@audit_router.post("/audit", response_model=AuditResponse)
def receive_audit(req: AuditRequest) -> AuditResponse:
    """波 2 dev-D hook 调这: destructive 操作发 audit → 写 attest queue.

    流程:
    1. 解析 actor_did (req.actor_did → cfg.attester_did → 本地 DID registry 第一条)
    2. 用 AuditAttestation.from_audit_payload 算 prompt_hash + 入队
    3. 若 auto_flush=True 且达 batch 条件 → upload_batch
    """
    try:
        cfg = load_config(Path(req.config_path) if req.config_path else None)
    except EASError as e:
        raise HTTPException(status_code=500, detail=f"config 读取失败: {e}")

    # 解析 attester
    if req.actor_did:
        actor_did = req.actor_did
    else:
        try:
            actor_did = resolve_attester_did(cfg)
        except EASError as e:
            # fail-open: 没 DID 也允许入队, 用 fallback (daemon hook 不该因 DID 缺失 abort)
            actor_did = "did:sisoul:unknown"

    att = AuditAttestation.from_audit_payload(
        actor_did=actor_did,
        action_type=req.action_type,
        target=req.target,
        prompt=req.prompt or "",
        tool_name=req.tool_name,
        timestamp=req.timestamp,
    )

    auto_flushed = False
    batch_uid: Optional[str] = None
    tx_hash: Optional[str] = None

    db_path = Path(req.queue_db) if req.queue_db else None
    with AttestQueue(db_path=db_path) as q:
        q.enqueue(att)
        if req.auto_flush and q.should_flush(cfg.batch_size, cfg.batch_timeout_sec):
            try:
                result = upload_batch(q, cfg)
                auto_flushed = True
                batch_uid = result.batch_uid
                tx_hash = result.tx_hash
            except (QueueEmptyError, NetworkNotSupportedError, EASError):
                # 入队成功, batch 失败不 fail 整个 audit; daemon 总能稍后 flush
                pass

    return AuditResponse(
        queue_id=att.queue_id,
        actor_did=att.actor_did,
        queued_at=att.queued_at,
        auto_flushed=auto_flushed,
        batch_uid=batch_uid,
        tx_hash=tx_hash,
    )


# ── GET /sisoul/attest/queue ─────────────────────────────────────────────────


@attest_router.get("/queue", response_model=QueueListResponse)
def get_queue(
    status: str = Query("pending", description="pending/batched/confirmed/failed/all"),
    limit: int = Query(50, ge=1, le=500),
    queue_db: Optional[str] = Query(None, description="SQLite path 覆盖"),
) -> QueueListResponse:
    """当前 queue."""
    db = Path(queue_db) if queue_db else None
    with AttestQueue(db_path=db) as q:
        if status == "all":
            items = q.all_items(status=None, limit=limit)
        elif status in ("pending", "batched", "confirmed", "failed"):
            items = q.all_items(status=status, limit=limit)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"status 必须是 pending/batched/confirmed/failed/all (拿到 '{status}')",
            )
        stats = q.stats()

    return QueueListResponse(
        stats=stats,
        items=[
            QueueItemSummary(
                queue_id=it.queue_id,
                actor_did=it.actor_did,
                action_type=it.action_type,
                target=it.target,
                tool_name=it.tool_name,
                queued_at=it.queued_at,
                status=it.status,
                attestation_uid=it.attestation_uid,
                tx_hash=it.tx_hash,
            )
            for it in items
        ],
    )


# ── POST /sisoul/attest/flush ────────────────────────────────────────────────


@attest_router.post("/flush", response_model=FlushResponse)
def post_flush(req: FlushRequest, chain: Optional[str] = Query(None)) -> FlushResponse:
    """强制 batch 上链 (跳过 batch_size 阈值). P3-5 支持 ?chain= 或 body.chain."""
    try:
        cfg = _config(req.config_path)
    except EASError as e:
        raise HTTPException(status_code=500, detail=f"config: {e}")

    # P3-5: query param 优先于 body
    chain_eff = chain or req.chain
    try:
        cfg = _apply_chain(cfg, chain_eff)
    except NetworkNotSupportedError as e:
        raise HTTPException(status_code=403, detail=str(e))

    db = Path(req.queue_db) if req.queue_db else None
    try:
        with AttestQueue(db_path=db) as q:
            result = upload_batch(q, cfg, force=req.force, max_items=req.max_items)
    except QueueEmptyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except NetworkNotSupportedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except EASError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return FlushResponse(
        batch_uid=result.batch_uid,
        tx_hash=result.tx_hash,
        network=result.network,
        schema_uid=result.schema_uid,
        attestation_uids=result.attestation_uids,
        count=result.count,
        method=result.method,
        gas_used_estimate=result.gas_used_estimate,
        gas_cost_wei_estimate=result.gas_cost_wei_estimate,
        confirmed_at=result.confirmed_at,
    )


# ── GET /sisoul/attest/history ───────────────────────────────────────────────


@attest_router.get("/history", response_model=HistoryResponse)
def get_history(
    source: str = Query("local", description="local / onchain"),
    attester: Optional[str] = Query(None, description="onchain 过滤 attester 地址"),
    limit: int = Query(20, ge=1, le=200),
    config_path: Optional[str] = Query(None),
    queue_db: Optional[str] = Query(None),
    chain: Optional[str] = Query(
        None, description="P3-5: optimism/arbitrum/base/zksync (覆盖 config.network)"
    ),
) -> HistoryResponse:
    """attestation 历史 (本地 batches 或链上 EAS GraphQL)."""
    try:
        cfg = _config(config_path)
    except EASError as e:
        raise HTTPException(status_code=500, detail=f"config: {e}")

    try:
        cfg = _apply_chain(cfg, chain)
    except NetworkNotSupportedError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if source == "local":
        db = Path(queue_db) if queue_db else None
        with AttestQueue(db_path=db) as q:
            batches = list_history_local(q, limit=limit)
        items = [
            HistoryItem(
                batch_uid=b.batch_uid,
                tx_hash=b.tx_hash,
                network=b.network,
                count=b.count,
                method=b.method,
                confirmed_at=b.confirmed_at,
            )
            for b in batches
        ]
        return HistoryResponse(source="local", items=items)

    if source == "onchain":
        try:
            atts = list_history_onchain(
                attester=attester, network=cfg.network, limit=limit
            )
        except NetworkNotSupportedError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except EASError as e:
            raise HTTPException(status_code=502, detail=str(e))
        items = [
            HistoryItem(
                id=a.get("id"),
                attester=a.get("attester"),
                recipient=a.get("recipient"),
                schemaId=a.get("schemaId"),
                time=a.get("time"),
            )
            for a in atts
        ]
        return HistoryResponse(source="onchain", items=items)

    raise HTTPException(status_code=400, detail=f"source 必须是 local / onchain")


# ── GET /sisoul/attest/verify/{uid} ──────────────────────────────────────────


@attest_router.get("/verify/{uid}", response_model=VerifyResponse)
def get_verify(
    uid: str,
    onchain: bool = Query(False, description="同时查链上 EAS GraphQL"),
    queue_db: Optional[str] = Query(None),
    config_path: Optional[str] = Query(None),
    chain: Optional[str] = Query(
        None, description="P3-5: optimism/arbitrum/base/zksync (覆盖 config.network)"
    ),
) -> VerifyResponse:
    """verify attestation (本地 recompute + 可选 onchain)."""
    try:
        cfg = _config(config_path)
    except EASError as e:
        raise HTTPException(status_code=500, detail=f"config: {e}")

    try:
        cfg = _apply_chain(cfg, chain)
    except NetworkNotSupportedError as e:
        raise HTTPException(status_code=403, detail=str(e))

    db = Path(queue_db) if queue_db else None
    with AttestQueue(db_path=db) as q:
        try:
            local = verify_attestation_local(q, uid)
        except AttestationNotFoundError as e:
            local = {
                "valid": False,
                "method": "local-recompute",
                "reason": str(e),
            }

    onchain_result: Optional[dict[str, Any]] = None
    if onchain:
        try:
            onchain_result = verify_attestation_onchain(
                uid, network=cfg.network, rpc_url=cfg.rpc_url
            )
        except NetworkNotSupportedError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except EASError as e:
            onchain_result = {
                "valid": False,
                "method": "onchain-graphql",
                "reason": str(e),
            }

    return VerifyResponse(uid=uid, local=local, onchain=onchain_result)


__all__ = ["attest_router", "audit_router"]
