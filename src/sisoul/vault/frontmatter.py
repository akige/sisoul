"""sisoul vault · markdown frontmatter 解析 (Phase 1 W3).

用 python-frontmatter (PyPI: python-frontmatter, import 名 `frontmatter`).
封装 2 个函数:
- load_frontmatter(text) → (dict, body_str)
- dump_frontmatter(meta, body) → str (含 --- YAML frontmatter)
"""

from __future__ import annotations

from typing import Any

import frontmatter as _fm


def load_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """parse markdown 文本 → (frontmatter dict, body str).

    无 frontmatter → ({}, original_text).
    """
    post = _fm.loads(text)
    # post.metadata 是 dict-like, body 是 str
    return dict(post.metadata), post.content


def dump_frontmatter(meta: dict[str, Any], body: str) -> str:
    """组装 → '---\\n<yaml>\\n---\\n\\n<body>\\n'.

    meta 空 dict 也强行写 '---\\n---' 头, 方便后续 reload.
    """
    post = _fm.Post(body, **meta)
    # frontmatter.dumps 默认 yaml + --- 包. 末尾不一定带 \n, 我们补一个.
    out = _fm.dumps(post)
    if not out.endswith("\n"):
        out += "\n"
    return out
