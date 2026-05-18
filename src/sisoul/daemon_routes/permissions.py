"""sisoul daemon · friend permissions HTTP API (Phase 4 W59-W65, 波 5 dev-C).

⚠️ Router 命名强制规范: ``permissions_router = APIRouter(prefix="/sisoul/perms", tags=["permissions"])``
(波 3 dev-C 用 `router` 集成 bug, 父会话强制规范本 wave 起所有 router 都用 `<topic>_router`.)

Endpoints (§28 §3.3 + §3.7):
- GET  /sisoul/perms/list          列朋友权限 (?friend=DID 过滤)
- POST /sisoul/perms/set           改授权 (body: friend_did + perm)
- POST /sisoul/perms/revoke        L3 即时撤销 + 链上 REVOKE
- GET  /sisoul/perms/reputation    L4 reputation 查询/上链
- POST /sisoul/perms/check         核心: 判 Alice 借 X 是否准 (dev-D borrow 内部调)
- GET  /sisoul/perms/scan-log      L5 scan 拦截日志

由主 daemon.py 通过 ``app.include_router(permissions_router)`` 整合 (主集成 layer 负责).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from sisoul.friend.anti_abuse import (
    compute_reputation,
    list_scan_log,
    publish_reputation_attestation,
    revoke_friend_permission,
)
from sisoul.friend.permissions import (
    AISkillShare,
    ComputeShare,
    FriendPermission,
    InvalidPermissionConfigError,
    LLMQuotaShare,
    PermissionNotFoundError,
    VALID_MODES,
    VALID_RESOURCES,
    check_permission,
    list_all_friends,
    load_permissions,
    save_permissions,
)

permissions_router = APIRouter(prefix="/sisoul/perms", tags=["permissions"])


# ── schemas ──────────────────────────────────────────────────────────────────


class LLMQuotaShareBody(BaseModel):
    enabled: bool = False
    mode: str = "per-request"
    monthly_token_cap: int = 0
    rate_limit: int = 0
    models: list[str] = Field(default_factory=list)
    emergency_reserve_tokens: int = 0


class AISkillShareBody(BaseModel):
    enabled: bool = False
    mode: str = "per-request"
    skills: list[str] = Field(default_factory=list)
    per_session_max_minutes: int = 0


class ComputeShareBody(BaseModel):
    enabled: bool = False
    mode: str = "per-request"
    cpu_cores: int = 0
    ram_mb: int = 0


class PermissionBody(BaseModel):
    """POST /sisoul/perms/set 入参."""

    friend_did: str
    llm_quota_share: Optional[LLMQuotaShareBody] = None
    ai_skill_share: Optional[AISkillShareBody] = None
    compute_share: Optional[ComputeShareBody] = None
    perms_dir: Optional[str] = Field(None, description="测试用; 覆盖 ~/.sisoul/friends/")


class PermissionView(BaseModel):
    friend: str
    llm_quota_share: dict[str, Any]
    ai_skill_share: dict[str, Any]
    compute_share: dict[str, Any]
    revoked: bool
    revoked_at: Optional[str]
    revoked_reason: Optional[str]


class RevokeBody(BaseModel):
    friend_did: str
    reason: str = ""
    perms_dir: Optional[str] = None


class RevokeResponse(BaseModel):
    revoked: bool
    friend_did: str
    revoked_at: Optional[str] = None
    reason: str = ""
    attestation_queue_id: Optional[str] = None


class CheckBody(BaseModel):
    """POST /sisoul/perms/check 入参 (dev-D borrow 调)."""

    friend_did: str
    resource_type: str  # llm_quota / ai_skill / compute
    amount: int
    model: Optional[str] = None
    emergency_flag: bool = False
    per_request_approved: bool = False
    current_usage: Optional[int] = None
    perms_dir: Optional[str] = None


class CheckResponse(BaseModel):
    allowed: bool
    reason: str
    friend_did: str
    resource_type: str
    amount: int


class ReputationResponse(BaseModel):
    did: str
    score: int
    grade: str
    borrows: int
    lends: int
    abuse_incidents: int
    spam_complaints: int
    balance_ratio: float
    computed_at: str
    attestation_queue_id: Optional[str] = None


class ListResponse(BaseModel):
    count: int
    friends: list[PermissionView]


class ScanLogItem(BaseModel):
    id: int
    friend_did: str
    allowed: bool
    reason: str
    amount: Optional[int] = None
    model: Optional[str] = None
    prompt_hash: Optional[str] = None
    ts: str
    details: dict[str, Any] = Field(default_factory=dict)


class ScanLogResponse(BaseModel):
    count: int
    events: list[ScanLogItem]


# ── helper ───────────────────────────────────────────────────────────────────


def _to_view(p: FriendPermission) -> PermissionView:
    return PermissionView(
        friend=p.friend_did,
        llm_quota_share=p.llm_quota_share.to_dict(),
        ai_skill_share=p.ai_skill_share.to_dict(),
        compute_share=p.compute_share.to_dict(),
        revoked=p.revoked,
        revoked_at=p.revoked_at,
        revoked_reason=p.revoked_reason,
    )


def _resolve_perms_dir(s: Optional[str]) -> Optional[Path]:
    return Path(s) if s else None


# ── GET /sisoul/perms/list ───────────────────────────────────────────────────


@permissions_router.get("/list", response_model=ListResponse)
def get_list(
    friend: Optional[str] = Query(None, description="只查某 DID, 省略=全部"),
    perms_dir: Optional[str] = Query(None, description="测试用"),
) -> ListResponse:
    pd = _resolve_perms_dir(perms_dir)
    if friend:
        try:
            p = load_permissions(friend, pd)
        except PermissionNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return ListResponse(count=1, friends=[_to_view(p)])

    friends = list_all_friends(pd)
    out: list[PermissionView] = []
    for f in friends:
        try:
            out.append(_to_view(load_permissions(f, pd)))
        except Exception:
            continue
    return ListResponse(count=len(out), friends=out)


# ── POST /sisoul/perms/set ───────────────────────────────────────────────────


@permissions_router.post("/set", response_model=PermissionView)
def post_set(body: PermissionBody) -> PermissionView:
    pd = _resolve_perms_dir(body.perms_dir)
    try:
        perm = load_permissions(body.friend_did, pd)
    except PermissionNotFoundError:
        perm = FriendPermission(friend_did=body.friend_did)

    if body.llm_quota_share is not None:
        d = body.llm_quota_share.model_dump()
        if d.get("mode") and d["mode"] not in VALID_MODES:
            raise HTTPException(
                status_code=422,
                detail=f"llm_quota_share.mode 非法: {d['mode']}; 必须 ∈ {VALID_MODES}",
            )
        perm.llm_quota_share = LLMQuotaShare.from_dict(d)
    if body.ai_skill_share is not None:
        d = body.ai_skill_share.model_dump()
        if d.get("mode") and d["mode"] not in VALID_MODES:
            raise HTTPException(
                status_code=422,
                detail=f"ai_skill_share.mode 非法: {d['mode']}",
            )
        perm.ai_skill_share = AISkillShare.from_dict(d)
    if body.compute_share is not None:
        d = body.compute_share.model_dump()
        if d.get("mode") and d["mode"] not in VALID_MODES:
            raise HTTPException(
                status_code=422,
                detail=f"compute_share.mode 非法: {d['mode']}",
            )
        perm.compute_share = ComputeShare.from_dict(d)

    try:
        save_permissions(body.friend_did, perm, pd)
    except InvalidPermissionConfigError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return _to_view(perm)


# ── POST /sisoul/perms/revoke ────────────────────────────────────────────────


@permissions_router.post("/revoke", response_model=RevokeResponse)
def post_revoke(body: RevokeBody) -> RevokeResponse:
    pd = _resolve_perms_dir(body.perms_dir)
    r = revoke_friend_permission(body.friend_did, reason=body.reason, perms_dir=pd)
    return RevokeResponse(
        revoked=True,
        friend_did=body.friend_did,
        revoked_at=r.get("revoked_at"),
        reason=body.reason,
        attestation_queue_id=r.get("attestation_queue_id"),
    )


# ── POST /sisoul/perms/check (dev-D borrow 调) ───────────────────────────────


@permissions_router.post("/check", response_model=CheckResponse)
def post_check(body: CheckBody) -> CheckResponse:
    pd = _resolve_perms_dir(body.perms_dir)
    if body.resource_type not in VALID_RESOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"resource_type 非法: {body.resource_type}; ∈ {VALID_RESOURCES}",
        )
    allowed, reason = check_permission(
        friend_did=body.friend_did,
        resource_type=body.resource_type,
        amount=body.amount,
        model=body.model,
        perms_dir=pd,
        emergency_flag=body.emergency_flag,
        per_request_approved=body.per_request_approved,
        current_usage=body.current_usage,
    )
    return CheckResponse(
        allowed=allowed,
        reason=reason,
        friend_did=body.friend_did,
        resource_type=body.resource_type,
        amount=body.amount,
    )


# ── GET /sisoul/perms/reputation ─────────────────────────────────────────────


@permissions_router.get("/reputation", response_model=ReputationResponse)
def get_reputation(
    did: str = Query(..., description="目标 DID (自己或朋友)"),
    borrows: int = Query(0, ge=0),
    lends: int = Query(0, ge=0),
    abuse_incidents: int = Query(0, ge=0),
    spam_complaints: int = Query(0, ge=0),
    publish: bool = Query(False, description="同时上链 REPUTATION_PUBLISH"),
) -> ReputationResponse:
    rep = compute_reputation(
        did,
        borrows=borrows,
        lends=lends,
        abuse_incidents=abuse_incidents,
        spam_complaints=spam_complaints,
    )
    qid: Optional[str] = None
    if publish:
        qid = publish_reputation_attestation(rep)
    return ReputationResponse(
        did=rep.did,
        score=rep.score,
        grade=rep.grade,
        borrows=rep.borrows,
        lends=rep.lends,
        abuse_incidents=rep.abuse_incidents,
        spam_complaints=rep.spam_complaints,
        balance_ratio=rep.balance_ratio,
        computed_at=rep.computed_at,
        attestation_queue_id=qid,
    )


# ── GET /sisoul/perms/scan-log ───────────────────────────────────────────────


@permissions_router.get("/scan-log", response_model=ScanLogResponse)
def get_scan_log(
    friend: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=500),
    only_blocked: bool = Query(True),
    scan_db: Optional[str] = Query(None, description="测试用"),
) -> ScanLogResponse:
    db = Path(scan_db) if scan_db else None
    rows = list_scan_log(
        limit=limit, friend_did=friend, only_blocked=only_blocked, db_path=db
    )
    return ScanLogResponse(
        count=len(rows),
        events=[ScanLogItem(**r) for r in rows],
    )


__all__ = ["permissions_router"]
