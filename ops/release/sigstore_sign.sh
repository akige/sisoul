#!/usr/bin/env bash
# sigstore_sign.sh — cosign sign-blob 给 sisoul-cli binary 签名
#
# 用法:
#   ops/release/sigstore_sign.sh <binary-path> [--key cosign.key.test]
#
# 默认行为:
#   - 不真签 release (没真 cosign keypair, 默认用 test keypair, 在 ops/release/.testkeys/)
#   - 真生成 .sig + .pem (cert chain) + .bundle (Sigstore "bundle" 格式包含
#     signature + cert + rekor inclusion proof, 1 文件可验)
#   - --keyless 才走 Fulcio OIDC + Rekor transparency log (真 release 用)
#
# 输出 (同目录, 跟 binary 并排):
#   <binary>.sig      detached signature (raw, base64)
#   <binary>.pem      signing certificate (keyless 模式才有)
#   <binary>.bundle   Sigstore bundle (推荐验证用)
#
# 退出码:
#   0  签名成功
#   1  签名失败 (cosign 错 / key 不存在)
#   2  参数错
#
# 配套:
#   build-binary.sh   先构建 binary
#   verify.sh         离线验签
#   install.sh        端到端 download + verify + install
#
# Used-by: Wave A #11

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY_DIR="${SCRIPT_DIR}/.testkeys"
TEST_KEY="${KEY_DIR}/cosign.key.test"
TEST_PUB="${SCRIPT_DIR}/cosign.pub"  # public key checked-in for verify.sh

# ---------- args ----------
BINARY=""
MODE="test-key"     # test-key | keyless
KEY_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --key)      KEY_PATH="$2"; MODE="custom-key"; shift 2 ;;
        --keyless)  MODE="keyless"; shift ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        -*)
            echo "ERR: unknown flag $1"; exit 2
            ;;
        *)
            [[ -n "${BINARY}" ]] && { echo "ERR: extra arg $1"; exit 2; }
            BINARY="$1"
            shift
            ;;
    esac
done

[[ -z "${BINARY}" ]] && { echo "usage: $0 <binary-path> [--key path|--keyless]"; exit 2; }
[[ -f "${BINARY}" ]] || { echo "ERR: not a file: ${BINARY}"; exit 2; }

command -v cosign >/dev/null 2>&1 || { echo "ERR: cosign not in PATH; brew install cosign"; exit 1; }

# ---------- test keypair (auto-generate if missing) ----------
ensure_test_key() {
    if [[ -f "${TEST_KEY}" && -f "${TEST_PUB}" ]]; then
        return 0
    fi
    echo "==> generating test keypair (NOT for real releases) → ${KEY_DIR}/"
    mkdir -p "${KEY_DIR}"
    # cosign generate-key-pair prompts for passphrase; use COSIGN_PASSWORD=""
    (
        cd "${KEY_DIR}"
        rm -f cosign.key cosign.pub
        COSIGN_PASSWORD="" cosign generate-key-pair >/dev/null 2>&1
        mv cosign.key "${TEST_KEY}"
        # publish pubkey at the conventional location for install.sh / verify.sh
        mv cosign.pub "${TEST_PUB}"
    )
    echo "    test private key → ${TEST_KEY} (gitignored)"
    echo "    test public  key → ${TEST_PUB} (checked-in)"
}

# ---------- sign ----------
case "${MODE}" in
    test-key)
        ensure_test_key
        KEY_PATH="${TEST_KEY}"
        ;;
    custom-key)
        [[ -f "${KEY_PATH}" ]] || { echo "ERR: --key file not found: ${KEY_PATH}"; exit 2; }
        ;;
    keyless)
        : # uses Fulcio OIDC + Rekor; needs interactive browser / GH OIDC token
        ;;
esac

SIG="${BINARY}.sig"
BUNDLE="${BINARY}.bundle"
CERT="${BINARY}.pem"

echo "==> cosign sign-blob (${MODE})"
echo "    binary : ${BINARY}"
echo "    sig    : ${SIG}"
echo "    bundle : ${BUNDLE}"

case "${MODE}" in
    test-key|custom-key)
        # 用 test key 时不上 Rekor transparency log. cosign 3.x 改默认行为, 需要
        # 同时 --use-signing-config=false (旁路 sigstore signing-config) +
        # --tlog-upload=false (再次显式抑制). 真 release 走 --keyless 上 Rekor.
        COSIGN_PASSWORD="" cosign sign-blob \
            --yes \
            --key "${KEY_PATH}" \
            --use-signing-config=false \
            --tlog-upload=false \
            --output-signature "${SIG}" \
            --bundle "${BUNDLE}" \
            "${BINARY}"
        ;;
    keyless)
        cosign sign-blob \
            --yes \
            --output-signature "${SIG}" \
            --output-certificate "${CERT}" \
            --bundle "${BUNDLE}" \
            "${BINARY}"
        ;;
esac

echo "==> signed"
ls -la "${SIG}" "${BUNDLE}" 2>/dev/null || true
[[ -f "${CERT}" ]] && ls -la "${CERT}"

echo
echo "==> next: ops/release/verify.sh ${BINARY}"
