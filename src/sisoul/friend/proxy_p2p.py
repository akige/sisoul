"""Encrypted LLM-proxy round-trip over GossipSub (P0 · 2026-06-10).

Closes the last stub in the borrow path: instead of returning
"stub-passthrough", the borrower's daemon now ships the prompt — sealed with
libsodium Box (X25519 did:key material) — to the lender's daemon over
GossipSub. The lender decrypts in memory, calls its own LLM endpoint via
``EncryptedProxy`` (honouring ``OPENAI_API_BASE`` / ``OPENAI_API_KEY``), and
publishes the Box-sealed response back.

Topics
------
- ``/sisoul/proxy/v1/<hash(lender_did)>``        inbound proxy requests
- ``/sisoul/proxy-resp/v1/<hash(borrower_did)>`` sealed responses back

Key material: both sides' did:key **is** an X25519 public key (multicodec
0xec01, see identity.did_key), so ``Box(my_priv, peer_did_pubkey)`` works with
no extra key exchange. Private keys derive from each vault's BIP-39 seed via
``derive_did_key_keypair(master, index=0)`` — the same derivation that
produced the did:key itself.

Alpha gate (P1 will replace with real per-request approval): the lender only
serves requests whose mode is ``strong-tie-auto``; everything else gets a
``denied`` response envelope.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from nacl.public import Box, PrivateKey, PublicKey

from sisoul.identity.did_key import did_key_to_pubkey


PROXY_REQ_TOPIC_PREFIX = "/sisoul/proxy/v1/"
PROXY_RESP_TOPIC_PREFIX = "/sisoul/proxy-resp/v1/"

DEFAULT_ROUNDTRIP_TIMEOUT = 15.0


class ProxyP2PError(Exception):
    """Round-trip level failure (transport down / timeout / decrypt)."""


class ProxyP2PTimeout(ProxyP2PError):
    """Lender did not answer within the timeout — caller may fall back."""


def _topic_hash(did: str) -> str:
    return hashlib.sha256(did.encode()).hexdigest()[:16]


def proxy_req_topic_for(lender_did: str) -> str:
    return PROXY_REQ_TOPIC_PREFIX + _topic_hash(lender_did)


def proxy_resp_topic_for(borrower_did: str) -> str:
    return PROXY_RESP_TOPIC_PREFIX + _topic_hash(borrower_did)


# ── vault keypair (works for either side) ───────────────────────────────────


def load_vault_keypair(vault_dir: Optional[Path] = None) -> tuple[str, PrivateKey, PublicKey]:
    """Load (did, priv, pub) from the vault's BIP-39 seed.

    Same derivation as ``sisoul did show`` so the priv matches the did:key
    the peer encrypts to.
    """
    from sisoul.identity import (  # lazy: heavy imports
        load_mnemonic_from_file,
        mnemonic_to_master_key,
        generate_did_key_from_master,
    )

    vault = Path(
        vault_dir
        or os.environ.get("SISOUL_VAULT", str(Path.home() / ".sisoul"))
    ).expanduser()
    seed_path = vault / "seed.txt"
    if not seed_path.exists():
        raise ProxyP2PError(f"vault seed 不存在: {seed_path}")
    mnemonic = load_mnemonic_from_file(seed_path)
    master = mnemonic_to_master_key(mnemonic)
    did, priv, pub = generate_did_key_from_master(master, index=0)
    return did, priv, pub


# ── borrower side: sync round-trip ──────────────────────────────────────────


def borrower_roundtrip(
    borrower_did: str,
    lender_did: str,
    model: str,
    prompt: str,
    *,
    provider: str = "openai",
    mode: str = "strong-tie-auto",
    timeout: float = DEFAULT_ROUNDTRIP_TIMEOUT,
    kubo_api: str = "http://127.0.0.1:5001",
) -> dict[str, Any]:
    """Seal prompt → publish → wait for sealed response → return plaintext.

    Returns ``{"text": str, "prompt_tokens": int, "response_tokens": int,
    "model_used": str, "request_id": str}``.

    Raises ProxyP2PTimeout / ProxyP2PError. Sync entry — runs its own event
    loop, so call from a thread with no running loop (FastAPI sync routes /
    CLI both qualify).
    """
    my_did, my_priv, _my_pub = load_vault_keypair()
    if my_did != borrower_did:
        # vault mismatch is a hard bug, not a fallback case
        raise ProxyP2PError(
            f"vault did ({my_did[:24]}…) != borrower_did ({borrower_did[:24]}…)"
        )
    lender_pub = PublicKey(did_key_to_pubkey(lender_did))
    box = Box(my_priv, lender_pub)
    sealed_prompt = base64.b64encode(bytes(box.encrypt(prompt.encode("utf-8")))).decode()

    request_id = "pxr_" + uuid.uuid4().hex[:16]
    req_body = {
        "request_id": request_id,
        "borrower_did": borrower_did,
        "lender_did": lender_did,
        "model": model,
        "provider": provider,
        "mode": mode,
        "encrypted_prompt_b64": sealed_prompt,
        "issued_at": int(time.time()),
    }

    async def _roundtrip() -> dict[str, Any]:
        from sisoul.chat.transport import KuboGossipSubTransport, WireEnvelope

        transport = KuboGossipSubTransport(api_url=kubo_api)
        resp_topic = proxy_resp_topic_for(borrower_did)
        gen = await transport.subscribe(resp_topic)
        try:
            # let the kubo subscription actually attach before we publish
            await asyncio.sleep(0.5)
            wire = WireEnvelope(kind="proxy-request", body=req_body)
            await transport.publish(proxy_req_topic_for(lender_did), wire.to_bytes())

            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise ProxyP2PTimeout(
                        f"lender {lender_did[:24]}… 未在 {timeout:.0f}s 内响应 proxy 请求"
                    )
                try:
                    raw = await asyncio.wait_for(gen.__anext__(), timeout=remaining)
                except (asyncio.TimeoutError, StopAsyncIteration):
                    raise ProxyP2PTimeout(
                        f"lender {lender_did[:24]}… 未在 {timeout:.0f}s 内响应 proxy 请求"
                    ) from None
                try:
                    resp_wire = WireEnvelope.from_bytes(raw)
                except Exception:
                    continue
                if resp_wire.kind != "proxy-response":
                    continue
                body = resp_wire.body or {}
                if body.get("request_id") != request_id:
                    continue
                return body
        finally:
            try:
                await gen.aclose()  # type: ignore[attr-defined]
            except Exception:
                pass

    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            body = asyncio.run(_roundtrip())  # 普通 sync 上下文 (CLI / threadpool)
        else:
            # 调用方在 event loop 里 (不该, 但兜底): 丢到独立线程跑自己的 loop,
            # 防 asyncio.run RuntimeError + 防阻塞调用方 loop.
            import concurrent.futures as _cf

            with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                body = _ex.submit(asyncio.run, _roundtrip()).result(timeout=timeout + 10)
    except ProxyP2PError:
        raise
    except Exception as e:  # noqa: BLE001 — kubo down / httpx error → typed error
        raise ProxyP2PError(f"transport 失败: {type(e).__name__}: {e}") from e

    status = body.get("status")
    if status != "completed":
        raise ProxyP2PError(
            f"lender 返回 status={status}: {body.get('error_class') or body.get('reason') or ''}"
        )
    try:
        sealed = base64.b64decode(body["encrypted_response_b64"])
        text = box.decrypt(sealed).decode("utf-8")
    except Exception as e:  # noqa: BLE001
        raise ProxyP2PError(f"response 解密失败: {type(e).__name__}") from e
    return {
        "text": text,
        "prompt_tokens": int(body.get("prompt_tokens", 0) or 0),
        "response_tokens": int(body.get("response_tokens", 0) or 0),
        "model_used": str(body.get("model_used", model)),
        "request_id": request_id,
    }


# ── lender side: async serve loop (daemon startup task) ─────────────────────


async def lender_serve_loop(transport: Any, my_did: str) -> None:
    """Subscribe to my proxy topic and answer requests forever.

    Decrypt happens inside ``EncryptedProxy.proxy_chat_request`` (memory-only,
    never logged). LLM call honours OPENAI_API_BASE / OPENAI_API_KEY env on
    this (lender) daemon.
    """
    from sisoul.chat.transport import WireEnvelope
    from sisoul.friend.encrypted_proxy import EncryptedProxy

    try:
        _did, my_priv, my_pub = load_vault_keypair()
    except ProxyP2PError as e:
        print(f"[daemon] proxy serve loop skipped: {e}", file=sys.stderr)
        return

    proxy = EncryptedProxy(self_priv=my_priv, self_pub=my_pub, self_did=my_did)
    topic = proxy_req_topic_for(my_did)

    while True:
        try:
            gen = await transport.subscribe(topic)
            print(f"[daemon] proxy serve loop subscribed {topic}", file=sys.stderr)
            async for raw in gen:
                try:
                    wire = WireEnvelope.from_bytes(raw)
                except Exception:
                    continue
                if wire.kind != "proxy-request":
                    continue
                body = wire.body or {}
                if body.get("lender_did") != my_did:
                    continue
                asyncio.get_running_loop().create_task(
                    _serve_one(transport, proxy, body)
                )
        except Exception as e:  # noqa: BLE001 — kubo down / transient
            print(
                f"[daemon] proxy serve loop crashed: {type(e).__name__}: {e}; retry in 10s",
                file=sys.stderr,
            )
            await asyncio.sleep(10)


async def _serve_one(transport: Any, proxy: Any, body: dict) -> None:
    from sisoul.chat.transport import WireEnvelope

    request_id = str(body.get("request_id", ""))
    borrower_did = str(body.get("borrower_did", ""))
    resp: dict[str, Any] = {"request_id": request_id, "lender_did": proxy.self_did}

    # alpha gate: only strong-tie-auto auto-serves; per-request approval is P1
    if body.get("mode") != "strong-tie-auto":
        resp.update(status="denied", reason="per-request approval not implemented (P1); only strong-tie-auto served")
        await _publish_resp(transport, borrower_did, resp)
        return

    try:
        borrower_pub = did_key_to_pubkey(borrower_did)
        sealed_prompt = base64.b64decode(str(body.get("encrypted_prompt_b64", "")))
        from sisoul.friend.encrypted_proxy import proxy_chat_request_async

        encrypted_response, meta = await proxy_chat_request_async(
            proxy,
            borrower_did=borrower_did,
            borrower_pubkey=borrower_pub,
            encrypted_prompt=sealed_prompt,
            target_model=str(body.get("model", "")),
            provider=str(body.get("provider", "openai")),
        )
        resp.update(
            status="completed",
            encrypted_response_b64=base64.b64encode(encrypted_response).decode(),
            prompt_tokens=meta.prompt_token_count,
            response_tokens=meta.response_token_count,
            model_used=meta.target_model,
        )
    except Exception as e:  # noqa: BLE001 — never leak prompt via exception chain
        resp.update(status="failed", error_class=type(e).__name__)
    await _publish_resp(transport, borrower_did, resp)


async def _publish_resp(transport: Any, borrower_did: str, resp: dict) -> None:
    from sisoul.chat.transport import WireEnvelope

    try:
        wire = WireEnvelope(kind="proxy-response", body=resp)
        await transport.publish(proxy_resp_topic_for(borrower_did), wire.to_bytes())
    except Exception as e:  # noqa: BLE001
        print(f"[daemon] proxy resp publish failed: {type(e).__name__}: {e}", file=sys.stderr)


__all__ = [
    "ProxyP2PError",
    "ProxyP2PTimeout",
    "proxy_req_topic_for",
    "proxy_resp_topic_for",
    "load_vault_keypair",
    "borrower_roundtrip",
    "lender_serve_loop",
    "DEFAULT_ROUNDTRIP_TIMEOUT",
]
