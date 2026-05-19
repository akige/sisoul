"""sisoul sync · Claude CLI hook 真协议层 adapter (Phase 2 P2-5).

替换原 managed-section markdown 静态注入 (~/.claude/CLAUDE.md), 改成 runtime hook
脚本 (~/.claude/hooks/sisoul_session_start.sh) 启动时 curl 拉 daemon 注入 system prompt.

特点:
- daemon 在线: 拉 preferences/list + goals/list 拼成 <sisoul-preferences> / <sisoul-long-term-goals> XML 段
- daemon 离线 / 超时: silent exit 0 (不破坏 Claude Code 启动) — fail-open
- 钩子脚本依赖: curl + jq (mac/linux 默认就有)

公共 API:
- render_hook_script(daemon_url, timeout) → str (脚本内容)
- install_hook(target_path=None) → Path (安装)
- query_daemon_for_inject(...) → str (test-friendly: 直接拿 daemon HTTP 返结果拼输出)
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

DEFAULT_DAEMON_URL = "http://127.0.0.1:9876"
DEFAULT_TIMEOUT_S = 2
DEFAULT_HOOK_PATH = Path.home() / ".claude" / "hooks" / "sisoul_session_start.sh"


def render_hook_script(
    daemon_url: str = DEFAULT_DAEMON_URL,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> str:
    """渲染 hook bash 脚本内容.

    脚本行为:
    - curl daemon /sisoul/preferences/list (JSON list) → jq 解析 title + body 拼 <sisoul-preferences>
    - curl daemon /sisoul/goals/list (JSON list) → jq 解析 → <sisoul-long-term-goals>
    - daemon 不通 / curl 失败 → 静默 exit 0
    - 内置 fallback: jq 缺失 → 用 python3 -c 解析 (mac 默认有 python3)
    """
    # 用 single-quoted heredoc 形式拼, 防 bash 变量插值
    base = daemon_url.rstrip("/")
    return f"""#!/bin/bash
# sisoul SessionStart hook (P2-5 真协议层 adapter).
# 拉 daemon HTTP -> inject Claude Code system prompt. fail-open silent.
# 安装路径: ~/.claude/hooks/sisoul_session_start.sh

set -u
SISOUL_BASE="${{SISOUL_BASE:-{base}}}"
TIMEOUT="${{SISOUL_HOOK_TIMEOUT:-{timeout_s}}}"

_fetch() {{
    curl -sS --max-time "$TIMEOUT" "$1" 2>/dev/null
    return $?
}}

_parse_list() {{
    # stdin: JSON list of {{id,title,body / progress,...}}
    # stdout: bullet list lines
    if command -v jq >/dev/null 2>&1; then
        jq -r '.[]? | "- **" + (.title // .id // "?") + "**: " + ((.body // .progress // "") | tostring | gsub("\\n"; " "))' 2>/dev/null
    else
        python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    if not isinstance(data, list):
        sys.exit(0)
    for it in data:
        if not isinstance(it, dict):
            continue
        title = it.get("title") or it.get("id") or "?"
        body = it.get("body") or it.get("progress") or ""
        body = str(body).replace("\\n", " ")
        print(f"- **{{title}}**: {{body}}")
except Exception:
    sys.exit(0)
' 2>/dev/null
    fi
}}

PREFS_JSON=$(_fetch "$SISOUL_BASE/sisoul/preferences/list")
PREFS_RC=$?
if [[ $PREFS_RC -eq 0 && -n "$PREFS_JSON" ]]; then
    PREFS_BODY=$(printf '%s' "$PREFS_JSON" | _parse_list)
    if [[ -n "$PREFS_BODY" ]]; then
        echo "<sisoul-preferences>"
        echo "$PREFS_BODY"
        echo "</sisoul-preferences>"
    fi
fi

GOALS_JSON=$(_fetch "$SISOUL_BASE/sisoul/goals/list")
GOALS_RC=$?
if [[ $GOALS_RC -eq 0 && -n "$GOALS_JSON" ]]; then
    GOALS_BODY=$(printf '%s' "$GOALS_JSON" | _parse_list)
    if [[ -n "$GOALS_BODY" ]]; then
        echo "<sisoul-long-term-goals>"
        echo "$GOALS_BODY"
        echo "</sisoul-long-term-goals>"
    fi
fi

exit 0
"""


def install_hook(
    target_path: Path | None = None,
    *,
    daemon_url: str = DEFAULT_DAEMON_URL,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    overwrite: bool = True,
) -> Path:
    """把 hook 脚本写到 ~/.claude/hooks/sisoul_session_start.sh (或自定义路径).

    Args:
        target_path: 自定义路径 (test 用)
        daemon_url: daemon base URL
        timeout_s: curl --max-time 秒
        overwrite: False 时已存在不覆盖

    Returns:
        实际写入的路径
    """
    target = Path(target_path) if target_path else DEFAULT_HOOK_PATH
    if target.exists() and not overwrite:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    content = render_hook_script(daemon_url=daemon_url, timeout_s=timeout_s)
    target.write_text(content, encoding="utf-8")
    # chmod +x
    cur = target.stat().st_mode
    target.chmod(cur | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def query_daemon_for_inject(
    daemon_url: str = DEFAULT_DAEMON_URL,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> str:
    """python 直接拉 daemon 拼 system prompt 文本 (test-friendly).

    Returns:
        多行字符串 (<sisoul-preferences>...). daemon 不通 / 空 → 返回 "".
    """
    try:
        import httpx
    except ImportError:
        return ""

    out: list[str] = []
    try:
        client = httpx.Client(base_url=daemon_url.rstrip("/"), timeout=timeout_s)
    except Exception:
        return ""

    def _fetch(path: str) -> Any:
        try:
            r = client.get(path)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    try:
        prefs = _fetch("/sisoul/preferences/list")
        if isinstance(prefs, list) and prefs:
            lines = _format_items(prefs)
            if lines:
                out.append("<sisoul-preferences>")
                out.extend(lines)
                out.append("</sisoul-preferences>")
        goals = _fetch("/sisoul/goals/list")
        if isinstance(goals, list) and goals:
            lines = _format_items(goals)
            if lines:
                out.append("<sisoul-long-term-goals>")
                out.extend(lines)
                out.append("</sisoul-long-term-goals>")
    finally:
        try:
            client.close()
        except Exception:
            pass
    return "\n".join(out)


def _format_items(items: list[dict]) -> list[str]:
    """从 daemon JSON list 提 bullet 行 (跟 bash 脚本对齐)."""
    out: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = it.get("title") or it.get("id") or "?"
        body = it.get("body") or it.get("progress") or ""
        body = str(body).replace("\n", " ")
        out.append(f"- **{title}**: {body}")
    return out


__all__ = [
    "DEFAULT_DAEMON_URL",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_HOOK_PATH",
    "render_hook_script",
    "install_hook",
    "query_daemon_for_inject",
]
