"""sisoul daemon · 加密 proxy HTTP API (Phase 4 W54-W58 · 波 5 dev-B · Wave B' P0-1).

§28 §3.2 加密 proxy daemon endpoints.

Endpoints (波 5):
- POST /sisoul/proxy/forward       — Alice→Bob: 提交 encrypted_prompt, 返加密 response + metadata
- GET  /sisoul/proxy/sessions      — 列活动 forward session (metadata only, 绝不含 prompt)
- POST /sisoul/proxy/end-session   — 结束 session

Endpoints (Wave B' P0-1 新增):
- POST /sisoul/borrow/proxy-chat   — borrow 语义版 proxy 入口, lender_no_key 反向 case

⚠️ 强制命名规范:
- ``proxy_router`` — 主集成 ``app.include_router(proxy_router)``
- ``borrow_proxy_router`` — Wave B' P0-1 borrow 路径
"""

from __future__ import annotations

import base64
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from sisoul.friend.encrypted_proxy import (
    ForwarderNotInjectedError,
    ProxyDecryptError,
    ProxyError,
    ProxyPermissionError,
    get_global_proxy,
    proxy_chat_request_async,
)

# 主集成强制命名: proxy_router
proxy_router = APIRouter(prefix="/sisoul/proxy", tags=["proxy"])

# Wave B' P0-1: borrow-side proxy-chat endpoint (Alice → Bob daemon).
borrow_proxy_router = APIRouter(prefix="/sisoul/borrow", tags=["borrow"])


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
        raise HTTPException(status_code=401, detail="prompt 解密失败 (MAC/pubkey 错)")
    except ProxyError as e:
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
    """主动结束某 session. 幂等."""
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


# ── Wave B' P0-1: POST /sisoul/borrow/proxy-chat ──────────────────────────────


class ProxyChatRequest(BaseModel):
    """Wave B' P0-1: Alice 借用 Bob LLM quota 的加密 chat 请求体."""

    borrower_did: str = Field(
        ..., description="Alice DID (did:sisoul:* 或 did:key:z...)"
    )
    borrower_pubkey_hex: str = Field(
        ..., description="Alice 32B Curve25519 pubkey hex"
    )
    encrypted_prompt_b64: str = Field(
        ..., description="Alice 用 Bob pubkey 加密的 prompt (base64)"
    )
    target_model: str = Field(..., description="例 'claude-opus-4-7'")
    provider: str = Field(
        "anthropic",
        description=(
            "LLM provider (anthropic / openai / gemini / grok / deepseek / "
            "ollama / openrouter)"
        ),
    )
    max_tokens: int = Field(1024, ge=1, le=128000)
    temperature: float = Field(1.0, ge=0.0, le=2.0)


class ProxyChatResponse(BaseModel):
    encrypted_response_b64: str
    metadata: dict


@borrow_proxy_router.post(
    "/proxy-chat", response_model=ProxyChatResponse, status_code=200
)
async def post_borrow_proxy_chat(body: ProxyChatRequest) -> ProxyChatResponse:
    """Wave B' P0-1: Bob daemon 接 Alice borrow 请求 → 真打 LLM → 返加密 response.

    跟 /sisoul/proxy/forward 区别:
      - /forward 是通用 proxy entrypoint, 主要给同机 / pytest 用.
      - /borrow/proxy-chat 是 borrow 流程语义: Alice → Bob (lender) daemon, 真 LLM API key.

    反向 case (lender_no_key):
      Bob daemon 未配 LLM api_key (forwarder LLMAdapterError) → 返 401 + body 含
      ``error: lender_no_key``. Alice ledger 收到后标 ``lender_no_key``.

    隐私铁律 (跟 /forward 同):
      - encrypted_prompt / response 全程加密
      - metadata 走白名单 (无 prompt 字串)
      - 任何 error_class 字段不含 prompt 内容
    """
    proxy = get_global_proxy()
    if proxy is None:
        raise HTTPException(
            status_code=409,
            detail="proxy 未启动 (Bob daemon 还未 `sisoul proxy start` 或 daemon 未注册)",
        )

    try:
        borrower_pubkey = bytes.fromhex(body.borrower_pubkey_hex)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail=f"borrower_pubkey_hex 不合法: {e}"
        )
    if len(borrower_pubkey) != 32:
        raise HTTPException(
            status_code=400,
            detail=f"borrower_pubkey 必须 32B, 实际 {len(borrower_pubkey)}B",
        )

    try:
        encrypted_prompt = base64.b64decode(body.encrypted_prompt_b64, validate=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail=f"encrypted_prompt_b64 不合法: {e}"
        )

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
    except ProxyDecryptError:
        raise HTTPException(
            status_code=401, detail="prompt 解密失败 (MAC/pubkey 错)"
        )
    except ForwarderNotInjectedError:
        # SISOUL_DEFAULT_FORWARDER_REAL!=1 且未注入 forwarder → 视同 no_key
        raise HTTPException(
            status_code=401,
            detail={
                "error": "lender_no_key",
                "reason": "Bob daemon forwarder 未启用 (SISOUL_DEFAULT_FORWARDER_REAL!=1)",
            },
        )
    except ProxyError as e:
        # forwarder 失败 (含 LLMAdapterError). proxy_chat_request 故意
        # `raise ProxyError("forwarder 调用失败 (LLMAdapterError)") from None`
        # 防 prompt 字串通过 e.__cause__.args 链泄漏. 故只看 str(e) class 名.
        msg = str(e)
        if "LLMAdapterError" in msg:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "lender_no_key",
                    "reason": "Bob daemon 未配 LLM api key (或 key 无效)",
                },
            )
        raise HTTPException(
            status_code=502, detail=f"forwarder 失败: {type(e).__name__}"
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"proxy 内部错误: {type(e).__name__}"
        )

    return ProxyChatResponse(
        encrypted_response_b64=base64.b64encode(encrypted_response).decode("ascii"),
        metadata=metadata.to_safe_dict(),
    )


__all__ = ["proxy_router", "borrow_proxy_router"]
