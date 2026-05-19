"""sisoul daemon · RAG selective inject endpoints (Phase 2 P2-2).

POST /sisoul/rag/inject — 接 prompt, 返回 selective 选中文件 + context.
GET  /sisoul/rag/preview — debug 用, list 全 decisions.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from sisoul.rag.selective import (
    DEFAULT_AUTO_MTIME_DAYS,
    DEFAULT_MAX_CONTEXT_CHARS,
    filter_files,
    inject_context,
)
from sisoul.vault.storage import DEFAULT_VAULT_DIR


def _resolve_vault_root(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get("SISOUL_VAULT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_VAULT_DIR


class RagInjectRequest(BaseModel):
    """POST /sisoul/rag/inject body."""

    prompt: str = Field(default="", description="用户 prompt, auto 模式 keyword 命中用")
    vault: str | None = Field(default=None, description="覆盖 vault root")
    mtime_days: int = Field(default=DEFAULT_AUTO_MTIME_DAYS, ge=0, le=3650)
    max_chars: int = Field(default=DEFAULT_MAX_CONTEXT_CHARS, ge=0, le=1_000_000)
    include_context: bool = Field(default=False, description="True 时响应里带完整 context 文本")


class RagInjectResponse(BaseModel):
    selected_files: list[str]
    context_chars: int
    filtered_count: int
    context: str = ""  # include_context=True 才填


def create_router() -> APIRouter:
    """RAG router (test + 真 daemon 都用)."""
    router = APIRouter(prefix="/sisoul/rag", tags=["rag"])

    @router.post("/inject", response_model=RagInjectResponse)
    def rag_inject(req: RagInjectRequest) -> RagInjectResponse:
        root = _resolve_vault_root(req.vault)
        result = inject_context(
            req.prompt, root,
            mtime_days=req.mtime_days,
            max_chars=req.max_chars,
        )
        return RagInjectResponse(
            selected_files=result["selected_files"],
            context_chars=result["context_chars"],
            filtered_count=result["filtered_count"],
            context=result["context"] if req.include_context else "",
        )

    @router.get("/preview")
    def rag_preview(
        prompt: str = Query(default=""),
        vault: str | None = Query(default=None),
        mtime_days: int = Query(default=DEFAULT_AUTO_MTIME_DAYS, ge=0, le=3650),
    ) -> dict[str, Any]:
        root = _resolve_vault_root(vault)
        selected, decisions = filter_files(  # type: ignore[misc]
            root, prompt, mtime_days=mtime_days, return_decisions=True,
        )
        return {
            "vault_root": str(root),
            "selected_count": len(selected),
            "decisions": [
                {
                    "path": str(d.path.relative_to(root)) if root in d.path.parents else str(d.path),
                    "mode": d.mode,
                    "included": d.included,
                    "reason": d.reason,
                }
                for d in decisions
            ],
        }

    return router


rag_router = create_router()
