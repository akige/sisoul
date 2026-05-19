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

    return app


# Module-level app instance (uvicorn 直接拿)
app = create_app()


def run_daemon(host: str = DAEMON_HOST, port: int = DAEMON_PORT) -> None:
    """启动 daemon (前台 blocking)."""
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
