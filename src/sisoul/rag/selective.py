"""sisoul rag · selective inject (Phase 2 P2-2).

按 frontmatter `sisoul_rag` 三态 + auto 启发式选 vault .md 文件 inject 进 context.

frontmatter key 值:
- "include": 强制 include (跳过 auto 启发)
- "exclude": 强制 exclude (跳过 auto 启发)
- "auto" (默认): mtime 近 N 天 + prompt keyword 命中 → include

设计要点:
- frontmatter parse 失败 → 当 auto (fail-open · 不打挂 daemon)
- vault dir 不存在 / 无 .md → 返回空 list (不抛错)
- prompt 空 → 仅靠 mtime + include 决定
- 公共函数都接 vault_dir Path (不写死 ~/.sisoul/)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sisoul.vault.frontmatter import load_frontmatter

# frontmatter key 名
RAG_FRONTMATTER_KEY = "sisoul_rag"

# auto 模式: mtime 近 N 天才纳入 (默认 30 天)
DEFAULT_AUTO_MTIME_DAYS = 30

# inject 的 context 总字符上限 (避免炸 prompt)
DEFAULT_MAX_CONTEXT_CHARS = 8000

# 三态值
MODE_INCLUDE = "include"
MODE_EXCLUDE = "exclude"
MODE_AUTO = "auto"
VALID_MODES = {MODE_INCLUDE, MODE_EXCLUDE, MODE_AUTO}


@dataclass(frozen=True)
class FileDecision:
    """单文件被纳入/排除的决策记录 (调试 / 验收用)."""

    path: Path
    mode: str  # include / exclude / auto
    included: bool
    reason: str


def _parse_mode(meta: dict[str, Any]) -> str:
    """读 frontmatter sisoul_rag 字段, normalize 成三态之一. 缺失 / 坏 → auto."""
    raw = meta.get(RAG_FRONTMATTER_KEY)
    if raw is None:
        return MODE_AUTO
    if not isinstance(raw, str):
        return MODE_AUTO
    val = raw.strip().lower()
    if val in VALID_MODES:
        return val
    return MODE_AUTO


def _safe_meta_and_body(path: Path) -> tuple[dict[str, Any], str]:
    """读 + parse frontmatter, 失败 fail-open ({}, '')."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}, ""
    try:
        meta, body = load_frontmatter(raw)
        return meta, body
    except Exception:
        return {}, raw


def _extract_keywords(prompt: str) -> set[str]:
    """从 prompt 提取关键词 (>= 3 字符 alnum/中文 token, 去停用词).

    朴素实现: 切词 + 去通用词 (在 / 的 / a / the 等). 命中即 keyword match.
    """
    if not prompt:
        return set()
    # 切 [a-zA-Z0-9_]+ 或 CJK 连续段
    tokens = re.findall(r"[a-zA-Z0-9_]{3,}|[一-鿿]{2,}", prompt.lower())
    stop = {
        "the", "and", "for", "with", "you", "are", "this", "that", "have",
        "from", "but", "not", "what", "how", "why", "when", "where", "who",
        "怎么", "如何", "什么", "为什么", "可以", "需要",
    }
    return {t for t in tokens if t not in stop}


def _auto_should_include(
    path: Path,
    body: str,
    meta: dict[str, Any],
    keywords: set[str],
    *,
    mtime_days: int,
    now_ts: float | None = None,
) -> tuple[bool, str]:
    """auto 模式启发: mtime 近 N 天 OR keyword 命中 (title / tags / body 前 4KB)."""
    now = now_ts if now_ts is not None else time.time()

    # mtime check
    try:
        mtime = path.stat().st_mtime
        age_days = (now - mtime) / 86400.0
        if age_days <= mtime_days:
            mtime_ok = True
        else:
            mtime_ok = False
    except OSError:
        return False, "stat-failed"

    # keyword check
    kw_hit = False
    kw_reason = ""
    if keywords:
        hay_parts = [
            str(meta.get("title") or ""),
            " ".join(str(t) for t in (meta.get("tags") or [])),
            body[:4096],  # 前 4KB body 足够 fingerprint
        ]
        hay = " ".join(hay_parts).lower()
        matched = [k for k in keywords if k in hay]
        if matched:
            kw_hit = True
            kw_reason = f"keyword:{matched[0]}"

    if mtime_ok and kw_hit:
        return True, f"auto-mtime+{kw_reason}"
    if mtime_ok and not keywords:
        # 无 prompt keyword → 仅 mtime 近 = include
        return True, "auto-mtime-only"
    if kw_hit:
        return True, f"auto-{kw_reason}"
    return False, "auto-no-match"


def _iter_vault_md(vault_dir: Path) -> list[Path]:
    """递归 list vault 下所有 .md (sorted by relative path)."""
    if not vault_dir.exists() or not vault_dir.is_dir():
        return []
    return sorted(vault_dir.rglob("*.md"))


def filter_files(
    vault_dir: Path,
    prompt: str = "",
    *,
    mtime_days: int = DEFAULT_AUTO_MTIME_DAYS,
    now_ts: float | None = None,
    return_decisions: bool = False,
) -> list[Path] | tuple[list[Path], list[FileDecision]]:
    """按 frontmatter `sisoul_rag` + auto 启发式选 vault .md 文件.

    Args:
        vault_dir: vault 根目录
        prompt: 用户 prompt (auto 模式 keyword 命中用)
        mtime_days: auto 模式 mtime 阈值 (天, 默认 30)
        now_ts: 测试用 frozen time
        return_decisions: True 时返回 (files, decisions) 全决策记录

    Returns:
        list[Path] (sorted) 或 tuple
    """
    paths = _iter_vault_md(Path(vault_dir))
    if not paths:
        return ([], []) if return_decisions else []

    keywords = _extract_keywords(prompt)
    selected: list[Path] = []
    decisions: list[FileDecision] = []

    for p in paths:
        meta, body = _safe_meta_and_body(p)
        mode = _parse_mode(meta)

        if mode == MODE_INCLUDE:
            selected.append(p)
            decisions.append(FileDecision(p, mode, True, "frontmatter-include"))
            continue
        if mode == MODE_EXCLUDE:
            decisions.append(FileDecision(p, mode, False, "frontmatter-exclude"))
            continue

        # auto
        ok, reason = _auto_should_include(
            p, body, meta, keywords,
            mtime_days=mtime_days, now_ts=now_ts,
        )
        if ok:
            selected.append(p)
        decisions.append(FileDecision(p, mode, ok, reason))

    if return_decisions:
        return selected, decisions
    return selected


def build_context(
    files: list[Path],
    *,
    max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> tuple[str, int]:
    """把选中文件拼成 context 文本 + 实际 char 数.

    truncate 到 max_chars 上限, 文件间用 '\\n\\n--- <path> ---\\n\\n' 分隔.
    """
    if not files:
        return "", 0
    chunks: list[str] = []
    total = 0
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        header = f"\n\n--- {p.name} ---\n\n"
        piece = header + text
        if total + len(piece) > max_chars:
            remain = max_chars - total
            if remain > len(header) + 1:
                chunks.append(piece[:remain])
                total = max_chars
            break
        chunks.append(piece)
        total += len(piece)
    return "".join(chunks), total


def inject_context(
    prompt: str,
    vault_dir: Path,
    *,
    mtime_days: int = DEFAULT_AUTO_MTIME_DAYS,
    max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    now_ts: float | None = None,
) -> dict[str, Any]:
    """daemon endpoint 用入口: 返回 {selected_files, context_chars, filtered_count}.

    Returns:
        {
            "selected_files": [<relpath>],
            "context_chars": <int>,
            "filtered_count": <int 总扫了多少 .md>,
            "context": <str 拼好的 context>,
        }
    """
    vault = Path(vault_dir)
    selected, decisions = filter_files(
        vault, prompt,
        mtime_days=mtime_days, now_ts=now_ts,
        return_decisions=True,
    )  # type: ignore[misc]
    context, ctx_chars = build_context(selected, max_chars=max_chars)

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(vault))
        except ValueError:
            return str(p)

    return {
        "selected_files": [_rel(p) for p in selected],
        "context_chars": ctx_chars,
        "filtered_count": len(decisions),
        "context": context,
    }
