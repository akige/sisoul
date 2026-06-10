"""sisoul daemon · 本地后台 HTTP API server (Phase 1 W2 ship).

§28 §2.1 endpoints (Phase 1 W2 范围, 其他 endpoints 后续 phase 补):
- GET /sisoul/health → {"status":"ok", "version":..., "phase":...}

后续 phase 补:
- W3+: GET /sisoul/status (vault size / 长期目标 / 同步状态)
- W5+: GET /sisoul/preferences (拉最新偏好 inject system prompt)
- W6+: GET /sisoul/long-term-goals
- W11+: POST /sisoul/remember
- ... (详 §28 §2.1 完整 endpoint 表)
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from sisoul import DAEMON_HOST, DAEMON_PORT, __phase__, __version__


class HealthResponse(BaseModel):
    """GET /sisoul/health 响应."""

    status: str
    version: str
    phase: str
    daemon: dict


def create_app() -> FastAPI:
    """构造 FastAPI app (test + 真启动都用)."""
    app = FastAPI(
        title="sisoul daemon",
        version=__version__,
        description=f"sisoul meta-layer daemon ({__phase__}). 元层协议本地 HTTP API.",
    )

    # ── CORS (允许 GH Pages https://akige.github.io 跨域访问本机 daemon, 备用)
    # 主路径仍是 daemon-served PWA on http://127.0.0.1:<port>/app/ (same-origin
    # 不需要 CORS), 但 GH Pages 上的 PWA 有时也想 fetch 本机 daemon, 给开.
    try:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "https://akige.github.io",
                "http://127.0.0.1:9876",
                "http://localhost:9876",
                "http://127.0.0.1:5173",  # vite dev
                "http://localhost:5173",
            ],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    except Exception:
        pass  # fastapi 没 CORSMiddleware 也不阻塞

    # ── PWA compat aliases (PWA v1.0 ships these URLs, daemon naming差异 fix)
    # 不改 PWA api/daemon.ts URL 避免重 build, 直接 daemon side alias.
    from fastapi.responses import JSONResponse

    @app.get("/sisoul/lend/list", include_in_schema=False)
    async def _alias_lend_list():
        """PWA alias → /sisoul/lend/pending. PWA 期望 LendListResponse
        = {requests: LendRequestItem[]}, 不是 raw array (caller spread
        会 TypeError: Spread requires iterable not undefined).

        字段映射 (daemon LendStore.list_pending() → PWA LendRequestItem):
        - id              → request_id
        - amount          → token_count
        - model           → model
        - 派生 provider   = note.provider 或 'unknown'
        - borrower_did    → borrower_did
        - created_at int  → ISO string (PWA relativeTime() 期望 ISO)
        - expires_at int  → ISO string
        - emergency_flag  → emergency_flag
        - note 解析 reason → reason
        """
        from sisoul.friend.lend import LendStore
        import json as _json
        from datetime import datetime as _dt, timezone as _tz

        def _epoch_to_iso(v):
            if v is None:
                return None
            if isinstance(v, (int, float)):
                try:
                    return _dt.fromtimestamp(float(v), tz=_tz.utc).isoformat()
                except Exception:
                    return None
            return str(v)

        try:
            with LendStore() as store:
                pending = store.list_pending()
                raw_items = [
                    r.to_dict() if hasattr(r, "to_dict") else r.__dict__
                    for r in pending
                ]
            mapped = []
            for r in raw_items:
                # note 是 JSON string, 解 provider/reason
                provider = "unknown"
                reason = None
                note_raw = r.get("note", "")
                if note_raw:
                    try:
                        n = _json.loads(note_raw) if isinstance(note_raw, str) else note_raw
                        provider = n.get("provider", provider)
                        reason = n.get("reason") or n.get("prompt")
                    except Exception:
                        pass
                mapped.append({
                    "request_id": r.get("id") or r.get("request_id") or "",
                    "borrower_did": r.get("borrower_did", ""),
                    "borrower_handle": r.get("borrower_handle"),
                    "provider": provider,
                    "model": r.get("model", ""),
                    "token_count": int(r.get("amount") or r.get("token_count") or 0),
                    "reason": reason,
                    "emergency_flag": bool(r.get("emergency_flag", False)),
                    "created_at": _epoch_to_iso(r.get("created_at"))
                                  or r.get("created_at_iso") or "",
                    "expires_at": _epoch_to_iso(r.get("expires_at"))
                                  or r.get("expires_at_iso") or "",
                })
            return {"requests": mapped}
        except Exception as _e:  # noqa: BLE001
            import sys as _sys
            print(f"[lend/list alias] failed: {_e}", file=_sys.stderr)
            return {"requests": []}

    @app.get("/sisoul/borrow/proxy-list", include_in_schema=False)
    async def _alias_borrow_proxy_list():
        """PWA alias → /sisoul/borrow-proxy/list. PWA 期望 ProxyListResponse
        = {sessions: ProxySessionItem[]}, caller spread {} 报错."""
        try:
            from sisoul.friend.borrow import list_proxy_sessions
            sessions = list_proxy_sessions()
            # sessions 可能 list 也可能 {count, sessions} dict; 兜底 normalize
            if isinstance(sessions, dict):
                return {"sessions": sessions.get("sessions", [])}
            return {"sessions": list(sessions) if sessions else []}
        except Exception:
            return {"sessions": []}

    @app.get("/sisoul/ledger/all", include_in_schema=False)
    async def _alias_ledger_all(direction: str = ""):
        """PWA 期望 LedgerResponse = {entries, total_tokens, total_cost_usd}.
        cross-friend sum 没实现, 返空 totals 让 PWA 渲染空态."""
        return {
            "direction": direction or None,
            "entries": [],
            "total_tokens": 0,
            "total_cost_usd": 0,
        }

    @app.post("/sisoul/borrow/run", include_in_schema=False)
    async def _alias_borrow_run(body: dict):
        """PWA alias → /sisoul/borrow. Translate PWA body shape:
          PWA  {friend_did, provider, model, token_count, reason, emergency_flag}
          → daemon /borrow {lender_did, borrower_did, resource_type='llm_quota',
                             amount, model, prompt, emergency_flag, ...}
        Also fetch Alice's own did (borrower) from daemon /sisoul/did 真路径.
        """
        try:
            from sisoul.daemon_routes.friend import _post_borrow, _BorrowRequestBody  # type: ignore
        except Exception as _ie:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=500, content={"error": f"borrow router import 失败: {_ie}"})
        # 拿 borrower_did (Alice 真 did:key, 跟 perms 配置匹配):
        # 真路径 — 复用 CLI did show 真函数 (X25519 派生).
        borrower_did = "did:key:unknown"
        try:
            from sisoul.identity.seed import load_mnemonic_from_file, mnemonic_to_master_key
            from sisoul.identity.did_key import generate_did_key_from_master
            mnemonic = load_mnemonic_from_file()
            master = mnemonic_to_master_key(mnemonic)
            borrower_did, _priv, _pub = generate_did_key_from_master(master, index=0)
        except Exception as _de:  # noqa: BLE001
            import sys as _sys
            print(f"[borrow/run] borrower did derive failed: {_de}", file=_sys.stderr)
        # translate. PWA 可传 mode:
        # - strong-tie-auto (默认): 强关系预授权, 不等 lender 真批
        # - per-request: 真等 lender 在 Lend 页批准 (GossipSub ack 解锁), 120s
        _mode = body.get("mode") or "strong-tie-auto"
        if _mode not in ("strong-tie-auto", "per-request"):
            _mode = "strong-tie-auto"
        translated = _BorrowRequestBody(
            borrower_did=borrower_did,
            lender_did=body.get("friend_did", ""),
            resource_type="llm_quota",
            amount=int(body.get("token_count", 0)),
            model=body.get("model", ""),
            prompt=body.get("reason", "") or f"借 {body.get('token_count',0)} tokens via PWA",
            emergency_flag=bool(body.get("emergency_flag", False)),
            force_mode=_mode,
            per_request_timeout_sec=120.0 if _mode == "per-request" else 10.0,
        )
        # _post_borrow 是同步阻塞函数 (内部 P2P proxy 往返可达 15s),
        # 走 to_thread 不阻塞 event loop (借出方 serve loop 还要靠这个 loop 跑).
        import asyncio as _aio
        return await _aio.to_thread(_post_borrow, translated)

    @app.post("/sisoul/friend/add", include_in_schema=False)
    async def _alias_friend_add(body: dict):
        """PWA alias → /sisoul/friend/request.

        PWA addFriend({did, handle?, trust_level?}) 期望 AddFriendResponse
        {did, handle, trust_level, added_at, verified}.

        daemon /sisoul/friend/request 真 endpoint 收 {target_did, message, ...}
        返 {request_id, requester_did, target_did, direction, message,
            created_at, attestation_uid, status}.

        映射:
        - PWA did       → daemon target_did
        - PWA handle    → daemon message ("add via handle=<h>, trust=<tl>")
        - daemon created_at → PWA added_at
        - verified      = True (FriendRequest 入 EAS queue 成功就视为)
        - trust_level   = body.trust_level or 1 (本地 cache 不存 trust_level,
                          PWA 用作 badge 显示, daemon 端默认 L1 Read)
        """
        from fastapi.responses import JSONResponse
        target_did = (body.get("did") or body.get("target_did") or "").strip()
        if not target_did:
            return JSONResponse(
                status_code=400, content={"detail": "did required"}
            )
        handle = body.get("handle") or ""
        trust_level = int(body.get("trust_level") or 1)
        message = body.get("message") or (
            f"add via PWA: handle={handle} trust=L{trust_level}"
            if handle else f"add via PWA trust=L{trust_level}"
        )
        try:
            from sisoul.daemon_routes.friend import _rel as _friend_rel
            from sisoul.friend.relationship import (
                FriendError, FriendRequestError,
            )
        except Exception as _ie:  # noqa: BLE001
            return JSONResponse(
                status_code=500,
                content={"detail": f"friend module import 失败: {_ie}"},
            )
        try:
            rel = _friend_rel(None, None, None, None)
        except Exception as _re:  # noqa: BLE001
            return JSONResponse(
                status_code=400,
                content={"detail": f"resolve own did 失败: {_re}"},
            )
        try:
            req = rel.send_friend_request(target_did, message=message)
        except (FriendError, FriendRequestError) as _e:  # noqa: BLE001
            return JSONResponse(
                status_code=400, content={"detail": str(_e)}
            )
        if not handle:
            if target_did.startswith("did:sisoul:"):
                handle = target_did[len("did:sisoul:"):]
            elif target_did.startswith("did:key:"):
                handle = target_did[8:16]
            else:
                handle = target_did[:8]
        return {
            "did": target_did,
            "handle": handle,
            "trust_level": trust_level,
            "added_at": req.created_at,
            "verified": True,
            "request_id": req.request_id,
        }

    @app.get("/sisoul/friend/list", include_in_schema=False)
    async def _alias_friend_list():
        """PWA alias 覆盖 friend_router /sisoul/friend/list.

        daemon _FriendOut 真返字段无 trust_level (PWA badge access f.trust_level
        → undefined → 显示 'Lundefined'). 本 alias 加 trust_level 默认 2
        (L2 Query, 内测合理默认), 兜底前端 badge 显示 'L2'.

        其他映射:
        - daemon created_at  → PWA connected_at (PWA formatDate 用)
        - daemon last_interaction → PWA last_seen_at (ISO 保留)

        合并 didkey_friends.json (alpha install 旧路径) 不丢老朋友.
        """
        out = []
        try:
            from sisoul.daemon_routes.friend import _rel as _friend_rel
            from sisoul.friend.relationship import FriendDB
            rel = _friend_rel(None, None, None, None)
            with FriendDB(db_path=rel.db_path) as db:
                friends = db.list_friends()
            for f in friends:
                d = f.to_dict() if hasattr(f, "to_dict") else dict(f)
                # 解历史双前缀残留 (did:sisoul:did:key:… 老 _normalize_did bug),
                # PWA 拿这种 did 去 borrow 解不出 X25519 pubkey 必失败.
                _did = str(d.get("did") or "")
                while _did.startswith("did:sisoul:did:"):
                    _did = _did[len("did:sisoul:"):]
                d["did"] = _did
                d.setdefault("trust_level", 2)
                d.setdefault("connected_at", d.get("created_at") or "")
                out.append(d)
        except Exception as _re:  # noqa: BLE001
            import sys as _sys
            print(f"[friend/list alias] friends.db read failed: {_re}",
                  file=_sys.stderr)
        # didkey_friends.json (alpha 旧 install 兼容, 真路径 = identity/didkey_friends.json)
        # 真 schema: [{did, pubkey_hex, key_type, nickname, added_at, method, ...}]
        # rename → PWA Friend shape (did, handle, trust_level, connected_at, ...)
        try:
            from pathlib import Path as _PP
            import json as _json
            import os as _os
            _vault = _PP(_os.environ.get(
                "SISOUL_VAULT", str(_PP.home() / ".sisoul"))).expanduser()
            _candidates = [
                _vault / "identity" / "didkey_friends.json",
                _vault / "didkey_friends.json",  # legacy
            ]
            _didkey_file = next((p for p in _candidates if p.exists()), None)
            if _didkey_file is not None:
                extras = _json.loads(_didkey_file.read_text("utf-8"))
                seen = {x.get("did") for x in out}
                if isinstance(extras, list):
                    for e in extras:
                        did = e.get("did")
                        if not did or did in seen:
                            continue
                        added_at = e.get("added_at") or e.get("created_at") or ""
                        out.append({
                            "did": did,
                            "handle": e.get("handle") or e.get("nickname") or "",
                            "status": e.get("status") or "active",
                            "strong_tie_score": float(e.get("strong_tie_score") or 0.5),
                            "trust_level": int(e.get("trust_level") or 2),
                            "created_at": added_at,
                            "connected_at": added_at,
                            "became_active_at": e.get("became_active_at") or added_at,
                            "last_interaction": e.get("last_interaction"),
                            "interaction_count": int(e.get("interaction_count") or 0),
                            "notes": e.get("notes") or "did:key 朋友",
                        })
        except Exception as _e:  # noqa: BLE001
            import sys as _sys
            print(f"[friend/list alias] didkey merge failed: {_e}",
                  file=_sys.stderr)
        return out

    @app.get("/sisoul/notify/stream", include_in_schema=False)
    async def _alias_notify_stream():
        """PWA 用 EventSource (SSE GET) 监听真事件:
        lend.request (借入请求到达) / lend.update (批/拒) / borrow.update (ack 回执).
        30s 无事件发 heartbeat keep-alive."""
        from fastapi.responses import StreamingResponse
        import asyncio as _aio
        from sisoul import daemon_events as _ev

        async def _stream():
            q = _ev.attach_queue()
            try:
                while True:
                    try:
                        evt = await _aio.wait_for(q.get(), timeout=30)
                        yield _ev.sse_format(evt)
                    except _aio.TimeoutError:
                        yield b"event: heartbeat\ndata: {}\n\n"
            except _aio.CancelledError:
                return
            finally:
                _ev.detach_queue(q)

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/sisoul/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Health check endpoint. Phase 1 W2 ship."""
        return HealthResponse(
            status="ok",
            version=__version__,
            phase=__phase__,
            daemon={
                "host": DAEMON_HOST,
                "port": DAEMON_PORT,
                "endpoints_implemented": ["/sisoul/health"],
                "endpoints_planned": [
                    "/sisoul/status",
                    "/sisoul/preferences",
                    "/sisoul/long-term-goals",
                    "/sisoul/remember",
                    "/sisoul/audit",
                    "/sisoul/session-summary",
                    "/sisoul/goal-progress",
                    "/sisoul/did",
                    "/sisoul/restore",
                    "/sisoul/export",
                    "/sisoul/friend/request",
                    "/sisoul/friend/accept",
                    "/sisoul/friend/list",
                    "/sisoul/borrow",
                    "/sisoul/lend/approve",
                    "/sisoul/ledger",
                ],
            },
        )

    # ── 波 3 routes (BIP-39 identity / DID / PWA) ───────────────────────────
    # qa-D 发现 ImportError 被静默吞掉, 改裸 import + print 异常 (Phase 3 用 logging)
    import sys
    try:
        from sisoul.daemon_routes.identity import identity_router
        app.include_router(identity_router)
    except Exception as e:
        print(f"[daemon] identity_router import failed: {type(e).__name__}: {e}", file=sys.stderr)

    try:
        from sisoul.daemon_routes.did import did_router
        app.include_router(did_router)
    except Exception as e:
        print(f"[daemon] did_router import failed: {type(e).__name__}: {e}", file=sys.stderr)

    try:
        from sisoul.daemon_routes.pwa import router as pwa_router
        app.include_router(pwa_router)
    except Exception as e:
        print(f"[daemon] pwa_router import failed: {type(e).__name__}: {e}", file=sys.stderr)

    # ── 波 4 routes (P2P / EAS attestation / Arweave snapshot) ───────────────
    try:
        from sisoul.daemon_routes.p2p import p2p_router
        app.include_router(p2p_router)
    except Exception as e:
        print(f"[daemon] p2p_router import failed: {type(e).__name__}: {e}", file=sys.stderr)

    try:
        from sisoul.daemon_routes.attest import attest_router, audit_router
        app.include_router(attest_router)
        app.include_router(audit_router)
    except Exception as e:
        print(f"[daemon] attest_router import failed: {type(e).__name__}: {e}", file=sys.stderr)

    try:
        from sisoul.daemon_routes.snapshot import snapshot_router
        app.include_router(snapshot_router)
    except Exception as e:
        print(f"[daemon] snapshot_router import failed: {type(e).__name__}: {e}", file=sys.stderr)

    # ── 波 5 routes (P2P 朋友共享: friend 统一 router 已内嵌 proxy + permissions) ─
    # 波 7 dev-A bug-1 修复: friend_router 内部已 include_router(proxy_router) +
    # include_router(permissions_router), 主 daemon.py 再单独 include 会触发
    # 10 条 FastAPI Duplicate Operation ID 警告 (qa-D/qa-E/qa-C 三波报告均反映).
    # 改成只 include friend_router 一个.
    try:
        from sisoul.daemon_routes.friend import friend_router
        app.include_router(friend_router)
    except Exception as e:
        print(f"[daemon] friend_router import failed: {type(e).__name__}: {e}", file=sys.stderr)

    # ── Wave B' P0-1 routes (borrow proxy-chat 真 LLM forwarder endpoint) ──────
    try:
        from sisoul.daemon_routes.proxy import borrow_proxy_router
        app.include_router(borrow_proxy_router)
    except Exception as e:
        print(f"[daemon] borrow_proxy_router import failed: {type(e).__name__}: {e}", file=sys.stderr)

    # ── Wave T3 routes (OpenAI Chat Completions compat → borrow 透明转 Bob) ────
    try:
        from sisoul.daemon_routes.openai_compat import openai_compat_router
        app.include_router(openai_compat_router)
    except Exception as e:
        print(f"[daemon] openai_compat_router import failed: {type(e).__name__}: {e}", file=sys.stderr)

    # ── 波 6 routes (AI 技能 packaging + IPFS 加密分发) ──────────────────────
    try:
        from sisoul.daemon_routes.skill import skill_router
        app.include_router(skill_router)
    except Exception as e:
        print(f"[daemon] skill_router import failed: {type(e).__name__}: {e}", file=sys.stderr)

    # ── Phase 2 P2-2 (RAG selective inject) ─────────────────────────────────
    try:
        from sisoul.daemon_routes.rag import rag_router
        app.include_router(rag_router)
    except Exception as e:
        print(f"[daemon] rag_router import failed: {type(e).__name__}: {e}", file=sys.stderr)

    # ── Phase 2 P2-3 (Goal-mode v1.1 scheduler + reminder) ──────────────────
    try:
        from sisoul.daemon_routes.goal import goal_router
        app.include_router(goal_router)
    except Exception as e:
        print(f"[daemon] goal_router import failed: {type(e).__name__}: {e}", file=sys.stderr)

    # Phase 2 P2-3: spawn goal scheduler 后台 task (daemon 启动时)
    try:
        from sisoul.goal.scheduler import register_scheduler_on_app
        register_scheduler_on_app(app)
    except Exception as e:
        print(f"[daemon] goal scheduler register failed: {type(e).__name__}: {e}", file=sys.stderr)

    # ── Phase 3 P3-4 routes (DAO governance: SisoulGov + PIPRegistry) ────────
    try:
        from sisoul.daemon_routes.dao import dao_router
        app.include_router(dao_router)
    except Exception as e:
        print(f"[daemon] dao_router import failed: {type(e).__name__}: {e}", file=sys.stderr)

    # ── Wave B' P1-1 routes (notify / 在线状态 / 推送 · agent-B3) ────────────
    try:
        from sisoul.daemon_routes.notify import notify_router
        app.include_router(notify_router)
    except Exception as e:
        print(f'[daemon] notify_router import failed: {type(e).__name__}: {e}', file=sys.stderr)

    # ── Phase 3 v2.0 智能体网络 routes (foundation skeleton) ───────────────
    try:
        from sisoul.daemon_routes.v2_case import router as v2_case_router
        app.include_router(v2_case_router)
    except Exception as e:
        print(f'[daemon] v2_case_router import failed: {type(e).__name__}: {e}', file=sys.stderr)
    try:
        from sisoul.daemon_routes.v2_skill import router as v2_skill_router
        app.include_router(v2_skill_router)
    except Exception as e:
        print(f'[daemon] v2_skill_router import failed: {type(e).__name__}: {e}', file=sys.stderr)


    try:
        from sisoul.daemon_routes.v2_more import router as v2_more_router
        app.include_router(v2_more_router)
    except Exception as e:
        print(f'[daemon] v2_more_router import failed: {type(e).__name__}: {e}', file=sys.stderr)
    try:
        from sisoul.daemon_routes.metrics import metrics_router
        app.include_router(metrics_router)
    except Exception as e:
        print(f'[daemon] metrics_router import failed: {type(e).__name__}: {e}', file=sys.stderr)

    # ── Phase 3 v3 routes (RSI · Recursive Self-Improvement skeleton) ──────────
    try:
        from sisoul.daemon_routes.v3_rsi import router as v3_rsi_router
        app.include_router(v3_rsi_router)
    except Exception as e:
        print(f'[daemon] v3_rsi_router import failed: {type(e).__name__}: {e}', file=sys.stderr)

    # ── Phase D mobile-pwa push device register routes ──────────────────────
    try:
        from sisoul.daemon_routes.push import push_router
        app.include_router(push_router)
    except Exception as e:
        print(f'[daemon] push_router import failed: {type(e).__name__}: {e}', file=sys.stderr)

    # ── Round 10 founder-agent routes ────────────────────────────────────────
    try:
        from sisoul.daemon_routes.founder import founder_router
        app.include_router(founder_router)
    except Exception as e:
        print(f'[daemon] founder_router import failed: {type(e).__name__}: {e}', file=sys.stderr)

    # ── daemon 启动时自动 init EncryptedProxy (替代用户手动 sisoul proxy start) ──
    # 跟 cli_commands/proxy.py 同逻辑, 但跑在 daemon 进程里 (set_global_proxy 才在
    # 同进程可见). 没 seed → 跳过 (init 前用 --skip-seed 跑的 dev 模式).
    @app.on_event("startup")
    async def _auto_init_global_proxy() -> None:
        try:
            from sisoul.identity.seed import (
                load_mnemonic_from_file,
                mnemonic_to_master_key,
            )
            from sisoul.friend.encrypted_proxy import (
                EncryptedProxy,
                derive_friend_session_keypair,
                set_global_proxy,
            )
            mnemonic = load_mnemonic_from_file()
            master = mnemonic_to_master_key(mnemonic)
            priv, pub = derive_friend_session_keypair(master, friend_index=0)
            self_did = "unknown.sisoul.eth"
            try:
                from sisoul.identity.did import load_did_state
                st = load_did_state()
                self_did = getattr(st, "did_string", None) or getattr(st, "handle", self_did)
            except Exception:  # noqa: BLE001
                pass
            proxy = EncryptedProxy(self_priv=priv, self_pub=pub, self_did=self_did)
            set_global_proxy(proxy)
            print(f"[daemon] EncryptedProxy auto-init OK (self_did={self_did})", file=sys.stderr)
        except FileNotFoundError:
            # seed 不存在 (dev mode --skip-seed) — borrow/proxy-chat 会返 409
            print("[daemon] no seed found, proxy auto-init skipped (--skip-seed mode)", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[daemon] proxy auto-init failed: {type(e).__name__}: {e}", file=sys.stderr)

    # ── Workstream A1: daemon 启动时内嵌 kubo (GossipSub 传输底座) ──────────────
    # 用户红线 §10.3: 只允许 mac/wsl/win 跑; aws-*/cloud 主机一律拒 (打一行日志后
    # 继续以 local-only 模式运行, 不起 GossipSub). 单一真相源 = host_policy.
    @app.on_event("startup")
    async def _maybe_start_embedded_kubo() -> None:
        import asyncio as _asyncio
        import os as _os

        from sisoul.p2p.host_policy import cloud_refusal_reason

        reason = cloud_refusal_reason()
        if reason is not None:
            print(
                f"[daemon] P2P/kubo disabled on this host ({reason}); "
                f"running local-only, no GossipSub. (policy: mac/wsl/win only)",
                file=sys.stderr,
            )
            return
        if _os.environ.get("SISOUL_EMBED_KUBO", "1") == "0":
            print("[daemon] SISOUL_EMBED_KUBO=0 — embedded kubo skipped.", file=sys.stderr)
            return
        try:
            from sisoul.p2p.ipfs_kubo import find_kubo_binary, get_default_node

            env_mode = _os.environ.get("SISOUL_IPFS_MODE", "")
            if env_mode not in ("external-daemon", "mock") and find_kubo_binary() is None:
                print(
                    "[daemon] no kubo binary — P2P disabled. "
                    "Install: brew install ipfs / apt install kubo (then restart daemon).",
                    file=sys.stderr,
                )
                return
            node = get_default_node()
            app.state.kubo_node = node

            async def _boot() -> None:
                try:
                    await node.start()
                    print(
                        f"[daemon] embedded kubo started "
                        f"(mode={node.mode}, peer_id={node.peer_id})",
                        file=sys.stderr,
                    )
                except Exception as e:  # noqa: BLE001
                    print(
                        f"[daemon] embedded kubo start failed: {type(e).__name__}: {e}",
                        file=sys.stderr,
                    )

            _asyncio.create_task(_boot())
        except Exception as e:  # noqa: BLE001
            print(f"[daemon] embedded kubo setup failed: {type(e).__name__}: {e}", file=sys.stderr)

    # ── Workstream A2: periodically announce our prekey bundle on GossipSub so
    # peers can discover it (decentralised, no directory). Allowed hosts + seed
    # only. A `chat send @me` on the other side subscribes and catches it. ──────
    @app.on_event("startup")
    async def _maybe_announce_prekey_loop() -> None:
        import asyncio as _asyncio
        import os as _os
        from pathlib import Path as _Path

        from sisoul.p2p.host_policy import cloud_refusal_reason

        if cloud_refusal_reason() is not None:
            return  # no P2P on cloud (red line)
        if _os.environ.get("SISOUL_PREKEY_ANNOUNCE", "1") == "0":
            return
        vault = _Path(_os.environ.get("SISOUL_VAULT", str(_Path.home() / ".sisoul"))).expanduser()
        if not (vault / "seed.txt").exists():
            print("[daemon] no seed — prekey announce loop skipped.", file=sys.stderr)
            return
        interval = float(_os.environ.get("SISOUL_PREKEY_ANNOUNCE_INTERVAL", "15"))

        async def _loop() -> None:
            await _asyncio.sleep(6)  # let embedded kubo come up first
            try:
                from sisoul.cli_commands.chat import _build_manager

                mgr = _build_manager(False)  # KuboGossipSub transport + persisted keys
            except Exception as e:  # noqa: BLE001
                print(f"[daemon] prekey announce loop init failed: {e}", file=sys.stderr)
                return
            announced_once = False
            while True:
                try:
                    await mgr.announce_prekey()
                    if not announced_once:
                        print(
                            f"[daemon] prekey announce loop active (every {interval:.0f}s, "
                            f"did={mgr.local_did})",
                            file=sys.stderr,
                        )
                        announced_once = True
                except Exception:  # noqa: BLE001  (kubo not up yet / transient — retry)
                    pass
                await _asyncio.sleep(interval)

        _asyncio.create_task(_loop())

    # ── Workstream A3: subscribe lend-request topic + LendAutoApprover (v1.1) ──
    # Lender daemon receives borrow-requests via GossipSub instead of Waku push.
    # Auto-approves micropay requests once TronGrid confirms USDT payment (opt-in).
    @app.on_event("startup")
    async def _maybe_start_lend_loops() -> None:
        import asyncio as _aio
        from pathlib import Path as _Path
        import os as _os

        from sisoul.p2p.host_policy import cloud_refusal_reason

        if cloud_refusal_reason() is not None:
            return  # red line: no P2P / lend service on cloud hosts
        vault = _Path(_os.environ.get("SISOUL_VAULT", str(_Path.home() / ".sisoul"))).expanduser()
        if not (vault / "seed.txt").exists():
            print("[daemon] no seed — lend loops skipped.", file=sys.stderr)
            return

        # P1 2026-06-10: 让 threadpool 路由 (lend approve/deny) 能 publish SSE 事件
        from sisoul import daemon_events as _devents
        _devents.bind_loop(_aio.get_running_loop())

        async def _boot_lend() -> None:
            # Wait for kubo to be up before reaching for the pubsub transport
            await _aio.sleep(8)
            try:
                from sisoul.cli_commands.chat import _build_manager
                from sisoul.chat.transport import set_default_transport
                from sisoul.friend.lend_gossipsub import subscribe_lend_requests
                from sisoul.friend.lend import LendStore
                from sisoul.friend.lend_auto_approve import LendAutoApprover, is_enabled

                mgr = _build_manager(False)
                transport = mgr.transport
                set_default_transport(transport)  # let borrow.py find it
                my_did = mgr.local_did
                print(f"[daemon] lend loops bound to did={my_did}", file=sys.stderr)

                # Persist inbound borrow-requests into LendStore so `sisoul lend list`
                # / approve / deny can act on them.
                async def _ingest_loop() -> None:
                    while True:
                        try:
                            async for env in subscribe_lend_requests(transport, my_did):
                                try:
                                    body = env.body or {}
                                    with LendStore() as store:
                                        try:
                                            _req = store.request_lend(
                                                borrower_did=env.sender_did,
                                                lender_did=my_did,
                                                resource_type=str(body.get("resource_type", "llm-call")),
                                                amount=int(body.get("amount", 0) or 0),
                                                model=str(body.get("model", "")),
                                                mode=str(body.get("mode", "per-request")),
                                                ttl_sec=int(body.get("ttl_sec", 3600)),
                                                emergency_flag=bool(body.get("emergency_flag", False)),
                                                note=__import__("json").dumps(body),
                                            )
                                            # P1: 推 PWA SSE — Lend 页 toast + 刷新 pending.
                                            # shape 对齐 PWA LendRequestItem (token_count 等).
                                            from datetime import datetime as _dt, timezone as _tz
                                            _devents.publish("lend.request", {
                                                "request_id": _req.id,
                                                "borrower_did": _req.borrower_did,
                                                "provider": str(body.get("provider", "")) or "llm",
                                                "model": _req.model,
                                                "token_count": _req.amount,
                                                "reason": str(body.get("reason", "") or ""),
                                                "emergency_flag": _req.emergency_flag,
                                                "mode": _req.mode,
                                                "created_at": _dt.fromtimestamp(_req.created_at, _tz.utc).isoformat(),
                                                "expires_at": _dt.fromtimestamp(_req.created_at + _req.ttl_sec, _tz.utc).isoformat(),
                                            })
                                        except Exception as e:  # noqa: BLE001
                                            # de-dup / schema mismatch — ignore
                                            print(f"[daemon] lend ingest skip: {type(e).__name__}: {e}", file=sys.stderr)
                                except Exception:
                                    continue
                        except Exception as e:  # noqa: BLE001  (kubo down / transient)
                            print(f"[daemon] lend ingest loop crashed: {e}; retrying in 10s", file=sys.stderr)
                            await _aio.sleep(10)

                _aio.create_task(_ingest_loop())

                # P1 2026-06-10: 借入方 ack 订阅 — lender 批/拒经 GossipSub 回来,
                # 落地到本地 LendStore, borrow_resource 的 per-request 轮询才能解锁.
                async def _ack_loop() -> None:
                    from sisoul.friend.lend_gossipsub import subscribe_lend_acks
                    from sisoul.friend.lend import RequestStateError, RequestNotFoundError
                    while True:
                        try:
                            async for ack in subscribe_lend_acks(transport, my_did):
                                body = ack.body or {}
                                rid = str(body.get("request_id", ""))
                                decision = str(body.get("decision", ""))
                                if not rid:
                                    continue
                                try:
                                    with LendStore() as store:
                                        if decision == "approved":
                                            store.approve_lend(rid)
                                        elif decision in ("denied", "expired"):
                                            store.deny_lend(rid, reason=body.get("reason"))
                                except (RequestStateError, RequestNotFoundError):
                                    pass  # 已决/非本机请求 — 幂等忽略
                                except Exception as e:  # noqa: BLE001
                                    print(f"[daemon] lend ack apply skip: {type(e).__name__}: {e}", file=sys.stderr)
                                _devents.publish("borrow.update", {
                                    "request_id": rid,
                                    "decision": decision,
                                    "reason": body.get("reason"),
                                    "lender_did": ack.sender_did,
                                })
                        except Exception as e:  # noqa: BLE001
                            print(f"[daemon] lend ack loop crashed: {e}; retry in 10s", file=sys.stderr)
                            await _aio.sleep(10)

                _aio.create_task(_ack_loop())

                # P0 2026-06-10: encrypted LLM-proxy serve loop — answers
                # borrowers' sealed proxy requests with this daemon's own LLM
                # endpoint (OPENAI_API_BASE / OPENAI_API_KEY env). Daemon is
                # the "production wrapper" per encrypted_proxy._default_forwarder
                # contract, so enable the real forwarder here.
                _os.environ.setdefault("SISOUL_DEFAULT_FORWARDER_REAL", "1")
                try:
                    from sisoul.friend.proxy_p2p import lender_serve_loop
                    _aio.create_task(lender_serve_loop(transport, my_did))
                except Exception as e:  # noqa: BLE001
                    print(f"[daemon] proxy serve loop init failed: {type(e).__name__}: {e}", file=sys.stderr)

                # v1.1 auto-approve (opt-in: lender ran `sisoul lend auto-approve enable`)
                if is_enabled():
                    approver = LendAutoApprover(my_did, transport=transport)
                    await approver.start()
                    print("[daemon] lend auto-approve loop started", file=sys.stderr)
                else:
                    print("[daemon] lend auto-approve disabled (opt-in: sisoul lend auto-approve enable)", file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print(f"[daemon] lend loops init failed: {type(e).__name__}: {e}", file=sys.stderr)

        _aio.create_task(_boot_lend())

    # ── PWA static mount at /app/ ───────────────────────────────────────────
    # 让 daemon 自己 serve PWA, 避免 GitHub Pages https → http://127.0.0.1
    # mixed content block. 用户访问 http://127.0.0.1:9876/app/ 走 same-origin,
    # 浏览器无 CORS / mixed content 限制.
    # PWA build artifact 找:
    #   1. 安装位置 (pip install 后, packaged): sisoul/pwa_static/
    #   2. dev clone: <repo>/pwa/dist/
    try:
        from fastapi.responses import FileResponse
        from pathlib import Path as _P
        import sisoul as _sisoul_pkg
        _pkg_dir = _P(_sisoul_pkg.__file__).parent
        _candidates = [
            _pkg_dir / "pwa_static",  # 打包后
            _pkg_dir.parent.parent / "pwa" / "dist",  # dev clone
        ]
        _pwa_root = next((p for p in _candidates if (p / "index.html").exists()), None)
        if _pwa_root is not None:
            # SPA fallback 模式 (StaticFiles html=True 不够 — sub-path /app/vault
            # 没对应文件就 404, 让浏览器拿到 JSON 而非 PWA index.html, 菜单点击全炸).
            # 这里 catch-all: 文件存在 → serve; 不存在 → 返 index.html 让 client
            # router 处理. PWA SPA 标准做法.
            _pwa_index = _pwa_root / "index.html"

            from fastapi.responses import RedirectResponse

            @app.get("/app", include_in_schema=False)
            async def _pwa_redirect_trailing_slash():
                # 关键: 不能两个 path 都返 index — 浏览器 base URL=/app 时
                # 相对路径 ./assets/... 解析为 /assets/... (daemon 404 → 白屏).
                # 308 (permanent + 保持 method) 让浏览器跳 /app/, base URL
                # 变 /app/, 相对路径才正确解析为 /app/assets/...
                return RedirectResponse(url="/app/", status_code=308)

            @app.get("/app/", include_in_schema=False)
            async def _pwa_index_route():
                return FileResponse(_pwa_index, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

            @app.get("/app/{full_path:path}", include_in_schema=False)
            async def _pwa_spa(full_path: str):
                # 防 path traversal: 限定在 _pwa_root 子树
                target = (_pwa_root / full_path).resolve()
                try:
                    target.relative_to(_pwa_root.resolve())
                except ValueError:
                    return FileResponse(_pwa_index)  # 越界 → SPA fallback
                if target.is_file():
                    # asset (含 hash) 强 cache
                    headers = {"Cache-Control": "public, max-age=31536000, immutable"} \
                        if "/assets/" in full_path else {}
                    return FileResponse(target, headers=headers)
                # SPA fallback — 任何不存在的 path 都返 index.html
                return FileResponse(_pwa_index, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

            print(f"[daemon] PWA mounted at /app/ (SPA fallback) from {_pwa_root}", file=sys.stderr)
        else:
            print(f"[daemon] PWA not found in any of {_candidates} — /app/ not mounted "
                  f"(rebuild: cd pwa && npm run build)", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[daemon] PWA mount failed: {type(e).__name__}: {e}", file=sys.stderr)

    return app


# Module-level app instance (uvicorn 直接拿)
app = create_app()


def run_daemon(host: str = DAEMON_HOST, port: int = DAEMON_PORT) -> None:
    """启动 daemon (前台 blocking)."""
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
