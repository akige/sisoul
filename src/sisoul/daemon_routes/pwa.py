"""sisoul daemon · PWA dashboard endpoints (Phase 2 W23-W28, dev-C ship).

只读 endpoints, 给 PWA dashboard (`pwa/`) 浏览 vault 用. 不做写入 (写入走 cli /
remember endpoint 后续 phase 加).

设计原则:
- 只读 (GET) · 写操作走专属 endpoint
- 路径全部前缀 /sisoul/
- 都接受 ?vault=<path> 覆盖 vault root (默认 $SISOUL_VAULT_ROOT 环境变量 / 不存在用
  ~/.sisoul/), 方便 test + 多 vault 调试
- 不读密码 / DID 私钥, 只读公开元信息

Endpoints (7):
- GET /sisoul/preferences/list      → 偏好 .md 列表 + 每条 metadata
- GET /sisoul/preferences/{id}      → 偏好单条 (frontmatter + body)
- GET /sisoul/goals/list            → 长期目标 .md 列表 + 进度
- GET /sisoul/goals/{id}            → 目标单条
- GET /sisoul/chat-history/list     → chat-history/<date>/*.md 列表
- GET /sisoul/chat-history/{date}/{session_id} → chat session 单条
- GET /sisoul/status/full           → vault 全状态 (size / 文件数 / DID / LLM provider)
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from sisoul import DAEMON_HOST, DAEMON_PORT, __phase__, __version__
from sisoul.vault.frontmatter import load_frontmatter
from sisoul.vault.storage import (
    DEFAULT_VAULT_DIR,
    VaultPaths,
    list_files,
    read_file,
    vault_size,
)

# Daemon 启动时间 (供 /status/full uptime)
_DAEMON_START_TS: float = time.time()


# ────────────────────────────────────────────────────────────
# Pydantic 响应模型
# ────────────────────────────────────────────────────────────


class PreferenceListItem(BaseModel):
    id: str  # 文件名去 .md
    path: str  # 相对 vault root
    title: str  # frontmatter.title 或 fallback id
    tags: list[str]
    updated: str | None  # frontmatter.updated / mtime ISO


class PreferenceDetail(BaseModel):
    id: str
    path: str
    frontmatter: dict[str, Any]
    body: str
    size_bytes: int


class GoalListItem(BaseModel):
    id: str
    path: str
    title: str
    progress: float  # 0.0 - 1.0
    status: str  # active / paused / done
    target_date: str | None
    updated: str | None


class GoalDetail(BaseModel):
    id: str
    path: str
    frontmatter: dict[str, Any]
    body: str
    progress: float
    size_bytes: int


class ChatHistoryListItem(BaseModel):
    date: str  # YYYY-MM-DD
    session_id: str
    path: str
    title: str
    message_count: int
    updated: str | None


class ChatHistorySessionDetail(BaseModel):
    date: str
    session_id: str
    path: str
    frontmatter: dict[str, Any]
    body: str
    size_bytes: int


class VaultStatusFull(BaseModel):
    vault_root: str
    vault_exists: bool
    vault_size_bytes: int
    counts: dict[str, int]  # preferences / goals / chat_sessions
    daemon: dict[str, Any]  # host / port / uptime_s / version / phase
    identity: dict[str, Any]  # did / handle / has_seed (dev-A 补)
    llm: dict[str, Any]  # provider / has_key (dev-B 补)


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────


def _resolve_vault_root(override: str | None = None) -> Path:
    """决定 vault root: query param > env > default ~/.sisoul/."""
    if override:
        return Path(override).expanduser().resolve()
    env = os.environ.get("SISOUL_VAULT_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_VAULT_DIR


def _mtime_iso(p: Path) -> str | None:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _safe_load(p: Path) -> tuple[dict[str, Any], str]:
    """读 + parse frontmatter, 失败返回 ({}, raw_text 或 '')."""
    try:
        raw = read_file(p)
    except (OSError, FileNotFoundError):
        return {}, ""
    try:
        meta, body = load_frontmatter(raw)
        return meta, body
    except Exception:
        return {}, raw


def _file_id(p: Path) -> str:
    """文件名去 .md (PWA 路由用)."""
    return p.stem


def _ensure_safe_id(file_id: str) -> None:
    """防 path traversal · 拒绝 .. / / 等."""
    if "/" in file_id or "\\" in file_id or ".." in file_id or file_id.startswith("."):
        raise HTTPException(status_code=400, detail=f"invalid id: {file_id!r}")


def _ensure_safe_date(date: str) -> None:
    """chat-history date 必须 YYYY-MM-DD."""
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid date: {date!r}") from e


def _did_status(vault_root: Path) -> dict[str, Any]:
    """读 dna.json (dev-A 写) 取 DID / handle / seed 存在标志.

    dna.json 不存在 / parse 失败 → 返回 {"did": None, "has_seed": False, "handle": None}.
    """
    dna_path = VaultPaths(vault_root).dna
    if not dna_path.exists():
        return {"did": None, "handle": None, "has_seed": False}
    try:
        data = json.loads(dna_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"did": None, "handle": None, "has_seed": False, "error": "dna.json parse failed"}
    return {
        "did": data.get("did"),
        "handle": data.get("handle"),
        "has_seed": bool(data.get("seed_fingerprint") or data.get("seed_encrypted")),
        "created": data.get("created"),
    }


def _llm_status() -> dict[str, Any]:
    """读 env / 配置看 LLM provider. PWA 只需知道哪家在用 + 是否有 key (不返 key)."""
    providers = []
    for env, name in [
        ("ANTHROPIC_API_KEY", "anthropic"),
        ("OPENAI_API_KEY", "openai"),
        ("GEMINI_API_KEY", "gemini"),
        ("OPENROUTER_API_KEY", "openrouter"),
    ]:
        if os.environ.get(env):
            providers.append(name)
    # ollama 本地不需要 key, 单独探
    has_ollama = bool(os.environ.get("OLLAMA_HOST")) or Path("/usr/local/bin/ollama").exists()
    if has_ollama:
        providers.append("ollama")
    primary = os.environ.get("SISOUL_LLM_PROVIDER") or (providers[0] if providers else None)
    return {
        "primary_provider": primary,
        "available_providers": providers,
        "has_any_key": len(providers) > 0,
    }


def _progress_from_meta(meta: dict[str, Any]) -> float:
    """从 frontmatter 读 progress (0-1 浮点 或 '50%' 字符串 或 done bool)."""
    if meta.get("status") == "done":
        return 1.0
    raw = meta.get("progress")
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        # 允许 0-1 或 0-100
        v = float(raw)
        if v > 1.0:
            v = v / 100.0
        return max(0.0, min(1.0, v))
    if isinstance(raw, str):
        s = raw.strip().rstrip("%").strip()
        try:
            v = float(s)
            if v > 1.0:
                v = v / 100.0
            return max(0.0, min(1.0, v))
        except ValueError:
            return 0.0
    return 0.0


# ────────────────────────────────────────────────────────────
# Router
# ────────────────────────────────────────────────────────────


def create_router() -> APIRouter:
    """构造 PWA APIRouter (test + 真 daemon 都用)."""
    router = APIRouter(prefix="/sisoul", tags=["pwa"])

    # ── preferences ──────────────────────────────────────────
    @router.get("/preferences/list", response_model=list[PreferenceListItem])
    def preferences_list(vault: str | None = Query(default=None)) -> list[PreferenceListItem]:
        root = _resolve_vault_root(vault)
        vp = VaultPaths(root)
        items: list[PreferenceListItem] = []
        for p in list_files(vp.preferences_dir, "*.md"):
            meta, _ = _safe_load(p)
            items.append(
                PreferenceListItem(
                    id=_file_id(p),
                    path=str(p.relative_to(root)) if root in p.parents else str(p),
                    title=str(meta.get("title") or _file_id(p)),
                    tags=list(meta.get("tags") or []),
                    updated=meta.get("updated") or _mtime_iso(p),
                )
            )
        return items

    @router.get("/preferences/{pref_id}", response_model=PreferenceDetail)
    def preferences_get(pref_id: str, vault: str | None = Query(default=None)) -> PreferenceDetail:
        _ensure_safe_id(pref_id)
        root = _resolve_vault_root(vault)
        p = VaultPaths(root).preferences_dir / f"{pref_id}.md"
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"preference not found: {pref_id}")
        meta, body = _safe_load(p)
        return PreferenceDetail(
            id=pref_id,
            path=str(p.relative_to(root)) if root in p.parents else str(p),
            frontmatter=meta,
            body=body,
            size_bytes=p.stat().st_size,
        )

    # ── goals ───────────────────────────────────────────────
    @router.get("/goals/list", response_model=list[GoalListItem])
    def goals_list(vault: str | None = Query(default=None)) -> list[GoalListItem]:
        root = _resolve_vault_root(vault)
        vp = VaultPaths(root)
        items: list[GoalListItem] = []
        for p in list_files(vp.goals_dir, "*.md"):
            meta, _ = _safe_load(p)
            items.append(
                GoalListItem(
                    id=_file_id(p),
                    path=str(p.relative_to(root)) if root in p.parents else str(p),
                    title=str(meta.get("title") or _file_id(p)),
                    progress=_progress_from_meta(meta),
                    status=str(meta.get("status") or "active"),
                    target_date=meta.get("target_date"),
                    updated=meta.get("updated") or _mtime_iso(p),
                )
            )
        return items

    @router.get("/goals/{goal_id}", response_model=GoalDetail)
    def goals_get(goal_id: str, vault: str | None = Query(default=None)) -> GoalDetail:
        _ensure_safe_id(goal_id)
        root = _resolve_vault_root(vault)
        p = VaultPaths(root).goals_dir / f"{goal_id}.md"
        if not p.exists():
            raise HTTPException(status_code=404, detail=f"goal not found: {goal_id}")
        meta, body = _safe_load(p)
        return GoalDetail(
            id=goal_id,
            path=str(p.relative_to(root)) if root in p.parents else str(p),
            frontmatter=meta,
            body=body,
            progress=_progress_from_meta(meta),
            size_bytes=p.stat().st_size,
        )

    # ── chat history ────────────────────────────────────────
    @router.get("/chat-history/list", response_model=list[ChatHistoryListItem])
    def chat_history_list(
        vault: str | None = Query(default=None),
        date: str | None = Query(default=None, description="可选 YYYY-MM-DD 过滤"),
        limit: int = Query(default=200, ge=1, le=2000),
    ) -> list[ChatHistoryListItem]:
        root = _resolve_vault_root(vault)
        chat_root = VaultPaths(root).chat_history_dir
        if not chat_root.exists():
            return []
        items: list[ChatHistoryListItem] = []
        date_dirs: list[Path]
        if date:
            _ensure_safe_date(date)
            d = chat_root / date
            date_dirs = [d] if d.is_dir() else []
        else:
            date_dirs = sorted(
                [d for d in chat_root.iterdir() if d.is_dir()],
                key=lambda x: x.name,
                reverse=True,
            )
        for d in date_dirs:
            for p in list_files(d, "*.md"):
                meta, body = _safe_load(p)
                msg_count = int(meta.get("message_count") or body.count("\n## ") or 0)
                items.append(
                    ChatHistoryListItem(
                        date=d.name,
                        session_id=_file_id(p),
                        path=str(p.relative_to(root)) if root in p.parents else str(p),
                        title=str(meta.get("title") or _file_id(p)),
                        message_count=msg_count,
                        updated=meta.get("updated") or _mtime_iso(p),
                    )
                )
                if len(items) >= limit:
                    return items
        return items

    @router.get(
        "/chat-history/{date}/{session_id}",
        response_model=ChatHistorySessionDetail,
    )
    def chat_history_get(
        date: str,
        session_id: str,
        vault: str | None = Query(default=None),
    ) -> ChatHistorySessionDetail:
        _ensure_safe_date(date)
        _ensure_safe_id(session_id)
        root = _resolve_vault_root(vault)
        p = VaultPaths(root).chat_history_dir / date / f"{session_id}.md"
        if not p.exists():
            raise HTTPException(
                status_code=404,
                detail=f"chat session not found: {date}/{session_id}",
            )
        meta, body = _safe_load(p)
        return ChatHistorySessionDetail(
            date=date,
            session_id=session_id,
            path=str(p.relative_to(root)) if root in p.parents else str(p),
            frontmatter=meta,
            body=body,
            size_bytes=p.stat().st_size,
        )

    # ── status full ────────────────────────────────────────
    @router.get("/status/full", response_model=VaultStatusFull)
    def status_full(vault: str | None = Query(default=None)) -> VaultStatusFull:
        root = _resolve_vault_root(vault)
        vp = VaultPaths(root)
        exists = root.exists()
        size = vault_size(root) if exists else 0
        prefs = len(list_files(vp.preferences_dir, "*.md")) if exists else 0
        goals = len(list_files(vp.goals_dir, "*.md")) if exists else 0
        chats = 0
        if exists and vp.chat_history_dir.exists():
            for d in vp.chat_history_dir.iterdir():
                if d.is_dir():
                    chats += len(list_files(d, "*.md"))
        return VaultStatusFull(
            vault_root=str(root),
            vault_exists=exists,
            vault_size_bytes=size,
            counts={
                "preferences": prefs,
                "goals": goals,
                "chat_sessions": chats,
            },
            daemon={
                "host": DAEMON_HOST,
                "port": DAEMON_PORT,
                "uptime_s": int(time.time() - _DAEMON_START_TS),
                "version": __version__,
                "phase": __phase__,
            },
            identity=_did_status(root),
            llm=_llm_status(),
        )

    return router


# Module-level router 实例 (主 daemon include_router 用)
router = create_router()
