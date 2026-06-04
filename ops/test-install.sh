#!/usr/bin/env bash
# ops/test-install.sh — install.sh 的 dry-run 烟测.
#
# 用法:
#   bash ops/test-install.sh
#
# 不真去 github 下载, 走 SISOUL_DRY_RUN=1 + SISOUL_SKIP_VERIFY=1 路径
# 验证 install.sh 能跑完、装出一个可执行 mock 到临时 install dir.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_SH="$SCRIPT_DIR/install.sh"

if [ ! -x "$INSTALL_SH" ]; then
    echo "FAIL: $INSTALL_SH 不存在或不可执行" >&2
    exit 1
fi

TMPDIR="$(mktemp -d -t sisoul-test-install.XXXXXX)"
trap 'rm -rf "$TMPDIR"' EXIT

export SISOUL_DRY_RUN=1
export SISOUL_SKIP_VERIFY=1
export SISOUL_INSTALL_DIR="$TMPDIR/bin"
export SISOUL_RELEASE_URL="file:///tmp/mock-sisoul-releases"

if ! bash "$INSTALL_SH" > "$TMPDIR/out.log" 2>&1; then
    echo "FAIL: install.sh dry-run exit != 0" >&2
    cat "$TMPDIR/out.log" >&2
    exit 1
fi

if [ ! -x "$SISOUL_INSTALL_DIR/sisoul" ]; then
    echo "FAIL: $SISOUL_INSTALL_DIR/sisoul 没装出来" >&2
    cat "$TMPDIR/out.log" >&2
    exit 1
fi

OUT="$("$SISOUL_INSTALL_DIR/sisoul" 2>&1 || true)"
if ! printf '%s' "$OUT" | grep -q "sisoul"; then
    echo "FAIL: mock binary 跑起来输出不含 'sisoul': $OUT" >&2
    exit 1
fi

if ! grep -q "sisoul init 开始用" "$TMPDIR/out.log"; then
    echo "FAIL: install.sh 输出没含 next-steps 提示" >&2
    cat "$TMPDIR/out.log" >&2
    exit 1
fi

echo "PASS: install.sh dry-run + mock binary 装好 + next-steps 提示完整"
exit 0
