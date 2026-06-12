"""sisoul · 去中心化 AI 工作元层协议 (内测开发版).

§28 元层定位: 不替代 Claude CLI / Codex CLI / Pi CLI / Cursor / Aider / OpenCode,
而是在每设备跑后台 daemon, 接管底层 (vault / 身份 / 审计 / 灵魂迁移 / 跨设备 P2P / 朋友共享).

详 obs:
- §28 元层架构 + P2P 朋友共享设计
- §29 v1.0 开发执行计划
"""

__version__ = "1.0.0-alpha"
__name_full__ = "sisoul"
__phase__ = "Phase 5 v1.0-internal release"

# Daemon 端口 (Phase 1 W2 ship)
# 9876 选择理由: Mac 本机 + obs 跨机均空闲, 跟 9890 (svc-b) / 9878 (svc-a) /
# 9888 (svc-c) / 9892 (swarm-server-1.7) / 9893 (supervisor-1.7) 全不冲突.
DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 9876
DAEMON_BASE_URL = f"http://{DAEMON_HOST}:{DAEMON_PORT}"
