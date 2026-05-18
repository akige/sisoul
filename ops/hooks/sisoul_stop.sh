#!/bin/bash
# sisoul Stop hook — session 结束写 session summary + 评估长期目标进度
#
# 安装路径: ~/.claude/hooks/sisoul_stop.sh
# Claude Code hook 类型: Stop
# 配置 (~/.claude/settings.json 里):
#   "hooks": {
#     "Stop": [{"hooks": [{"type":"command","command":"~/.claude/hooks/sisoul_stop.sh"}]}]
#   }
#
# Claude Code 传入环境变量 (Claude Code hook spec §28 §2.2):
#   CLAUDE_SESSION_ID  — session id
#   CLAUDE_TURNS       — 本次 session turns 数
#   CLAUDE_DURATION    — session 总时长 (秒)
#
# 行为:
# - POST /sisoul/session-summary → 写 chat history 到 vault
# - daemon 不在线 → 静默 exit 0 (Stop hook 不能 block)

SISOUL_PORT="${SISOUL_PORT:-9876}"
SISOUL_BASE="http://127.0.0.1:${SISOUL_PORT}"
TIMEOUT=3  # Stop hook 可以稍长一点 (session 已结束)

SESSION_ID="${CLAUDE_SESSION_ID:-unknown}"
TURNS="${CLAUDE_TURNS:-0}"
DURATION="${CLAUDE_DURATION:-0}"

# 写 session summary (异步, 不等 daemon 响应)
curl -sS \
    --max-time "$TIMEOUT" \
    -X POST \
    "${SISOUL_BASE}/sisoul/session-summary" \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"${SESSION_ID}\",\"turns\":${TURNS},\"duration\":${DURATION}}" \
    2>/dev/null &

# goal-progress 评估 (daemon 内 LLM 算对长期目标贡献度, 异步)
curl -sS \
    --max-time "$TIMEOUT" \
    -X POST \
    "${SISOUL_BASE}/sisoul/goal-progress" \
    -H "Content-Type: application/json" \
    -d "{\"session_id\":\"${SESSION_ID}\"}" \
    2>/dev/null &

# 等后台请求完成 (最多 TIMEOUT 秒, 不卡 terminal)
wait 2>/dev/null

exit 0
