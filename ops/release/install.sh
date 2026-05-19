#!/usr/bin/env sh
# install.sh — sisoul-cli one-line installer
#
# 用户:
#   curl -sSL https://sisoul.io/install.sh | sh
#   curl -sSL https://sisoul.io/install.sh | sh -s -- --version 1.0.0
#   curl -sSL https://sisoul.io/install.sh | sh -s -- --dest ~/bin
#
# 行为 (端到端):
#   1. 检测 OS/ARCH (darwin/linux × arm64/x86_64)
#   2. 选 release tag (默认 VERSION 文件里的 latest)
#   3. 下载 binary + .bundle + cosign.pub
#      - 主源: GitHub release (https://github.com/sisoul/sisoul-cli/releases)
#      - 备源: IPFS gateway (https://ipfs.io/ipfs/<CID>), CID 来自 ENS contenthash
#               sisoul-cli.eth (由 Wave B #1/#8 写入)
#   4. cosign verify-blob 验签 (失败 → abort, 不安装)
#   5. 安装到 ~/.local/bin/sisoul (或 --dest)
#   6. 跑 sisoul --version sanity check
#
# 强制 POSIX sh (不假设 bash 在 /bin). install.sh 写完后用
# `sh -n install.sh` 校验语法.
#
# 退出码:
#   0 安装成功 + --version OK
#   1 下载失败 / 验签失败 / 安装失败
#   2 不支持的 OS/ARCH / 缺 curl|cosign
#
# Used-by: Wave A #11 (sigstore signed install path)

set -eu

# ---------- defaults ----------
SISOUL_VERSION="${SISOUL_VERSION:-}"       # empty → fetch latest from VERSION endpoint
SISOUL_DEST="${SISOUL_DEST:-${HOME}/.local/bin}"
SISOUL_GH_REPO="${SISOUL_GH_REPO:-sisoul/sisoul-cli}"
SISOUL_GH_BASE="${SISOUL_GH_BASE:-https://github.com/${SISOUL_GH_REPO}/releases/download}"
SISOUL_IPFS_GATEWAY="${SISOUL_IPFS_GATEWAY:-https://ipfs.io/ipfs}"
SISOUL_ENS_NAME="${SISOUL_ENS_NAME:-sisoul-cli.eth}"
SISOUL_PUBKEY_URL="${SISOUL_PUBKEY_URL:-https://raw.githubusercontent.com/${SISOUL_GH_REPO}/main/ops/release/cosign.pub}"
SISOUL_VERIFY="${SISOUL_VERIFY:-1}"        # 0 = skip cosign verify (DANGEROUS, dev only)
SISOUL_DRY_RUN="${SISOUL_DRY_RUN:-0}"
SISOUL_INSECURE_IGNORE_TLOG="${SISOUL_INSECURE_IGNORE_TLOG:-1}"  # 1 for test keypair; flip to 0 for real keyless

# ---------- args parse ----------
while [ $# -gt 0 ]; do
    case "$1" in
        --version) SISOUL_VERSION="$2"; shift 2 ;;
        --dest)    SISOUL_DEST="$2"; shift 2 ;;
        --no-verify) SISOUL_VERIFY=0; shift ;;
        --dry-run) SISOUL_DRY_RUN=1; shift ;;
        --keyless) SISOUL_INSECURE_IGNORE_TLOG=0; shift ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *) echo "ERR: unknown arg: $1" >&2; exit 2 ;;
    esac
done

# ---------- log helpers ----------
log()  { printf '==> %s\n' "$*" >&2; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die()  { printf 'ERR: %s\n' "$*" >&2; exit "${2:-1}"; }

# ---------- detect OS/ARCH ----------
detect_platform() {
    UNAME_S="$(uname -s)"
    UNAME_M="$(uname -m)"
    case "${UNAME_S}" in
        Darwin) OS="darwin" ;;
        Linux)  OS="linux"  ;;
        *) die "unsupported OS: ${UNAME_S}" 2 ;;
    esac
    case "${UNAME_M}" in
        arm64|aarch64) ARCH="arm64" ;;
        x86_64|amd64)  ARCH="x86_64" ;;
        *) die "unsupported ARCH: ${UNAME_M}" 2 ;;
    esac
    log "platform: ${OS}/${ARCH}"
}

# ---------- prereqs ----------
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing required command: $1" 2; }

# ---------- version resolution ----------
resolve_version() {
    if [ -n "${SISOUL_VERSION}" ]; then
        log "version (from flag/env): ${SISOUL_VERSION}"
        return 0
    fi
    # Resolve latest via GH API
    log "resolving latest version from GitHub..."
    if command -v curl >/dev/null 2>&1; then
        # /latest returns 404 for draft-only releases; fallback to /releases
        SISOUL_VERSION="$(curl -fsSL "https://api.github.com/repos/${SISOUL_GH_REPO}/releases/latest" 2>/dev/null \
            | sed -n 's/.*"tag_name": *"v\{0,1\}\([^"]*\)".*/\1/p' \
            | head -1 || true)"
    fi
    if [ -z "${SISOUL_VERSION}" ]; then
        # Last-resort fallback for offline / mock scenarios
        SISOUL_VERSION="1.0.0+internal"
        warn "could not resolve latest tag, falling back to ${SISOUL_VERSION}"
    fi
    log "version: ${SISOUL_VERSION}"
}

# ---------- download with fallback ----------
download() {
    src_url="$1"
    dst_path="$2"

    if curl -fsSL --max-time 30 -o "${dst_path}" "${src_url}" 2>/dev/null; then
        return 0
    fi
    return 1
}

resolve_ipfs_cid() {
    # Wave B #1/#8 will populate ENS contenthash → CID for current release.
    # For now: best-effort fetch from a config endpoint, otherwise empty.
    if [ -n "${SISOUL_IPFS_CID:-}" ]; then
        printf '%s\n' "${SISOUL_IPFS_CID}"
        return 0
    fi
    log "ENS contenthash resolution for ${SISOUL_ENS_NAME} not yet wired (Wave B #1/#8)"
    printf '%s\n' ""
}

# ---------- main ----------
main() {
    need_cmd uname
    need_cmd curl
    need_cmd mkdir
    need_cmd chmod

    detect_platform
    resolve_version

    OUT_NAME="sisoul-${SISOUL_VERSION}-${OS}-${ARCH}"
    TMP="$(mktemp -d)"
    trap 'rm -rf "${TMP}"' EXIT INT TERM

    BIN_URL="${SISOUL_GH_BASE}/v${SISOUL_VERSION}/${OUT_NAME}"
    SIG_URL="${BIN_URL}.bundle"
    PUB_URL="${SISOUL_PUBKEY_URL}"

    BIN_LOCAL="${TMP}/${OUT_NAME}"
    SIG_LOCAL="${TMP}/${OUT_NAME}.bundle"
    PUB_LOCAL="${TMP}/cosign.pub"

    if [ "${SISOUL_DRY_RUN}" = "1" ]; then
        log "[dry-run] would download:"
        log "    BIN: ${BIN_URL}"
        log "    SIG: ${SIG_URL}"
        log "    PUB: ${PUB_URL}"
        log "[dry-run] would install to: ${SISOUL_DEST}/sisoul"
        exit 0
    fi

    log "downloading binary..."
    if ! download "${BIN_URL}" "${BIN_LOCAL}"; then
        warn "GitHub download failed, trying IPFS mirror..."
        CID="$(resolve_ipfs_cid)"
        if [ -n "${CID}" ]; then
            if download "${SISOUL_IPFS_GATEWAY}/${CID}/${OUT_NAME}" "${BIN_LOCAL}"; then
                log "fetched from IPFS gateway"
            else
                die "all download mirrors failed"
            fi
        else
            die "GitHub download failed and no IPFS CID configured"
        fi
    fi
    chmod +x "${BIN_LOCAL}"

    if [ "${SISOUL_VERIFY}" = "1" ]; then
        log "downloading signature bundle..."
        download "${SIG_URL}" "${SIG_LOCAL}" || die "failed to download ${SIG_URL}"

        log "downloading cosign pubkey..."
        download "${PUB_URL}" "${PUB_LOCAL}" || die "failed to download ${PUB_URL}"

        need_cmd cosign

        log "cosign verify-blob..."
        TLOG_FLAG=""
        if [ "${SISOUL_INSECURE_IGNORE_TLOG}" = "1" ]; then
            TLOG_FLAG="--insecure-ignore-tlog=true"
        fi
        if ! cosign verify-blob \
            --key "${PUB_LOCAL}" \
            --bundle "${SIG_LOCAL}" \
            ${TLOG_FLAG} \
            "${BIN_LOCAL}" >&2
        then
            die "signature verification FAILED — refusing to install"
        fi
        log "signature: Verified OK"
    else
        warn "cosign verification SKIPPED (SISOUL_VERIFY=0)"
    fi

    log "installing → ${SISOUL_DEST}/sisoul"
    mkdir -p "${SISOUL_DEST}"
    mv "${BIN_LOCAL}" "${SISOUL_DEST}/sisoul"
    chmod +x "${SISOUL_DEST}/sisoul"

    log "sanity --version"
    VER_OUT="$("${SISOUL_DEST}/sisoul" --version 2>&1 | head -1 || true)"
    log "    ${VER_OUT}"
    case "${VER_OUT}" in
        "sisoul ${SISOUL_VERSION}"*) : ;;
        *) die "post-install --version mismatch: got '${VER_OUT}' want prefix 'sisoul ${SISOUL_VERSION}'" 1 ;;
    esac

    log "done. add to PATH if missing:"
    log "    export PATH=\"${SISOUL_DEST}:\$PATH\""
}

main "$@"
