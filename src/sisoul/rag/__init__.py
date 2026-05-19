"""sisoul rag 模块 (Phase 2 P2-2).

RAG selective inject: 决定 vault 里哪些 .md 该被 inject 进 context.

frontmatter `sisoul_rag` 三态:
- "include": 强制 include
- "exclude": 强制 exclude
- "auto" (默认): 走启发式 (mtime 近 N 天 / prompt keyword 命中)

公共 API:
- filter_files(vault_dir, prompt, ...) → list[Path]
- inject_context(prompt, vault_dir, ...) → dict (selected_files / context_chars / filtered_count)
"""

from __future__ import annotations

from sisoul.rag.selective import (
    DEFAULT_AUTO_MTIME_DAYS,
    DEFAULT_MAX_CONTEXT_CHARS,
    RAG_FRONTMATTER_KEY,
    build_context,
    filter_files,
    inject_context,
)

__all__ = [
    "RAG_FRONTMATTER_KEY",
    "DEFAULT_AUTO_MTIME_DAYS",
    "DEFAULT_MAX_CONTEXT_CHARS",
    "filter_files",
    "build_context",
    "inject_context",
]
