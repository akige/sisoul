#!/bin/bash
# sisoul PostToolUse hook — destructive 操作 audit 写 sisoul daemon
#
# 安装路径: ~/.claude/hooks/sisoul_post_tool_use.sh
# Claude Code hook 类型: PostToolUse
# 配置 (~/.claude/settings.json 里):
#   "hooks": {
#     "PostToolUse": [{"hooks": [{"type":"command","command":"~/.claude/hooks/sisoul_post_tool_use.sh"}]}]
#   }
#
# Claude Code 传入环境变量 (Claude Code hook spec §28 §2.2):
#   TOOL_NAME    — 工具名 (Bash / Edit / Write / ...)
#   TOOL_INPUT   — 工具输入 (JSON string 或 raw)
#   TOOL_OUTPUT  — 工具输出 (可能很长, 建议 daemon 截断)
#   PROMPT_HASH  — 本次 prompt hash (用于去重)
#   CLAUDE_SESSION_ID — 本次 session id
#
# 行为:
# - 仅 TOOL_NAME=Bash 且输入含 destructive 关键词才写 audit
# - daemon 不在线 → 静默 exit 0 (不拦截 Claude Code)
# - 必须 exit 0 (非 0 会在 PostToolUse 阻断工具执行的某些场景)

SISOUL_PORT="${SISOUL_PORT:-9876}"
SISOUL_BASE="http://127.0.0.1:${SISOUL_PORT}"
TIMEOUT=1  # audit 写入超时 (异步, 不等结果)

# 只对 Bash 工具做 destructive 检测
if [[ "$TOOL_NAME" != "Bash" ]]; then
    exit 0
fi

# destructive 关键词正则 (§28 §2.2 spec)
DESTRUCTIVE_PATTERN="(rm |git reset|truncate|drop |DELETE |DROP TABLE|mkfs|dd if=|shred )"

if [[ "$TOOL_INPUT" =~ $DESTRUCTIVE_PATTERN ]]; then
    # 清理输入里的双引号防 JSON 注入 (简单 escape)
    SAFE_INPUT=$(echo "$TOOL_INPUT" | head -c 500 | sed 's/"/\\"/g' | tr -d '\n')
    SAFE_PROMPT_HASH="${PROMPT_HASH:-unknown}"
    SAFE_SESSION="${CLAUDE_SESSION_ID:-unknown}"

    curl -sS \
        --max-time "$TIMEOUT" \
        -X POST \
        "${SISOUL_BASE}/sisoul/audit" \
        -H "Content-Type: application/json" \
        -d "{\"tool\":\"${TOOL_NAME}\",\"input\":\"${SAFE_INPUT}\",\"prompt_hash\":\"${SAFE_PROMPT_HASH}\",\"session_id\":\"${SAFE_SESSION}\"}" \
        2>/dev/null &  # 后台异步, 不卡 Claude Code
fi

exit 0
