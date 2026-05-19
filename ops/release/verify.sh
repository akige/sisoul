#!/usr/bin/env bash
# verify.sh — cosign verify-blob, 离线验签 sisoul-cli binary
#
# 用法:
#   ops/release/verify.sh <binary-path>          # 用 ops/release/cosign.pub
#   ops/release/verify.sh <binary> --pub <pem>   # 自定义 pubkey
#   ops/release/verify.sh <binary> --keyless --identity <email> --issuer <oidc>
#
# 输出:
#   stdout: "Verified OK" 即通过
#   exit 0 通过, 1 失败
#
# 自动找配套文件:
#   <binary>.bundle   优先 (Sigstore bundle, 一文件搞定)
#   <binary>.sig      fallback (detached sig + 单独 pubkey)
#
# install.sh 内部也调本脚本, 保持验签逻辑单一来源.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PUB="${SCRIPT_DIR}/cosign.pub"

BINARY=""
MODE="key"          # key | keyless
PUBKEY="${DEFAULT_PUB}"
IDENTITY=""
ISSUER=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pub)       PUBKEY="$2"; shift 2 ;;
        --keyless)   MODE="keyless"; shift ;;
        --identity)  IDENTITY="$2"; shift 2 ;;
        --issuer)    ISSUER="$2"; shift 2 ;;
        -h|--help)   sed -n '2,18p' "$0"; exit 0 ;;
        -*)          echo "ERR: unknown flag $1"; exit 2 ;;
        *)
            [[ -n "${BINARY}" ]] && { echo "ERR: extra arg $1"; exit 2; }
            BINARY="$1"; shift
            ;;
    esac
done

[[ -z "${BINARY}" ]] && { echo "usage: $0 <binary-path>"; exit 2; }
[[ -f "${BINARY}" ]] || { echo "ERR: not a file: ${BINARY}"; exit 1; }

command -v cosign >/dev/null 2>&1 || { echo "ERR: cosign not in PATH"; exit 1; }

SIG="${BINARY}.sig"
BUNDLE="${BINARY}.bundle"

echo "==> cosign verify-blob (${MODE})"
echo "    binary : ${BINARY}"

# Prefer bundle (single-file, includes everything cosign needs).
if [[ -f "${BUNDLE}" ]]; then
    echo "    bundle : ${BUNDLE}"
    case "${MODE}" in
        key)
            [[ -f "${PUBKEY}" ]] || { echo "ERR: pubkey not found: ${PUBKEY}"; exit 1; }
            echo "    pubkey : ${PUBKEY}"
            # NOTE: --insecure-ignore-tlog only for test keypairs that didn't upload to Rekor.
            if cosign verify-blob \
                --key "${PUBKEY}" \
                --bundle "${BUNDLE}" \
                --insecure-ignore-tlog=true \
                "${BINARY}" 2>&1
            then
                echo "Verified OK"
                exit 0
            else
                echo "FAILED: cosign verify-blob (bundle, key)"
                exit 1
            fi
            ;;
        keyless)
            [[ -z "${IDENTITY}" || -z "${ISSUER}" ]] && {
                echo "ERR: --keyless requires --identity <email> --issuer <oidc>"
                exit 2
            }
            if cosign verify-blob \
                --bundle "${BUNDLE}" \
                --certificate-identity "${IDENTITY}" \
                --certificate-oidc-issuer "${ISSUER}" \
                "${BINARY}" 2>&1
            then
                echo "Verified OK"
                exit 0
            else
                echo "FAILED: cosign verify-blob (bundle, keyless)"
                exit 1
            fi
            ;;
    esac
fi

# Fall back to detached sig
if [[ -f "${SIG}" ]]; then
    echo "    sig    : ${SIG}"
    [[ -f "${PUBKEY}" ]] || { echo "ERR: pubkey not found: ${PUBKEY}"; exit 1; }
    if cosign verify-blob \
        --key "${PUBKEY}" \
        --signature "${SIG}" \
        --insecure-ignore-tlog=true \
        "${BINARY}" 2>&1
    then
        echo "Verified OK"
        exit 0
    fi
    echo "FAILED: cosign verify-blob (detached sig)"
    exit 1
fi

echo "ERR: neither ${BUNDLE} nor ${SIG} found"
exit 1
