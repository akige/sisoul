#!/usr/bin/env bash
# build-binary.sh — sisoul-cli single-binary builder (PyInstaller)
#
# 用 PyInstaller 把 Python sisoul CLI 打成 single-file binary, 用于
# Wave A #11 GitHub release + install.sh + sigstore 签名链路.
#
# 输出:
#   ops/release/dist/sisoul-${VERSION}-${OS}-${ARCH}        binary
#   ops/release/dist/sisoul-${VERSION}-${OS}-${ARCH}.sha256 sha256sum
#
# 平台:
#   - mac arm64  (脚本本机直接 build, host build only — cross-compile not supported by PyInstaller)
#   - linux x86_64 / linux arm64 → 在对应 host 上跑本脚本 (CI matrix)
#
# 约束:
#   - 输出 binary < 50MB (剔 numpy/pandas/grpc/web3 等无关 deps, 见 EXCLUDE_MODULES)
#   - exit 1 if size > 50MB
#   - 退出前 sanity check: ./binary --version == "sisoul ${VERSION}"
#
# 配套:
#   sigstore_sign.sh   对 binary 签名
#   install.sh         curl|sh 下载 + verify + 安装
#   verify.sh          离线 cosign 验签
#
# Used-by: Wave A #11 (sigstore + GitHub release + install.sh)

set -euo pipefail

# ---------- args / env ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENTRY="${SCRIPT_DIR}/sisoul-entry.py"
DIST_DIR="${SCRIPT_DIR}/dist"
BUILD_DIR="${SCRIPT_DIR}/.pyinstaller-build"

VERSION="$(cat "${REPO_ROOT}/VERSION" | tr -d '[:space:]')"
[[ -z "${VERSION}" ]] && { echo "ERR: VERSION file empty"; exit 2; }

# OS/ARCH normalize (matches GitHub release naming)
case "$(uname -s)" in
    Darwin)  OS="darwin" ;;
    Linux)   OS="linux"  ;;
    *) echo "ERR: unsupported OS $(uname -s)"; exit 2 ;;
esac
case "$(uname -m)" in
    arm64|aarch64) ARCH="arm64" ;;
    x86_64|amd64)  ARCH="x86_64" ;;
    *) echo "ERR: unsupported ARCH $(uname -m)"; exit 2 ;;
esac

OUT_NAME="sisoul-${VERSION}-${OS}-${ARCH}"
MAX_SIZE_MB=50

# ---------- python venv ----------
VENV_PY="${SISOUL_VENV_PY:-${REPO_ROOT}/.venv/bin/python}"
if [[ ! -x "${VENV_PY}" ]]; then
    echo "ERR: venv python not at ${VENV_PY}"
    echo "    set SISOUL_VENV_PY=<path-to-python-3.11+> or run 'uv venv && uv sync' first"
    exit 2
fi

PYV="$("${VENV_PY}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "${PYV}" in
    3.11|3.12|3.13|3.14) : ;;
    *) echo "ERR: python ${PYV} not in [3.11, 3.14]"; exit 2 ;;
esac

# Ensure pyinstaller is installed
if ! "${VENV_PY}" -c 'import PyInstaller' 2>/dev/null; then
    echo "WARN: PyInstaller not in venv, installing via uv pip..."
    (cd "${REPO_ROOT}" && uv pip install pyinstaller >/dev/null 2>&1) || {
        echo "ERR: failed to install pyinstaller"; exit 2
    }
fi

# ---------- excludes ----------
# Big optional deps that get pulled in transitively but aren't needed in the
# default `sisoul` CLI surface. Pinned list, reviewed for v1.0-internal.
EXCLUDE_MODULES=(
    # daemon-only
    fastapi uvicorn starlette anyio
    # llm-only (delivered via daemon/agent path, not CLI binary)
    anthropic openai google ollama
    # data science (none of sisoul CLI uses these)
    numpy pandas matplotlib scipy sklearn pyarrow
    # GUI
    PyQt5 PyQt6 PySide6 tkinter
    # interactive
    IPython jupyter notebook ipykernel
    # dev/test
    pytest mypy ruff coverage hypothesis
    # transitive bloat (web3/aiortc/grpc)
    googleapiclient googleapi google.api google.cloud
    av grpc grpc_tools zeroconf aiohttp aiortc aioice
    ens eth_utils eth_account eth_abi eth_hash eth_typing eth_keyfile web3
    Crypto pylibsrtp ckzg bitarray cytoolz
    # mac signing (host-only, not bundled)
    win32com pythonwin
)

# Hidden imports we MUST keep (PyInstaller's analyzer misses these)
HIDDEN_IMPORTS=(
    mnemonic
    nacl
    nacl.bindings
    nacl.signing
    nacl.encoding
    nacl.public
    nacl.secret
    pydantic
    pydantic_core
    yaml
    typer
    click
    httpx
    frontmatter
)

# ---------- build ----------
echo "==> sisoul-cli binary build"
echo "    version : ${VERSION}"
echo "    os/arch : ${OS}/${ARCH}"
echo "    python  : ${PYV} (${VENV_PY})"
echo "    out     : ${DIST_DIR}/${OUT_NAME}"

mkdir -p "${DIST_DIR}"
rm -rf "${BUILD_DIR}"
rm -f  "${SCRIPT_DIR}/sisoul.spec"

PYINSTALLER_ARGS=(
    --onefile
    --name sisoul
    --paths "${REPO_ROOT}/src"
    --collect-submodules sisoul
    --console
    --noconfirm
    --distpath "${DIST_DIR}/_raw"
    --workpath "${BUILD_DIR}"
    --specpath "${BUILD_DIR}"
)

for m in "${EXCLUDE_MODULES[@]}"; do
    PYINSTALLER_ARGS+=(--exclude-module "${m}")
done
for m in "${HIDDEN_IMPORTS[@]}"; do
    PYINSTALLER_ARGS+=(--hidden-import "${m}")
done

# Strip symbols on Linux (further size reduction; Mac strip is unsafe with codesign)
if [[ "${OS}" == "linux" ]]; then
    PYINSTALLER_ARGS+=(--strip)
fi

"${VENV_PY}" -m PyInstaller "${PYINSTALLER_ARGS[@]}" "${ENTRY}" >"${BUILD_DIR}.log" 2>&1 || {
    echo "ERR: pyinstaller failed; last 30 lines of log:"
    tail -30 "${BUILD_DIR}.log" || true
    exit 1
}

# Rename to versioned name
RAW="${DIST_DIR}/_raw/sisoul"
[[ -f "${RAW}" ]] || { echo "ERR: pyinstaller did not produce ${RAW}"; exit 1; }

OUT_BIN="${DIST_DIR}/${OUT_NAME}"
mv -f "${RAW}" "${OUT_BIN}"
rm -rf "${DIST_DIR}/_raw"
chmod +x "${OUT_BIN}"

# ---------- verify ----------
SIZE_BYTES="$(stat -f%z "${OUT_BIN}" 2>/dev/null || stat -c%s "${OUT_BIN}")"
SIZE_MB=$(( SIZE_BYTES / 1024 / 1024 ))

echo "==> built: ${OUT_BIN} (${SIZE_MB}MB)"

if (( SIZE_MB > MAX_SIZE_MB )); then
    echo "ERR: binary size ${SIZE_MB}MB exceeds budget ${MAX_SIZE_MB}MB"
    echo "    revise EXCLUDE_MODULES in build-binary.sh"
    exit 1
fi

# sanity: --version
ACTUAL_VER="$("${OUT_BIN}" --version 2>&1 | head -1 || true)"
EXPECTED_VER="sisoul ${VERSION}"
if [[ "${ACTUAL_VER}" != ${EXPECTED_VER}* ]]; then
    echo "ERR: --version mismatch"
    echo "    expected: ${EXPECTED_VER}*"
    echo "    got     : ${ACTUAL_VER}"
    exit 1
fi
echo "    sanity --version OK: ${ACTUAL_VER}"

# sha256
SHA_FILE="${OUT_BIN}.sha256"
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${OUT_BIN}" | awk '{print $1}' > "${SHA_FILE}"
else
    shasum -a 256 "${OUT_BIN}" | awk '{print $1}' > "${SHA_FILE}"
fi
echo "    sha256 → $(cat "${SHA_FILE}")  ${SHA_FILE}"

echo "==> done"
echo "    next: ops/release/sigstore_sign.sh ${OUT_BIN}"
