"""sisoul daemon · AI 技能 share HTTP API (Phase 4 W70-W74 · 波 6 dev-A).

§28 §3.6 + 波 6 dev-A 任务 spec. Router 命名强制规范:
    skill_router = APIRouter(prefix="/sisoul/skill", tags=["skill"])

Endpoints:
- POST /sisoul/skill/create        owner 训练 + 打包新 skill
- GET  /sisoul/skill/list          列 owned + available-to-borrow
- POST /sisoul/skill/lend          标 skill 可借 (可选立即 IPFS pin)
- POST /sisoul/skill/borrow        borrower 起 borrow session (走 self-loop / 真 P2P)
- GET  /sisoul/skill/sessions      列 active session
- POST /sisoul/skill/end-session   主动 destroy session
- POST /sisoul/skill/proxy-chat    用 skill 跑 LLM (borrower 自己 key)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from sisoul.friend.skill_borrow import (
    DEFAULT_BORROW_DURATION_MINUTES,
    SkillBorrowError,
    SkillBorrowExpiredError,
    SkillBorrowPermissionError,
    SkillBorrowSession,
    SkillBorrowSessionNotFoundError,
    end_skill_borrow_session,
    get_borrow_session,
    list_borrow_sessions,
    proxy_skill_chat,
    request_borrow_skill,
)
from sisoul.friend.skill_ipfs import (
    SkillIPFSClient,
    SkillIPFSError,
    SkillPinDB,
    register_mock_blob,
)
from sisoul.friend.skill_package import (
    DEFAULT_SKILL_EXPIRY_HOURS,
    InvalidSkillPackageError,
    SkillPackage,
    decrypt_skill_package,
    encrypt_skill_package,
    package_skill,
    parse_qualified_name,
)

logger = logging.getLogger(__name__)

# 强制命名: skill_router (daemon.py include_router 时一行接入).
skill_router = APIRouter(prefix="/sisoul/skill", tags=["skill"])


# ── owned skill 本地存储 (跟 cli_commands/skill.py 共享) ────────────────────


def _owned_skills_dir() -> Path:
    """lazy 重算 (兼容 monkeypatch HOME for tests)."""
    return Path.home() / ".sisoul" / "skills" / "owned"


def _owned_path(skill_id: str) -> Path:
    d = _owned_skills_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{skill_id}.json"


def _load_owned(skill_id: str) -> SkillPackage:
    p = _owned_path(skill_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"owned skill 不存在: {skill_id}")
    return SkillPackage.from_json(p.read_text(encoding="utf-8"))


def _list_owned() -> list[SkillPackage]:
    out: list[SkillPackage] = []
    d = _owned_skills_dir()
    if not d.exists():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            out.append(SkillPackage.from_json(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def _resolve_own_did(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    try:
        from sisoul.identity.did import list_local_dids  # type: ignore[import-untyped]
        dids = list_local_dids()
        if dids:
            return dids[0].did_string
    except Exception:
        pass
    return "me.local"


def _self_keypair():
    """派生本机 long-term keypair (复用 dev-B derive_friend_session_keypair, index=0).

    daemon scope; 真生产可按 friend_index 派不同 keypair per friend.
    本 wave self-loop 测试只用 index=0.
    """
    from sisoul.friend.encrypted_proxy import derive_friend_session_keypair
    from sisoul.identity.seed import (
        load_mnemonic_from_file,
        mnemonic_to_master_key,
    )

    mnemonic = load_mnemonic_from_file()
    master = mnemonic_to_master_key(mnemonic)
    return derive_friend_session_keypair(master, friend_index=0)


# ── schemas ─────────────────────────────────────────────────────────────────


class _CreateBody(BaseModel):
    name: str
    system_prompt: str
    description: str = ""
    version: str = "0.1.0"
    examples: Optional[list[dict[str, Any]]] = None
    preference_overlay: Optional[dict[str, Any]] = None
    tool_call_templates: Optional[list[dict[str, Any]]] = None
    personality_traits: Optional[list[str]] = None
    recommended_models: Optional[list[str]] = None
    expiry_hours: int = DEFAULT_SKILL_EXPIRY_HOURS
    owner_did: Optional[str] = None


class _CreateResponse(BaseModel):
    skill_id: str
    qualified_name: str
    owner_did: str
    version: str
    fingerprint: str
    examples_count: int


class _SkillSummary(BaseModel):
    skill_id: str
    qualified_name: str
    owner_did: str
    version: str
    description: str
    fingerprint: str
    examples_count: int
    personality_traits: list[str]
    recommended_models: list[str]


class _ListResponse(BaseModel):
    own_did: str
    owned: list[_SkillSummary]
    available_to_borrow: list[dict[str, Any]]


class _LendBody(BaseModel):
    skill_id: str
    max_duration_minutes: int = DEFAULT_BORROW_DURATION_MINUTES
    pin_to_ipfs: bool = False
    recipient_pubkey_b64: Optional[str] = None
    expiry_hours: int = DEFAULT_SKILL_EXPIRY_HOURS


class _LendResponse(BaseModel):
    skill_id: str
    qualified_name: str
    max_duration_minutes: int
    ipfs_cid: Optional[str] = None
    encrypted_b64: Optional[str] = None
    sender_pubkey_b64: Optional[str] = None


class _BorrowBody(BaseModel):
    qualified_name: str
    duration_minutes: int = DEFAULT_BORROW_DURATION_MINUTES
    duration_seconds_override: Optional[int] = None
    borrower_did: Optional[str] = None
    per_request_approved: bool = False
    emergency_flag: bool = False
    skip_permission_check: bool = False
    # self-loop 模式 (开发期 / test): owner == self, 走本机 owned skill 自加密
    # 真生产: daemon 走 P2P 跟 owner 协商, 这里 ship self-loop branch (跟 CLI 一致).


class _BorrowResponse(BaseModel):
    session_id: str
    qualified_name: str
    owner_did: str
    borrower_did: str
    skill_id: str
    started_at: int
    expires_at: int
    duration_minutes: int
    ipfs_cid: Optional[str]
    skill_package_fingerprint: str
    permission_reason: str
    used_fallback: bool


class _SessionsResponse(BaseModel):
    own_did: str
    sessions: list[dict[str, Any]]


class _EndSessionBody(BaseModel):
    session_id: str
    reason: str = "manual"


class _EndSessionResponse(BaseModel):
    session_id: str
    status: str
    destroy_reason: Optional[str]
    destroyed_at: Optional[int]
    ledger_entry_id: Optional[str]


class _ProxyChatBody(BaseModel):
    session_id: str
    prompt: str
    model: Optional[str] = None
    provider: str = "anthropic"
    llm_api_key: Optional[str] = None
    use_mock_forwarder: bool = False


class _ProxyChatResponse(BaseModel):
    text: str
    tokens_used: int
    prompt_tokens: int
    response_tokens: int
    model_used: str
    session_id: str
    session_remaining_sec: int
    skill_id: str
    owner_did: str


# ── helper: SkillPackage → summary ──────────────────────────────────────────


def _to_summary(pkg: SkillPackage) -> _SkillSummary:
    return _SkillSummary(
        skill_id=pkg.skill_id,
        qualified_name=pkg.qualified_name,
        owner_did=pkg.owner_did,
        version=pkg.version,
        description=pkg.description,
        fingerprint=pkg.fingerprint,
        examples_count=pkg.contents.few_shot_examples_count,
        personality_traits=pkg.contents.personality_traits,
        recommended_models=pkg.contents.recommended_models,
    )


# ── endpoints ───────────────────────────────────────────────────────────────


@skill_router.post("/create", response_model=_CreateResponse)
def create_skill(body: _CreateBody) -> _CreateResponse:
    own_did = _resolve_own_did(body.owner_did)
    try:
        pkg = package_skill(
            name=body.name,
            owner_did=own_did,
            system_prompt=body.system_prompt,
            description=body.description,
            version=body.version,
            examples=body.examples,
            preference_overlay=body.preference_overlay,
            tool_call_templates=body.tool_call_templates,
            personality_traits=body.personality_traits,
            recommended_models=body.recommended_models,
            expiry_hours=body.expiry_hours,
        )
    except (InvalidSkillPackageError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    _owned_path(pkg.skill_id).write_text(pkg.to_json(), encoding="utf-8")
    return _CreateResponse(
        skill_id=pkg.skill_id,
        qualified_name=pkg.qualified_name,
        owner_did=pkg.owner_did,
        version=pkg.version,
        fingerprint=pkg.fingerprint,
        examples_count=pkg.contents.few_shot_examples_count,
    )


@skill_router.get("/list", response_model=_ListResponse)
def list_skills(
    owned: bool = Query(True),
    available_to_borrow: bool = Query(False),
    own_did: Optional[str] = Query(None),
) -> _ListResponse:
    od = _resolve_own_did(own_did)
    owned_list: list[_SkillSummary] = []
    avail: list[dict[str, Any]] = []
    if owned:
        for pkg in _list_owned():
            owned_list.append(_to_summary(pkg))
    if available_to_borrow:
        try:
            with SkillPinDB() as db:
                pins = db.list_active(limit=200)
            for p in pins:
                if p.owner_did != od:
                    avail.append({
                        "cid": p.cid,
                        "owner_did": p.owner_did,
                        "skill_id": p.skill_id,
                        "expires_at": p.expires_at,
                        "size_bytes": p.size_bytes,
                    })
        except Exception as e:
            logger.warning("SkillPinDB 读失败: %s", e)
    return _ListResponse(own_did=od, owned=owned_list, available_to_borrow=avail)


@skill_router.post("/lend", response_model=_LendResponse)
def lend_skill(body: _LendBody) -> _LendResponse:
    pkg = _load_owned(body.skill_id)
    out = _LendResponse(
        skill_id=pkg.skill_id,
        qualified_name=pkg.qualified_name,
        max_duration_minutes=body.max_duration_minutes,
    )

    if body.pin_to_ipfs:
        if not body.recipient_pubkey_b64:
            raise HTTPException(
                status_code=400,
                detail="pin_to_ipfs=True 时必须给 recipient_pubkey_b64",
            )
        try:
            recipient_pub = base64.b64decode(body.recipient_pubkey_b64.encode("ascii"))
            sender_priv, sender_pub = _self_keypair()
            blob = encrypt_skill_package(pkg, recipient_pub, sender_priv)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"加密失败: {type(e).__name__}: {e}")

        try:
            from sisoul.friend.skill_ipfs import pin_skill_to_ipfs
            rec = pin_skill_to_ipfs(
                blob,
                owner_did=pkg.owner_did,
                skill_id=pkg.skill_id,
                expiry_hours=body.expiry_hours,
            )
            out.ipfs_cid = rec.cid
            out.encrypted_b64 = base64.b64encode(blob).decode("ascii")
            out.sender_pubkey_b64 = base64.b64encode(sender_pub.encode()).decode("ascii")
        except SkillIPFSError as e:
            raise HTTPException(status_code=500, detail=f"pin 失败: {e}")

    return out


@skill_router.post("/borrow", response_model=_BorrowResponse)
def borrow_skill(body: _BorrowBody) -> _BorrowResponse:
    try:
        owner_did, skill_id = parse_qualified_name(body.qualified_name)
    except InvalidSkillPackageError as e:
        raise HTTPException(status_code=400, detail=str(e))

    own_did = _resolve_own_did(body.borrower_did)

    # self-loop 模式 (本 wave 实现, 真 P2P Phase 5)
    if owner_did != own_did:
        raise HTTPException(
            status_code=501,
            detail=(
                f"远程 borrow (owner={owner_did} != self={own_did}) 走 P2P 真实现在 Phase 5. "
                "本 wave 仅支持 self-loop 验证 lifecycle."
            ),
        )

    try:
        pkg = _load_owned(skill_id)
        priv, pub = _self_keypair()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"keypair 派生失败: {e}")

    def provider(_o: str, _s: str) -> tuple[bytes, str]:
        blob = encrypt_skill_package(pkg, pub, priv)
        cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
        register_mock_blob(cid, blob)
        return blob, cid

    def decryptor(blob: bytes) -> SkillPackage:
        return decrypt_skill_package(blob, pub, priv)

    try:
        res = request_borrow_skill(
            owner_did=owner_did,
            skill_id=skill_id,
            borrower_did=own_did,
            duration_minutes=body.duration_minutes,
            duration_seconds_override=body.duration_seconds_override,
            encrypted_skill_provider=provider,
            decrypt_callback=decryptor,
            per_request_approved=body.per_request_approved,
            emergency_flag=body.emergency_flag,
            skip_permission_check=body.skip_permission_check,
        )
    except SkillBorrowPermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except SkillBorrowError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return _BorrowResponse(
        session_id=res.session.session_id,
        qualified_name=res.session.qualified_name,
        owner_did=res.session.owner_did,
        borrower_did=res.session.borrower_did,
        skill_id=res.session.skill_id,
        started_at=res.session.started_at,
        expires_at=res.session.expires_at,
        duration_minutes=res.session.duration_minutes,
        ipfs_cid=res.session.ipfs_cid,
        skill_package_fingerprint=res.skill_package_fingerprint,
        permission_reason=res.permission_reason,
        used_fallback=res.used_fallback,
    )


@skill_router.get("/sessions", response_model=_SessionsResponse)
def list_sessions(
    mine: bool = Query(False),
    mine_as_borrower: bool = Query(True),
    show_all: bool = Query(False),
    own_did: Optional[str] = Query(None),
) -> _SessionsResponse:
    od = _resolve_own_did(own_did)
    sessions: list[SkillBorrowSession] = []
    if mine_as_borrower:
        sessions += list_borrow_sessions(borrower_did=od, only_active=not show_all)
    if mine:
        sessions += list_borrow_sessions(owner_did=od, only_active=not show_all)
    seen = set()
    uniq: list[dict[str, Any]] = []
    for s in sessions:
        if s.session_id in seen:
            continue
        seen.add(s.session_id)
        uniq.append(s.to_dict())
    return _SessionsResponse(own_did=od, sessions=uniq)


@skill_router.post("/end-session", response_model=_EndSessionResponse)
def end_session(body: _EndSessionBody) -> _EndSessionResponse:
    try:
        s = end_skill_borrow_session(body.session_id, reason=body.reason)
    except SkillBorrowSessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SkillBorrowError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return _EndSessionResponse(
        session_id=s.session_id,
        status=s.status,
        destroy_reason=s.destroy_reason,
        destroyed_at=s.destroyed_at,
        ledger_entry_id=s.ledger_entry_id,
    )


@skill_router.post("/proxy-chat", response_model=_ProxyChatResponse)
def proxy_chat(body: _ProxyChatBody) -> _ProxyChatResponse:
    """borrower 用 skill 跑 LLM (borrower 自己 LLM key, 不消耗 owner quota).

    可选 use_mock_forwarder: True 时不真打 LLM API, 返 echo 模拟 (test / dev).
    """
    forwarder = None
    if body.use_mock_forwarder:
        def _mock(prompt: str, model: str, provider: str, api_key: Optional[str] = None, **kw):
            txt = f"[mock-forwarder echo] model={model} prompt_len={len(prompt)}"
            return txt, max(1, len(prompt) // 4), max(1, len(txt) // 4)
        forwarder = _mock

    try:
        result = proxy_skill_chat(
            session_id=body.session_id,
            prompt=body.prompt,
            model=body.model,
            provider=body.provider,
            llm_api_key=body.llm_api_key,
            forwarder=forwarder,
        )
    except SkillBorrowSessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SkillBorrowExpiredError as e:
        raise HTTPException(status_code=410, detail=str(e))  # 410 Gone (过期)
    except SkillBorrowError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return _ProxyChatResponse(**result)


__all__ = ["skill_router"]
