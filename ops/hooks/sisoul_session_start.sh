#!/bin/bash
# sisoul SessionStart hook — 拉最新偏好 + 长期目标 inject 进 Claude Code system prompt
#
# 安装路径: ~/.claude/hooks/sisoul_session_start.sh
# Claude Code hook 类型: SessionStart
# 配置 (~/.claude/settings.json 里):
#   "hooks": {
#     "SessionStart": [{"hooks": [{"type":"command","command":"~/.claude/hooks/sisoul_session_start.sh"}]}]
#   }
#
# 行为:
# - GET /sisoul/preferences → stdout (Claude Code 加进 system prompt)
# - GET /sisoul/long-term-goals → stdout (被 <sisoul-long-term-goals> 包裹)
# - daemon 不在线 → 静默 exit 0 (不破坏 Claude Code 启动)
#
# 注: sisoul daemon 必须已启动 (launchd/systemd 自启)
#     端口 9876 → ~/.sisoul/.env 里可覆盖 SISOUL_PORT

SISOUL_PORT="${SISOUL_PORT:-9876}"
SISOUL_BASE="http://127.0.0.1:${SISOUL_PORT}"
TIMEOUT=2  # daemon 响应超时 (秒), 不能卡 Claude Code 启动

# 拉偏好 (inject 进 system prompt)
PREFERENCES=$(curl -sS --max-time "$TIMEOUT" "${SISOUL_BASE}/sisoul/preferences" 2>/dev/null || echo "")
if [[ -n "$PREFERENCES" ]]; then
    echo "<sisoul-preferences>"
    echo "$PREFERENCES"
    echo "</sisoul-preferences>"
fi

# 拉长期目标
GOALS=$(curl -sS --max-time "$TIMEOUT" "${SISOUL_BASE}/sisoul/long-term-goals" 2>/dev/null || echo "")
if [[ -n "$GOALS" ]]; then
    echo "<sisoul-long-term-goals>"
    echo "$GOALS"
    echo "</sisoul-long-term-goals>"
fi

exit 0
