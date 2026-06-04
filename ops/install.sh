#!/usr/bin/env bash
# sisoul install.sh — release one-liner installer (P2-EF).
#
# 用法:
#   curl -sSfL https://github.com/sisoul/sisoul/releases/latest/download/install.sh | bash
#
# 流程:
#   1. uname 检测 OS / arch → 选 tarball
#   2. curl 拉 tarball + sigstore .sig (.bundle/.crt)
#   3. cosign verify-blob (若未装 cosign 提示装法)
#   4. 解到 ~/.local/bin/sisoul
#   5. 提示 PATH + 跑 `sisoul init` 开始用
#
# 环境变量 (覆盖):
#   SISOUL_VERSION       默认 latest
#   SISOUL_RELEASE_URL   默认 https://github.com/sisoul/sisoul/releases
#   SISOUL_INSTALL_DIR   默认 $HOME/.local/bin
#   SISOUL_SKIP_VERIFY   1 = 跳过 cosign 验签 (不推荐, dev/test 用)
#   SISOUL_DRY_RUN       1 = 不真下载/解压, 走 mock 流 (ops/test-install.sh 用)
#
# 退出码:
#   0 装好    1 OS 不支持    2 缺依赖    3 验签失败    4 下载失败

set -euo pipefail

# ── 颜色 ────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { printf '%b[sisoul]%b %s\n' "$GREEN" "$NC" "$*"; }
warn() { printf '%b[sisoul WARN]%b %s\n' "$YELLOW" "$NC" "$*"; }
err()  { printf '%b[sisoul ERROR]%b %s\n' "$RED" "$NC" "$*" >&2; }

# ── env ─────────────────────────────────────────────────────
SISOUL_VERSION="${SISOUL_VERSION:-latest}"
SISOUL_RELEASE_URL="${SISOUL_RELEASE_URL:-https://github.com/sisoul/sisoul/releases}"
SISOUL_INSTALL_DIR="${SISOUL_INSTALL_DIR:-$HOME/.local/bin}"
SISOUL_SKIP_VERIFY="${SISOUL_SKIP_VERIFY:-0}"
SISOUL_DRY_RUN="${SISOUL_DRY_RUN:-0}"

# ── 1. OS / arch 检测 ──────────────────────────────────────
detect_target() {
    local uname_s uname_m os arch
    uname_s="$(uname -s)"
    uname_m="$(uname -m)"
    case "$uname_s" in
        Darwin)   os="darwin"  ;;
        Linux)    os="linux"   ;;
        *)
            err "不支持的 OS: $uname_s (只支持 Darwin / Linux)"
            exit 1
            ;;
    esac
    case "$uname_m" in
        x86_64|amd64)   arch="amd64" ;;
        arm64|aarch64)  arch="arm64" ;;
        *)
            err "不支持的 arch: $uname_m (只支持 amd64 / arm64)"
            exit 1
            ;;
    esac
    printf '%s-%s\n' "$os" "$arch"
}

TARGET="$(detect_target)"
log "target = $TARGET version = $SISOUL_VERSION"

# ── 2. 依赖检查 (curl + tar 必需, cosign 可选) ─────────────
check_deps() {
    local missing=0
    for cmd in curl tar uname; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            err "缺依赖: $cmd"
            missing=1
        fi
    done
    if [ "$missing" -ne 0 ]; then
        exit 2
    fi

    if [ "$SISOUL_SKIP_VERIFY" = "1" ]; then
        warn "SISOUL_SKIP_VERIFY=1 — 跳过 sigstore 验签 (不推荐生产用)"
        return
    fi

    if ! command -v cosign >/dev/null 2>&1; then
        warn "cosign 未装 — 装法:"
        warn "  macOS:  brew install cosign"
        warn "  Linux:  sudo apt install cosign  (或下 https://github.com/sigstore/cosign/releases)"
        warn "装完重跑本脚本, 或 SISOUL_SKIP_VERIFY=1 临时跳过 (不推荐)"
        exit 2
    fi
}

check_deps

# ── 3. 拉 tarball + 验签 ───────────────────────────────────
if [ "$SISOUL_VERSION" = "latest" ]; then
    BASE_URL="$SISOUL_RELEASE_URL/latest/download"
else
    BASE_URL="$SISOUL_RELEASE_URL/download/$SISOUL_VERSION"
fi

TARBALL="sisoul-${TARGET}.tar.gz"
SIGFILE="${TARBALL}.sig"
CERTFILE="${TARBALL}.crt"

TMPDIR="$(mktemp -d -t sisoul-install.XXXXXX)"
trap 'rm -rf "$TMPDIR"' EXIT

cd "$TMPDIR"

log "下载 $BASE_URL/$TARBALL"
if [ "$SISOUL_DRY_RUN" = "1" ]; then
    log "(DRY_RUN) 跳过真下载, 写 mock tarball"
    # mock: 建一个含可执行 sisoul 占位的 tar.gz
    mkdir -p mockbin
    cat > mockbin/sisoul <<'MOCKEOF'
#!/usr/bin/env bash
echo "sisoul (dry-run mock build)"
MOCKEOF
    chmod +x mockbin/sisoul
    tar -czf "$TARBALL" -C mockbin sisoul
    : > "$SIGFILE"
    : > "$CERTFILE"
else
    if ! curl -sSfL -o "$TARBALL" "$BASE_URL/$TARBALL"; then
        err "下载 tarball 失败: $BASE_URL/$TARBALL"
        exit 4
    fi
    if [ "$SISOUL_SKIP_VERIFY" != "1" ]; then
        curl -sSfL -o "$SIGFILE" "$BASE_URL/$SIGFILE" || {
            err "下载 sig 失败"
            exit 4
        }
        curl -sSfL -o "$CERTFILE" "$BASE_URL/$CERTFILE" || {
            err "下载 cert 失败"
            exit 4
        }
    fi
fi

# 验签
if [ "$SISOUL_SKIP_VERIFY" != "1" ] && [ "$SISOUL_DRY_RUN" != "1" ]; then
    log "cosign verify-blob $TARBALL"
    if ! cosign verify-blob \
            --certificate "$CERTFILE" \
            --signature "$SIGFILE" \
            --certificate-identity-regexp '.*' \
            --certificate-oidc-issuer-regexp '.*' \
            "$TARBALL" >/dev/null 2>&1; then
        err "sigstore 验签失败 — release artifact 可能被篡改, 不装"
        exit 3
    fi
    log "✓ sigstore 验签通过"
else
    log "(skip verify)"
fi

# ── 4. 解压 + 装到 ~/.local/bin/sisoul ─────────────────────
mkdir -p "$SISOUL_INSTALL_DIR"
tar -xzf "$TARBALL" -C "$TMPDIR"

if [ ! -f "$TMPDIR/sisoul" ]; then
    err "tarball 解出来没有 sisoul binary"
    exit 4
fi

install -m 0755 "$TMPDIR/sisoul" "$SISOUL_INSTALL_DIR/sisoul"
log "✓ 装到 $SISOUL_INSTALL_DIR/sisoul"

# ── 5. PATH 提示 + next steps ──────────────────────────────
case ":$PATH:" in
    *":$SISOUL_INSTALL_DIR:"*)
        log "PATH 已含 $SISOUL_INSTALL_DIR"
        ;;
    *)
        warn "PATH 未含 $SISOUL_INSTALL_DIR — 加这行到 ~/.bashrc 或 ~/.zshrc:"
        warn "  export PATH=\"$SISOUL_INSTALL_DIR:\$PATH\""
        ;;
esac

echo ""
echo "sisoul init 开始用"
echo ""
exit 0
