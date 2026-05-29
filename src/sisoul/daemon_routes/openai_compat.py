"""sisoul daemon · OpenAI-compatible borrow proxy endpoint (Wave T3 dev).

§28 §3.2 加密 proxy + Wave B' P0-1 borrow 路径 + Wave T3 OpenAI 兼容层.

# 问题

用户的 codex / pi (走 LiteLLM) 已习惯 OpenAI Chat Completions `/v1/chat/completions`
API. 想透明地走 Alice → Bob → 真 LLM (借 Bob 的 quota), **不改用户代码**.

# 方案

Alice daemon 跑本 router. 用户:

```bash
export OPENAI_BASE_URL=http://localhost:9876/v1
export OPENAI_API_KEY=sk-anything   # 本地用, 不上行
codex / aichat / openai-py SDK ...
```

请求落到 Alice daemon `/v1/chat/completions`, 本 router 内部:

1. 拼 messages 成单 prompt 字串 (system + user + assistant 历史).
2. 选 Bob friend (env `SISOUL_BORROW_FRIEND_DID` 或 didkey_friends.json 第一个).
3. GET Bob daemon `/sisoul/borrow/peer-pubkey` 拿 Bob 真 X25519 pubkey.
4. 复用全局 EncryptedProxy 加密 prompt 用 Bob pubkey.
5. POST Bob daemon `/sisoul/borrow/proxy-chat` (含 alice_pubkey, 加密 prompt, model).
6. 接 encrypted_response_b64 → 解密 → 转 OpenAI Chat Completions JSON.

# 隐私铁律 (跟 /sisoul/borrow/proxy-chat 同)

- prompt 字串绝不出现在 metadata / log / 错误返回.
- 解密后明文仅活在本 router handler 局部变量, 返完即 del.
- 错误 type/code 只暴露 class 名, 不带 prompt 字串.

# 限制 (v1)

- 不支持 stream=true (返 503 not_implemented).
- 不支持 function calling / tools (透传也可工作, Bob LLM 决定如何处理, 但 OpenAI
  function_call response 不会被 mock forwarder 回显).
- usage token 数走 Bob 真返的 ProxySessionMetadata.{prompt,response}_token_count.

# 环境变量

- ``SISOUL_BORROW_FRIEND_DID``: 指定 Bob did:key (默认走 didkey_friends.json 第一个).
- ``SISOUL_BORROW_PROVIDER``: anthropic / openai / gemini (默认 anthropic).
- ``SISOUL_BORROW_BOB_URL``: Bob daemon URL (默认 friend record 里的 last_seen_url,
  fallback http://localhost:9877 — WSL Bob 监听端口).
- ``SISOUL_OPENAI_COMPAT_MOCK``: "1" → 强制 use_mock_forwarder=True (e2e 测试用).
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from sisoul.friend.encrypted_proxy import (
    ProxyDecryptError,
    get_global_proxy,
)


# ── Router ────────────────────────────────────────────────────────────────────


openai_compat_router = APIRouter(prefix="/v1", tags=["openai-compat"])


# ── 配置 / 友邻查找 ───────────────────────────────────────────────────────────


DEFAULT_BOB_URL = "http://localhost:9877"
DEFAULT_MODELS = ("claude-opus-4-7", "claude-sonnet-4-6")
DEFAULT_FRIEND_INDEX = 0
_HTTP_TIMEOUT = 60.0  # Bob 端真打 LLM 可能慢


def _didkey_friends_path() -> Path:
    """复用 cli_commands/friend.py 同路径 (无 vault_dir override 时)."""
    return Path.home() / ".sisoul" / "identity" / "didkey_friends.json"


def _load_friends() -> list[dict[str, Any]]:
    fp = _didkey_friends_path()
    if not fp.exists():
        return []
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _pick_friend() -> Optional[dict[str, Any]]:
    """选 Bob friend: env SISOUL_BORROW_FRIEND_DID 优先, 否则 list[0]."""
    friends = _load_friends()
    if not friends:
        return None
    target = os.environ.get("SISOUL_BORROW_FRIEND_DID", "").strip()
    if target:
        for f in friends:
            if f.get("did") == target:
                return f
        return None
    return friends[0]


def _resolve_bob_url(friend: Optional[dict[str, Any]]) -> str:
    """优先 env, 次 friend record last_seen_url, 否则 DEFAULT_BOB_URL."""
    env_url = os.environ.get("SISOUL_BORROW_BOB_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")
    if friend:
        url = (friend.get("last_seen_url") or friend.get("daemon_url") or "").strip()
        if url:
            return url.rstrip("/")
    return DEFAULT_BOB_URL


def _resolve_provider() -> str:
    return os.environ.get("SISOUL_BORROW_PROVIDER", "anthropic").strip() or "anthropic"


def _allowed_models(friend: Optional[dict[str, Any]]) -> list[str]:
    """Bob 允许的 model. 从 friend record 'allowed_models' 取, 否则 DEFAULT_MODELS."""
    if friend:
        ms = friend.get("allowed_models")
        if isinstance(ms, list) and ms:
            return [str(m) for m in ms]
    return list(DEFAULT_MODELS)


# ── OpenAI Schemas (v1 spec subset) ───────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str = Field(..., description="system / user / assistant / tool")
    content: Any = Field(..., description="str 或 OpenAI multimodal list")
    name: Optional[str] = None


class ChatCompletionsRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float = Field(1.0, ge=0.0, le=2.0)
    max_tokens: int = Field(1024, ge=1, le=128000)
    stream: bool = Field(False)
    # 透传 (本 router 不解析, 不传 Bob — 隐私: 防 Bob 看 tool 描述含 prompt 上下文):
    top_p: Optional[float] = None
    n: Optional[int] = None
    stop: Optional[Any] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    user: Optional[str] = None


class ChatCompletionMessage(BaseModel):
    role: str
    content: str


class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatCompletionMessage
    finish_reason: str


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionsResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage


class ModelEntry(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str = "sisoul-borrow"


class ModelsListResponse(BaseModel):
    object: str = "list"
    data: list[ModelEntry]


# ── OpenAI error helper ───────────────────────────────────────────────────────


def _openai_error(
    status: int, message: str, etype: str, code: Optional[str] = None
) -> JSONResponse:
    """OpenAI 标准 error response 格式. 不带 prompt 字串."""
    body = {"error": {"message": message, "type": etype, "code": code}}
    return JSONResponse(status_code=status, content=body)


# ── messages → prompt 拼接 ────────────────────────────────────────────────────


def _stringify_content(content: Any) -> str:
    """OpenAI message.content 可能是 str 或 list[{type,text/image_url}]. 这里只取 text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    chunks.append(item["text"])
                elif "text" in item and isinstance(item["text"], str):
                    chunks.append(item["text"])
        return "\n".join(chunks)
    return str(content)


def _flatten_messages(messages: list[ChatMessage]) -> str:
    """拼 messages 成单 prompt 字串.

    用 ChatML-ish 简单 markdown 包裹 (system: ... \\n user: ... ).
    Bob daemon 收到后会原样作 prompt 喂 LLM. 真生产可改成 provider-specific
    格式 (Anthropic system 单字段 / OpenAI messages 透传), v1 简化.
    """
    parts: list[str] = []
    for m in messages:
        role = m.role or "user"
        text = _stringify_content(m.content)
        parts.append(f"<|{role}|>\n{text}")
    parts.append("<|assistant|>")
    return "\n\n".join(parts)


# ── 核心 endpoint: POST /v1/chat/completions ──────────────────────────────────


@openai_compat_router.post("/chat/completions")
async def chat_completions(body: ChatCompletionsRequest):
    """OpenAI Chat Completions 兼容入口.

    流程: 拼 prompt → 选 Bob → GET peer-pubkey → 加密 → POST borrow/proxy-chat
    → 解密 → 返 OpenAI ChatCompletion 格式.

    错误状态码 (OpenAI 风格):
      - 400: messages 空 / model 缺
      - 401: prompt 解密失败 / Bob 无 LLM key (lender_no_key)
      - 403: Bob 拒授权
      - 404: model 不在 Bob allowed_models
      - 409: Alice 端 EncryptedProxy 未 init (无 seed)
      - 412: 无 friend 可借
      - 503: Bob daemon 不可达 / stream=true 未实现
      - 502: Bob 内部错误
      - 500: 其它
    """
    # stream 暂不支持
    if body.stream:
        return _openai_error(
            503,
            "stream=true not implemented in sisoul borrow proxy v1",
            "not_implemented",
            "stream_unsupported",
        )

    if not body.messages:
        return _openai_error(
            400, "messages must be non-empty", "invalid_request_error", "messages_empty"
        )
    if not body.model:
        return _openai_error(
            400, "model is required", "invalid_request_error", "model_missing"
        )

    # 1. Alice 全局 proxy (拿 self_priv / self_pub 用来加密 + 解密 response)
    proxy = get_global_proxy()
    if proxy is None:
        return _openai_error(
            409,
            "Alice EncryptedProxy not initialized (no seed or daemon --skip-seed)",
            "service_unavailable",
            "proxy_uninitialized",
        )

    # 2. 选 Bob friend
    friend = _pick_friend()
    if friend is None:
        target = os.environ.get("SISOUL_BORROW_FRIEND_DID", "").strip()
        msg = (
            f"no friend matching SISOUL_BORROW_FRIEND_DID={target!r}"
            if target
            else "no friend in didkey_friends.json (add via `sisoul friend add did:key:...`)"
        )
        return _openai_error(412, msg, "precondition_failed", "no_friend")

    # 3. model 是否在 Bob allowed_models
    allowed = _allowed_models(friend)
    if body.model not in allowed:
        return _openai_error(
            404,
            f"model {body.model!r} not in lender allowed_models ({allowed})",
            "model_not_found",
            "model_not_allowed",
        )

    bob_url = _resolve_bob_url(friend)
    provider = _resolve_provider()
    use_mock = os.environ.get("SISOUL_OPENAI_COMPAT_MOCK", "").strip() == "1"

    # 4. GET Bob /sisoul/borrow/peer-pubkey
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        try:
            r = await client.get(f"{bob_url}/sisoul/borrow/peer-pubkey")
        except httpx.RequestError as e:
            return _openai_error(
                503,
                f"Bob daemon unreachable at {bob_url}: {type(e).__name__}",
                "service_unavailable",
                "bob_unreachable",
            )
        if r.status_code != 200:
            return _openai_error(
                503,
                f"Bob /borrow/peer-pubkey {r.status_code}",
                "service_unavailable",
                "bob_peer_pubkey_failed",
            )
        try:
            peer_meta = r.json()
            bob_pubkey_hex = peer_meta["pubkey_hex"]
            bob_pubkey = bytes.fromhex(bob_pubkey_hex)
            if len(bob_pubkey) != 32:
                raise ValueError("pubkey != 32B")
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return _openai_error(
                502,
                f"Bob peer-pubkey malformed: {type(e).__name__}",
                "service_unavailable",
                "bob_peer_pubkey_malformed",
            )

        # 5. 拼 prompt + 加密
        prompt_text = _flatten_messages(body.messages)
        try:
            encrypted_prompt = proxy.encrypt_for(bob_pubkey, prompt_text)
        except Exception as e:  # noqa: BLE001
            # 不带 prompt 字串
            return _openai_error(
                500,
                f"encrypt failed: {type(e).__name__}",
                "internal_error",
                "encrypt_failed",
            )
        # 局部 del (best-effort, str immutable)
        del prompt_text

        encrypted_prompt_b64 = base64.b64encode(encrypted_prompt).decode("ascii")
        alice_pub_hex = bytes(proxy.self_pub).hex()

        # 6. POST Bob /sisoul/borrow/proxy-chat
        req_body = {
            "borrower_did": proxy.self_did,
            "borrower_pubkey_hex": alice_pub_hex,
            "encrypted_prompt_b64": encrypted_prompt_b64,
            "target_model": body.model,
            "provider": provider,
            "max_tokens": body.max_tokens,
            "temperature": body.temperature,
            "use_mock_forwarder": use_mock,
        }
        try:
            r2 = await client.post(
                f"{bob_url}/sisoul/borrow/proxy-chat", json=req_body
            )
        except httpx.RequestError as e:
            return _openai_error(
                503,
                f"Bob /borrow/proxy-chat unreachable: {type(e).__name__}",
                "service_unavailable",
                "bob_unreachable",
            )

        # 7. error status code 映射 OpenAI 错误
        if r2.status_code != 200:
            detail = _safe_detail(r2)
            if r2.status_code == 401:
                code = "lender_no_key" if "lender_no_key" in detail else "decrypt_failed"
                return _openai_error(401, detail, "authentication_error", code)
            if r2.status_code == 403:
                return _openai_error(403, detail, "permission_denied", "lender_denied")
            if r2.status_code == 409:
                return _openai_error(
                    503, detail, "service_unavailable", "bob_proxy_not_started"
                )
            if r2.status_code == 400:
                return _openai_error(
                    400, detail, "invalid_request_error", "bob_rejected"
                )
            if r2.status_code in (502, 500):
                return _openai_error(
                    502, detail, "upstream_error", "bob_forwarder_failed"
                )
            return _openai_error(
                502, detail, "upstream_error", f"bob_status_{r2.status_code}"
            )

        try:
            payload = r2.json()
            encrypted_response_b64 = payload["encrypted_response_b64"]
            metadata = payload.get("metadata", {}) or {}
            encrypted_response = base64.b64decode(encrypted_response_b64, validate=True)
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            return _openai_error(
                502,
                f"Bob response malformed: {type(e).__name__}",
                "upstream_error",
                "bob_response_malformed",
            )

    # 8. 解密 response
    try:
        plaintext_bytes = proxy.decrypt_from(bob_pubkey, encrypted_response)
        response_text = plaintext_bytes.decode("utf-8")
    except ProxyDecryptError:
        return _openai_error(
            500,
            "response decrypt failed (MAC/key mismatch)",
            "internal_error",
            "decrypt_failed",
        )
    except UnicodeDecodeError:
        return _openai_error(
            500,
            "response not utf-8",
            "internal_error",
            "decode_failed",
        )

    # 9. 转 OpenAI ChatCompletion
    prompt_tokens = int(metadata.get("prompt_token_count", 0) or 0)
    completion_tokens = int(metadata.get("response_token_count", 0) or 0)
    finish_reason = "stop"
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    out = ChatCompletionsResponse(
        id=completion_id,
        created=int(time.time()),
        model=body.model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatCompletionMessage(role="assistant", content=response_text),
                finish_reason=finish_reason,
            )
        ],
        usage=ChatCompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )
    # best-effort 清明文 (str immutable 限制)
    del response_text
    return out


def _safe_detail(resp: httpx.Response) -> str:
    """从 Bob HTTP error body 提取 detail 字串. 防 prompt 泄漏.

    Bob detail 可能是 str 或 {"error": "lender_no_key", "reason": "..."}.
    本函数只回原 detail 给上游 OpenAI client (Bob 那端已保证不带 prompt 字串).
    """
    try:
        j = resp.json()
        if isinstance(j, dict):
            d = j.get("detail", j)
            if isinstance(d, dict):
                return json.dumps(d, ensure_ascii=False)
            return str(d)
        return str(j)
    except Exception:  # noqa: BLE001
        return f"HTTP {resp.status_code}"


# ── GET /v1/models ────────────────────────────────────────────────────────────


@openai_compat_router.get("/models", response_model=ModelsListResponse)
async def list_models() -> ModelsListResponse:
    """返 Bob 允许的 model 列表.

    源: friend record `allowed_models` 字段, 否则 DEFAULT_MODELS.
    """
    friend = _pick_friend()
    allowed = _allowed_models(friend)
    now = int(time.time())
    return ModelsListResponse(
        data=[ModelEntry(id=m, created=now) for m in allowed]
    )


__all__ = ["openai_compat_router"]
