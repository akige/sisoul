#!/bin/bash
# sisoul SessionStart hook (P2-5 真协议层 adapter).
# 拉 sisoul daemon HTTP → inject Claude Code system prompt. fail-open silent.
#
# 安装路径: ~/.claude/hooks/sisoul_session_start.sh
# Claude Code hook 类型: SessionStart
# 配置 (~/.claude/settings.json):
#   "hooks": {
#     "SessionStart": [{"hooks": [{"type":"command","command":"~/.claude/hooks/sisoul_session_start.sh"}]}]
#   }
#
# 行为:
# - curl daemon /sisoul/preferences/list (JSON list) → jq 拼 <sisoul-preferences>
# - curl daemon /sisoul/goals/list      (JSON list) → jq 拼 <sisoul-long-term-goals>
# - daemon 不通 / curl 超时 / jq 失败 → silent exit 0 (不破坏 Claude Code 启动)
# - jq 缺失 → fallback python3 -c (mac 默认有)
#
# 模块 src/sisoul/sync/claude_cli_hook.py render_hook_script() 渲染同款脚本,
# 保持两边一致 (ops/hooks/ 是手工 reference 模板; install_hook 是 daemon 安装).

set -u
SISOUL_BASE="${SISOUL_BASE:-http://127.0.0.1:9876}"
TIMEOUT="${SISOUL_HOOK_TIMEOUT:-2}"

_fetch() {
    curl -sS --max-time "$TIMEOUT" "$1" 2>/dev/null
    return $?
}

_parse_list() {
    if command -v jq >/dev/null 2>&1; then
        jq -r '.[]? | "- **" + (.title // .id // "?") + "**: " + ((.body // .progress // "") | tostring | gsub("\n"; " "))' 2>/dev/null
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
        body = str(body).replace("\n", " ")
        print(f"- **{title}**: {body}")
except Exception:
    sys.exit(0)
' 2>/dev/null
    fi
}

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
