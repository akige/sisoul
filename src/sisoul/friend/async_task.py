"""Async task delivery for borrow — M5 异步任务投递 (2026-06-10).

借入方和借出方**不必同时在线**也能完成 borrow:

```
borrower (睡前)                 GossipSub                lender (早上上线)
    │ submit_task ×100              │                         │
    │ Box(prompt) → task topic ────▶│  (留存/replay)          │
    │ 下线                          │ ───────────────────────▶│ async_serve_loop
    │                               │   解密 → LLM → Box(结果) │
    │ (早上) collect_results ◀──────│◀──────────────────────  │
    │ 解密结果                       │   result topic          │
```

Topics
------
- ``/sisoul/async-task/v1/<hash(lender_did)>``     任务投递 (borrower → lender)
- ``/sisoul/async-result/v1/<hash(borrower_did)>`` 结果回投 (lender → borrower)

加密与 ``proxy_p2p`` 同款: ``Box(my_priv, peer_did_pubkey)`` — did:key 本身就是
X25519 pubkey (multicodec 0xec01), 私钥从 vault BIP-39 seed 派生
(``load_vault_keypair``), 双方无需额外 key exchange.

离线语义靠 transport 自身的 buffering: ``MemoryTransport`` 对 late subscriber
replay history deque; 真网 (Kubo GossipSub) 由 lender 上线后从 topic 收留存 /
重投消息. 本模块只保证 "先投递后订阅也能取到" 的协议层逻辑.

隐私铁律 (同 encrypted_proxy):
- prompt / result 明文绝不落盘、绝不 log — wire 上只有 Box 密文
- 任务封包 (``AsyncTask``) 只携带 ``encrypted_prompt_b64``, 无明文字段
"""
from __future__ import annotations

import asyncio
import base64
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from nacl.public import Box, PublicKey

from sisoul.identity.did_key import did_key_to_pubkey
from sisoul.friend.proxy_p2p import (
    ProxyP2PError,
    _is_friend,
    _lend_approved_locally,
    _topic_hash,
    load_vault_keypair,
)


ASYNC_TASK_TOPIC_PREFIX = "/sisoul/async-task/v1/"
ASYNC_RESULT_TOPIC_PREFIX = "/sisoul/async-result/v1/"

DEFAULT_COLLECT_TIMEOUT = 10.0

# wire envelope kinds
_KIND_TASK = "async-task"
_KIND_RESULT = "async-result"


def async_task_topic_for(lender_did: str) -> str:
    """任务投递 topic — lender 订阅自己的, borrower 往这里 publish."""
    return ASYNC_TASK_TOPIC_PREFIX + _topic_hash(lender_did)


def async_result_topic_for(borrower_did: str) -> str:
    """结果回投 topic — borrower 订阅自己的, lender 往这里 publish."""
    return ASYNC_RESULT_TOPIC_PREFIX + _topic_hash(borrower_did)


# ── 任务封包 ──────────────────────────────────────────────────────────────────


@dataclass
class AsyncTask:
    """异步 borrow 任务封包 (wire 格式, 只含密文 + metadata, 无明文 prompt)."""

    task_id: str
    borrower_did: str
    lender_did: str
    model: str
    provider: str
    encrypted_prompt_b64: str  # Box(borrower_priv, lender_pub) 密文, base64
    mode: str = "strong-tie-auto"
    created_ts: int = 0
    status: str = "queued"  # queued | done | failed
    lend_request_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "borrower_did": self.borrower_did,
            "lender_did": self.lender_did,
            "model": self.model,
            "provider": self.provider,
            "encrypted_prompt_b64": self.encrypted_prompt_b64,
            "mode": self.mode,
            "created_ts": self.created_ts,
            "status": self.status,
            "lend_request_id": self.lend_request_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AsyncTask":
        return cls(
            task_id=str(d["task_id"]),
            borrower_did=str(d["borrower_did"]),
            lender_did=str(d["lender_did"]),
            model=str(d.get("model", "")),
            provider=str(d.get("provider", "openai")),
            encrypted_prompt_b64=str(d.get("encrypted_prompt_b64", "")),
            mode=str(d.get("mode", "strong-tie-auto")),
            created_ts=int(d.get("created_ts", 0) or 0),
            status=str(d.get("status", "queued")),
            lend_request_id=d.get("lend_request_id"),
        )


# ── 借入方: 投递 (fire-and-forget, 不等结果) ──────────────────────────────────


async def submit_task(
    transport: Any,
    borrower_did: str,
    lender_did: str,
    model: str,
    prompt: str,
    *,
    provider: str = "openai",
    mode: str = "strong-tie-auto",
    lend_request_id: Optional[str] = None,
    vault_dir: Optional[Path] = None,
    created_ts: Optional[int] = None,
) -> str:
    """Box 加密 prompt → publish 到 lender 的 task topic, 立即返回 task_id.

    不等结果 — lender 可以离线, 上线后由 ``async_serve_loop`` 消费.
    结果由 ``collect_results`` 在 borrower 下次上线时收割.

    Args:
        created_ts: 任务时间戳 (秒). 测试注入用; None = 当前 wall clock.

    Raises:
        ProxyP2PError: vault 不匹配 / seed 缺失 / transport publish 失败.
    """
    from sisoul.chat.transport import WireEnvelope

    my_did, my_priv, _my_pub = load_vault_keypair(vault_dir)
    if my_did != borrower_did:
        # vault mismatch 是硬 bug, 不是 fallback 场景 (同 borrower_roundtrip)
        raise ProxyP2PError(
            f"vault did ({my_did[:24]}…) != borrower_did ({borrower_did[:24]}…)"
        )

    lender_pub = PublicKey(did_key_to_pubkey(lender_did))
    box = Box(my_priv, lender_pub)
    sealed = base64.b64encode(bytes(box.encrypt(prompt.encode("utf-8")))).decode()

    task = AsyncTask(
        task_id="atk_" + uuid.uuid4().hex[:16],
        borrower_did=borrower_did,
        lender_did=lender_did,
        model=model,
        provider=provider,
        encrypted_prompt_b64=sealed,
        mode=mode,
        created_ts=int(created_ts if created_ts is not None else time.time()),
        status="queued",
        lend_request_id=lend_request_id,
    )

    wire = WireEnvelope(kind=_KIND_TASK, body=task.to_dict())
    try:
        await transport.publish(async_task_topic_for(lender_did), wire.to_bytes())
    except Exception as e:  # noqa: BLE001 — typed error, 不泄 prompt
        raise ProxyP2PError(f"task publish 失败: {type(e).__name__}: {e}") from e
    return task.task_id


# ── 借入方: 收结果 ────────────────────────────────────────────────────────────


async def collect_results(
    transport: Any,
    borrower_did: str,
    *,
    timeout: float = DEFAULT_COLLECT_TIMEOUT,
    vault_dir: Optional[Path] = None,
    max_results: Optional[int] = None,
) -> list[dict[str, Any]]:
    """订阅自己的结果 topic, Box 解密, 返回 ``[{task_id, text, status, ...}]``.

    超时返回已收到的 (可能为空 list, 不抛). ``max_results`` 收满即提前返回
    (调用方知道自己投了几个任务时省等待).

    单条解密失败不崩 — 该条标 ``status="failed"`` + ``error_class`` 继续收.
    """
    my_did, my_priv, _my_pub = load_vault_keypair(vault_dir)
    if my_did != borrower_did:
        raise ProxyP2PError(
            f"vault did ({my_did[:24]}…) != borrower_did ({borrower_did[:24]}…)"
        )

    from sisoul.chat.transport import WireEnvelope

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    gen = await transport.subscribe(async_result_topic_for(borrower_did))
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if max_results is not None and len(results) >= max_results:
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(gen.__anext__(), timeout=remaining)
            except (asyncio.TimeoutError, StopAsyncIteration):
                break
            try:
                wire = WireEnvelope.from_bytes(raw)
            except Exception:  # noqa: BLE001 — 脏消息直接跳过
                continue
            if wire.kind != _KIND_RESULT:
                continue
            body = wire.body or {}
            if body.get("borrower_did") != borrower_did:
                continue
            task_id = str(body.get("task_id", ""))
            if not task_id or task_id in seen:
                continue  # 去重 (history replay 可能重复)
            seen.add(task_id)

            entry: dict[str, Any] = {
                "task_id": task_id,
                "lender_did": body.get("lender_did"),
                "status": str(body.get("status", "")),
                "text": None,
            }
            for k in ("reason", "error_class", "model_used",
                      "prompt_tokens", "response_tokens"):
                if k in body:
                    entry[k] = body[k]

            if entry["status"] == "done":
                # 解密结果: Box(my_priv, lender_pub) — 跟投递方向对称
                try:
                    lender_pub = PublicKey(did_key_to_pubkey(str(body["lender_did"])))
                    box = Box(my_priv, lender_pub)
                    sealed = base64.b64decode(str(body["encrypted_result_b64"]))
                    entry["text"] = box.decrypt(sealed).decode("utf-8")
                except Exception as e:  # noqa: BLE001 — 单条坏不崩整批
                    entry["status"] = "failed"
                    entry["error_class"] = type(e).__name__
            results.append(entry)
    finally:
        try:
            await gen.aclose()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    return results


# ── 借出方: serve loop (daemon startup task) ──────────────────────────────────


async def async_serve_loop(
    transport: Any,
    my_did: str,
    *,
    vault_dir: Optional[Path] = None,
    forwarder: Any = None,
    llm_api_key: Optional[str] = None,
) -> None:
    """订阅自己的 task topic, 逐任务: 解密 → LLM → 加密结果 → 回投.

    lender 离线期间投递的任务留在 transport (MemoryTransport history /
    GossipSub 留存) — 上线起 loop 后照样取到, 这正是 M5 的核心语义.

    serve gate 复用 proxy_p2p: 先 ``_is_friend`` (陌生人不服务, 防配额滥用);
    ``per-request`` 模式再查本机 LendStore 审批 (``_lend_approved_locally``).

    Args:
        forwarder: LLM 转发 hook (测试注入 mock; None = EncryptedProxy 默认,
            默认 forwarder 未设 SISOUL_DEFAULT_FORWARDER_REAL=1 会拒真调).
    """
    from sisoul.chat.transport import WireEnvelope
    from sisoul.friend.encrypted_proxy import EncryptedProxy

    try:
        vault_did, my_priv, my_pub = load_vault_keypair(vault_dir)
    except ProxyP2PError as e:
        print(f"[daemon] async task serve loop skipped: {e}", file=sys.stderr)
        return
    if vault_did != my_did:
        print(
            f"[daemon] async task serve loop skipped: vault did != {my_did[:24]}…",
            file=sys.stderr,
        )
        return

    proxy = EncryptedProxy(
        self_priv=my_priv,
        self_pub=my_pub,
        self_did=my_did,
        llm_api_key=llm_api_key,
        forwarder=forwarder,
    )
    topic = async_task_topic_for(my_did)
    seen: set[str] = set()  # 去重 (history replay / resubscribe 后不重跑)
    pending: set[asyncio.Task] = set()

    while True:
        try:
            gen = await transport.subscribe(topic)
            print(f"[daemon] async task serve loop subscribed {topic}", file=sys.stderr)
            async for raw in gen:
                try:
                    wire = WireEnvelope.from_bytes(raw)
                except Exception:  # noqa: BLE001
                    continue
                if wire.kind != _KIND_TASK:
                    continue
                body = wire.body or {}
                if body.get("lender_did") != my_did:
                    continue
                task_id = str(body.get("task_id", ""))
                if not task_id or task_id in seen:
                    continue
                seen.add(task_id)
                t = asyncio.get_running_loop().create_task(
                    _serve_one_task(transport, proxy, body)
                )
                pending.add(t)
                t.add_done_callback(pending.discard)
        except asyncio.CancelledError:
            raise  # daemon 关停 — 不吞
        except Exception as e:  # noqa: BLE001 — transport 抖动, 退避重订
            print(
                f"[daemon] async task serve loop crashed: {type(e).__name__}: {e}; "
                "retry in 10s",
                file=sys.stderr,
            )
            await asyncio.sleep(10)


async def _serve_one_task(transport: Any, proxy: Any, body: dict) -> None:
    """处理单个任务: gate → 解密+LLM (EncryptedProxy 内存内) → 回投结果."""
    try:
        task = AsyncTask.from_dict(body)
    except Exception:  # noqa: BLE001 — 缺字段的脏封包直接丢
        return

    resp: dict[str, Any] = {
        "task_id": task.task_id,
        "borrower_did": task.borrower_did,
        "lender_did": proxy.self_did,
    }

    # serve gate (同 proxy_p2p._serve_one):
    # - 两种模式都先验好友 — 陌生人不服务 (防配额滥用)
    # - per-request → 本机 LendStore 审批复核
    if not _is_friend(task.borrower_did):
        resp.update(status="denied", reason="borrower is not in lender's friend list")
        await _publish_result(transport, task.borrower_did, resp)
        return
    if task.mode == "per-request":
        if not _lend_approved_locally(task.borrower_did, str(task.lend_request_id or "")):
            resp.update(status="denied", reason="lend request not approved by lender")
            await _publish_result(transport, task.borrower_did, resp)
            return
    elif task.mode != "strong-tie-auto":
        resp.update(
            status="denied",
            reason=f"mode {task.mode!r} not served (use strong-tie-auto or per-request)",
        )
        await _publish_result(transport, task.borrower_did, resp)
        return

    try:
        borrower_pub = did_key_to_pubkey(task.borrower_did)
        sealed_prompt = base64.b64decode(task.encrypted_prompt_b64)
        from sisoul.friend.encrypted_proxy import proxy_chat_request_async

        # 解密+LLM+加密都在 EncryptedProxy 内 (prompt 仅内存, 不 log/写盘)
        encrypted_result, meta = await proxy_chat_request_async(
            proxy,
            borrower_did=task.borrower_did,
            borrower_pubkey=borrower_pub,
            encrypted_prompt=sealed_prompt,
            target_model=task.model,
            provider=task.provider,
        )
        resp.update(
            status="done",
            encrypted_result_b64=base64.b64encode(encrypted_result).decode(),
            prompt_tokens=meta.prompt_token_count,
            response_tokens=meta.response_token_count,
            model_used=meta.target_model,
        )
    except Exception as e:  # noqa: BLE001 — 绝不让 prompt 经异常链泄漏
        resp.update(status="failed", error_class=type(e).__name__)
    await _publish_result(transport, task.borrower_did, resp)


async def _publish_result(transport: Any, borrower_did: str, resp: dict) -> None:
    from sisoul.chat.transport import WireEnvelope

    try:
        wire = WireEnvelope(kind=_KIND_RESULT, body=resp)
        await transport.publish(async_result_topic_for(borrower_did), wire.to_bytes())
    except Exception as e:  # noqa: BLE001
        print(
            f"[daemon] async result publish failed: {type(e).__name__}: {e}",
            file=sys.stderr,
        )


__all__ = [
    "AsyncTask",
    "async_task_topic_for",
    "async_result_topic_for",
    "submit_task",
    "collect_results",
    "async_serve_loop",
    "DEFAULT_COLLECT_TIMEOUT",
]
