"""sisoul daemon · 加密 proxy HTTP API (Phase 4 W54-W58 · 波 5 dev-B).

§28 §3.2 加密 proxy daemon endpoints.

Endpoints:
- POST /sisoul/proxy/forward       — Alice→Bob: 提交 encrypted_prompt, 返加密 response + metadata
- GET  /sisoul/proxy/sessions      — 列活动 forward session (metadata only, 绝不含 prompt)
- POST /sisoul/proxy/end-session   — 结束 session

⚠️ 强制命名规范: 模块级变量 ``proxy_router`` (主集成用
``from sisoul.daemon_routes.proxy import proxy_router; app.include_router(proxy_router)``).
"""

from __future__ import annotations

import base64
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from sisoul.friend.encrypted_proxy import (
    ProxyDecryptError,
    ProxyError,
    ProxyPermissionError,
    get_global_proxy,
    proxy_chat_request_async,
)

# 主集成强制命名: proxy_router
proxy_router = APIRouter(prefix="/sisoul/proxy", tags=["proxy"])


# ── schemas ───────────────────────────────────────────────────────────────────


class ForwardRequest(BaseModel):
    """POST /sisoul/proxy/forward 请求体."""

    borrower_did: str = Field(..., description="Alice DID, 例 'alice.sisoul.eth'")
    borrower_pubkey_hex: str = Field(
        ..., description="Alice Curve25519 32B pubkey, hex 编码 (64 字符)"
    )
    encrypted_prompt_b64: str = Field(
        ..., description="Alice 用 Bob pubkey 加密的 prompt, base64 编码"
    )
    target_model: str = Field(..., description="例 'claude-opus-4-7'")
    provider: str = Field("anthropic", description="LLM provider (anthropic/openai/...)")
    max_tokens: int = Field(1024, ge=1, le=128000)
    temperature: float = Field(1.0, ge=0.0, le=2.0)


class ForwardResponse(BaseModel):
    encrypted_response_b64: str
    metadata: dict


class SessionItem(BaseModel):
    session_id: str
    borrower_did: str
    lender_did: str
    target_model: str
    provider: str
    started_ts: float
    ended_ts: Optional[float] = None
    prompt_token_count: int = 0
    response_token_count: int = 0
    status: str
    error_class: Optional[str] = None


class SessionsResponse(BaseModel):
    running: bool
    self_did: Optional[str] = None
    pubkey_hex: Optional[str] = None
    sessions: list[SessionItem]


class EndSessionRequest(BaseModel):
    session_id: str


class EndSessionResponse(BaseModel):
    ok: bool
    metadata: Optional[SessionItem] = None
    message: str = ""


# ── routes ────────────────────────────────────────────────────────────────────


@proxy_router.post("/forward", response_model=ForwardResponse, status_code=200)
async def post_forward(body: ForwardRequest) -> ForwardResponse:
    """Bob daemon 接 Alice 加密 prompt → 解密 → 调 LLM → 返加密 response."""
    proxy = get_global_proxy()
    if proxy is None:
        raise HTTPException(
            status_code=409,
            detail="proxy 未启动 (sisoul proxy start 或 daemon 启动时未注册)",
        )

    # 解码 pubkey / encrypted_prompt
    try:
        borrower_pubkey = bytes.fromhex(body.borrower_pubkey_hex)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"borrower_pubkey_hex 不合法: {e}")
    if len(borrower_pubkey) != 32:
        raise HTTPException(
            status_code=400,
            detail=f"borrower_pubkey 必须 32B, 实际 {len(borrower_pubkey)}B",
        )

    try:
        encrypted_prompt = base64.b64decode(body.encrypted_prompt_b64, validate=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"encrypted_prompt_b64 不合法: {e}")

    # 走 proxy
    try:
        encrypted_response, metadata = await proxy_chat_request_async(
            proxy=proxy,
            borrower_did=body.borrower_did,
            borrower_pubkey=borrower_pubkey,
            encrypted_prompt=encrypted_prompt,
            target_model=body.target_model,
            provider=body.provider,
            max_tokens=body.max_tokens,
            temperature=body.temperature,
        )
    except ProxyPermissionError as e:
        raise HTTPException(status_code=403, detail=f"授权拒绝: {e}")
    except ProxyDecryptError as e:
        # 不回显 e 详情避免 oracle attack (统一返 401-equivalent)
        raise HTTPException(status_code=401, detail="prompt 解密失败 (MAC/pubkey 错)")
    except ProxyError as e:
        # 统一错误类名 (不含 prompt 内容)
        raise HTTPException(status_code=502, detail=f"forwarder 失败: {type(e).__name__}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"proxy 内部错误: {type(e).__name__}"
        )

    return ForwardResponse(
        encrypted_response_b64=base64.b64encode(encrypted_response).decode("ascii"),
        metadata=metadata.to_safe_dict(),
    )


@proxy_router.get("/sessions", response_model=SessionsResponse)
async def get_sessions() -> SessionsResponse:
    """列活动 forward session metadata. 绝不含 prompt 内容."""
    proxy = get_global_proxy()
    if proxy is None:
        return SessionsResponse(running=False, sessions=[])

    items = [SessionItem(**s.to_safe_dict()) for s in proxy.list_sessions()]
    return SessionsResponse(
        running=True,
        self_did=proxy.self_did,
        pubkey_hex=proxy.self_pub.encode().hex(),
        sessions=items,
    )


@proxy_router.post("/end-session", response_model=EndSessionResponse)
async def post_end_session(body: EndSessionRequest) -> EndSessionResponse:
    """主动结束某 session. 幂等 (不存在返 ok=True message 提示)."""
    proxy = get_global_proxy()
    if proxy is None:
        raise HTTPException(status_code=409, detail="proxy 未启动")

    meta = proxy.end_session(body.session_id)
    if meta is None:
        return EndSessionResponse(
            ok=True, metadata=None, message=f"session {body.session_id} 不存在 (可能已结束)"
        )
    return EndSessionResponse(
        ok=True,
        metadata=SessionItem(**meta.to_safe_dict()),
        message="session ended",
    )


__all__ = ["proxy_router"]
