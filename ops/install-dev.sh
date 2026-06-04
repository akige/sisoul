#!/usr/bin/env bash
# sisoul install.sh — 装机脚本 (Mac launchd + Linux systemd)
#
# 用法:
#   bash ops/install.sh
#   # 或:
#   curl -sS https://... | bash  (v1.0-internal release 后)
#
# 功能:
#   1. 检测 OS (macOS / Linux)
#   2. 建 ~/.sisoul/ vault dir
#   3. uv venv .venv (在 ~/.sisoul/venv/) + uv pip install sisoul (或 -e . 开发模式)
#   4. Mac: 复制 launchd plist → ~/Library/LaunchAgents/, 替换 <USER>, launchctl load
#   5. Linux: 复制 systemd unit → ~/.config/systemd/user/, systemctl --user enable + start
#   6. 输出 next steps
#
# 注意:
#   - 绝不真 install 到 ~/.claude/hooks/ 自动 (用户手动 cp, 安全理由)
#   - 本脚本幂等: 重跑不报错 (mkdir -p / launchctl load 安全)
#   - 开发模式 (SISOUL_DEV=1): 用 uv pip install -e . (本地 dev/ 目录)

set -euo pipefail

# ── 颜色 ────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[sisoul]${NC} $*"; }
warn() { echo -e "${YELLOW}[sisoul WARN]${NC} $*"; }
err()  { echo -e "${RED}[sisoul ERROR]${NC} $*" >&2; }

# ── 环境变量 (可覆盖) ────────────────────────────────────────
SISOUL_VAULT_DIR="${SISOUL_VAULT_DIR:-$HOME/.sisoul}"
SISOUL_VENV_DIR="${SISOUL_VENV_DIR:-$SISOUL_VAULT_DIR/venv}"
SISOUL_PORT="${SISOUL_PORT:-9876}"
SISOUL_DEV="${SISOUL_DEV:-0}"  # 1 = 开发模式 (pip install -e .)

# 脚本所在目录 (ops/ 下)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 项目根目录 (ops/ 的上一级)
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ── OS 检测 ──────────────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Darwin)  echo "macos" ;;
        Linux)   echo "linux" ;;
        *)       echo "unknown" ;;
    esac
}

OS="$(detect_os)"
USER_NAME="$(whoami)"

log "sisoul install 开始 (OS=$OS, user=$USER_NAME)"

# ── 检查依赖 ─────────────────────────────────────────────────
check_deps() {
    local missing=0

    if ! command -v python3 &>/dev/null; then
        err "python3 未找到 — 请先装 Python 3.11+"
        missing=1
    else
        PY_VER="$(python3 -c 'import sys; print(sys.version_info[:2])')"
        log "python3 版本: $PY_VER"
    fi

    # uv 检查 (优先用 uv, 回退 pip)
    if command -v uv &>/dev/null; then
        INSTALLER="uv"
        log "使用 uv 安装依赖"
    elif command -v pip3 &>/dev/null || command -v pip &>/dev/null; then
        INSTALLER="pip"
        warn "uv 未找到, 回退 pip (推荐装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh)"
    else
        err "uv 和 pip 都没找到 — 请先装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
        missing=1
    fi

    if [[ "$missing" -ne 0 ]]; then
        exit 1
    fi
}

check_deps

# ── 1. 建 vault 目录 ─────────────────────────────────────────
log "建 vault dir: $SISOUL_VAULT_DIR"
mkdir -p \
    "$SISOUL_VAULT_DIR" \
    "$SISOUL_VAULT_DIR/preferences" \
    "$SISOUL_VAULT_DIR/goals" \
    "$SISOUL_VAULT_DIR/chat-history"

# ── 2. 装 Python 包 ──────────────────────────────────────────
install_python_package() {
    log "建 venv: $SISOUL_VENV_DIR"

    if [[ "$INSTALLER" == "uv" ]]; then
        uv venv "$SISOUL_VENV_DIR" --python python3
        if [[ "$SISOUL_DEV" == "1" ]]; then
            log "开发模式: uv pip install -e ."
            (cd "$PROJECT_DIR" && uv pip install --python "$SISOUL_VENV_DIR/bin/python" -e ".[daemon,crypto]")
        else
            log "生产模式: uv pip install sisoul[daemon,crypto]"
            uv pip install --python "$SISOUL_VENV_DIR/bin/python" "sisoul[daemon,crypto]"
        fi
    else
        # pip fallback
        python3 -m venv "$SISOUL_VENV_DIR"
        local pip_bin="$SISOUL_VENV_DIR/bin/pip"
        "$pip_bin" install --upgrade pip --quiet
        if [[ "$SISOUL_DEV" == "1" ]]; then
            log "开发模式: pip install -e ."
            (cd "$PROJECT_DIR" && "$pip_bin" install -e ".[daemon,crypto]" --quiet)
        else
            "$pip_bin" install "sisoul[daemon,crypto]" --quiet
        fi
    fi

    log "sisoul 安装完成: $SISOUL_VENV_DIR/bin/sisoul"
}

install_python_package

SISOUL_BIN="$SISOUL_VENV_DIR/bin/sisoul"
if [[ ! -f "$SISOUL_BIN" ]]; then
    err "安装失败: $SISOUL_BIN 不存在"
    exit 1
fi

# ── 3. 装 daemon 自启 ────────────────────────────────────────
install_macos() {
    local plist_src="$SCRIPT_DIR/launchd/com.sisoul.daemon.plist"
    local launchd_dir="$HOME/Library/LaunchAgents"
    local plist_dst="$launchd_dir/com.sisoul.daemon.plist"

    if [[ ! -f "$plist_src" ]]; then
        err "launchd plist 模板不存在: $plist_src"
        exit 1
    fi

    mkdir -p "$launchd_dir"

    # 替换 SISOUL_USER 占位符 (plist 模板里用 SISOUL_USER 而非 <USER>, 避免 XML tag 冲突)
    log "安装 launchd plist → $plist_dst"
    sed "s|SISOUL_USER|$USER_NAME|g" "$plist_src" > "$plist_dst"
    chmod 644 "$plist_dst"

    # 卸载旧版本 (忽略错误)
    launchctl unload "$plist_dst" 2>/dev/null || true

    # 加载新版本
    launchctl load "$plist_dst"
    log "launchd plist 已加载: com.sisoul.daemon"

    # 建 log dir
    mkdir -p "$HOME/Library/Logs"
    log "日志路径: $HOME/Library/Logs/sisoul-daemon.{out,err}.log"
}

install_linux() {
    local service_src="$SCRIPT_DIR/systemd/sisoul-daemon.service"
    local systemd_dir="$HOME/.config/systemd/user"
    local service_dst="$systemd_dir/sisoul-daemon.service"

    if [[ ! -f "$service_src" ]]; then
        err "systemd service 模板不存在: $service_src"
        exit 1
    fi

    mkdir -p "$systemd_dir"

    log "安装 systemd unit → $service_dst"
    cp "$service_src" "$service_dst"
    chmod 644 "$service_dst"

    # 建 sisoul env config dir
    mkdir -p "$HOME/.config/sisoul"

    # reload daemon + enable + start
    systemctl --user daemon-reload
    systemctl --user enable sisoul-daemon
    systemctl --user start sisoul-daemon || {
        warn "systemd start 失败 (可能 venv 还没 sisoul 或 session 环境问题)"
        warn "手动跑: systemctl --user start sisoul-daemon"
    }

    log "systemd service 已启动: sisoul-daemon"
}

case "$OS" in
    macos)  install_macos ;;
    linux)  install_linux ;;
    *)
        warn "未知 OS: $OS — 跳过 daemon 自启安装"
        warn "手动跑: $SISOUL_BIN daemon --port $SISOUL_PORT"
        ;;
esac

# ── 4. 验证 daemon 响应 ──────────────────────────────────────
verify_daemon() {
    log "等待 daemon 启动 (最多 10 秒)..."
    local i=0
    while [[ $i -lt 10 ]]; do
        if curl -sS --max-time 1 "http://127.0.0.1:${SISOUL_PORT}/sisoul/health" &>/dev/null; then
            log "daemon 响应正常: http://127.0.0.1:${SISOUL_PORT}/sisoul/health"
            return 0
        fi
        sleep 1
        i=$((i+1))
    done
    warn "daemon 10 秒内未响应 — 可能正在启动中, 稍等再试"
    warn "curl http://127.0.0.1:${SISOUL_PORT}/sisoul/health"
    return 0  # 不报错, 只警告
}

verify_daemon

# ── 5. Next steps ────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
log "sisoul 安装完成!"
echo ""
echo "  下一步:"
echo ""
echo "  1. 初始化 vault:"
echo "     $SISOUL_BIN init"
echo ""
echo "  2. 接 LLM provider:"
echo "     $SISOUL_BIN login --provider claude"
echo ""
echo "  3. 同步到 Claude Code / Codex 等工具:"
echo "     $SISOUL_BIN sync"
echo ""
echo "  4. 可选 — 集成 Claude Code hook (手动 cp):"
echo "     cp $SCRIPT_DIR/hooks/sisoul_session_start.sh ~/.claude/hooks/"
echo "     cp $SCRIPT_DIR/hooks/sisoul_post_tool_use.sh ~/.claude/hooks/"
echo "     cp $SCRIPT_DIR/hooks/sisoul_stop.sh          ~/.claude/hooks/"
echo "     # 然后在 ~/.claude/settings.json 里注册这 3 个 hook"
echo "     # 详 ops/hooks/README-hooks.md"
echo ""
echo "  5. daemon 状态:"
echo "     curl http://127.0.0.1:${SISOUL_PORT}/sisoul/health"
echo "     $SISOUL_BIN status"
echo ""
echo "  详: sisoul --help"
echo "════════════════════════════════════════════════════════"
