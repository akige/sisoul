#!/usr/bin/env bash
# Build Sisoul.app menu-bar tray via py2app.
#
# Output: dist/Sisoul.app  (drag to /Applications or double-click in Finder)
#
# Re-run safe: cleans build/ and dist/ first.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

PYTHON="${PYTHON:-/Users/as/sisoul-dev/.venv/bin/python}"

if [[ ! -x "$PYTHON" ]]; then
  echo "ERROR: python not found at $PYTHON" >&2
  echo "  set PYTHON=/path/to/python and retry, or create venv with: python3 -m venv .venv" >&2
  exit 2
fi

# dep check
"$PYTHON" -c "import rumps, py2app" 2>/dev/null || {
  echo "INFO: installing rumps + py2app into $PYTHON..."
  "$PYTHON" -m pip install --quiet rumps py2app
}

echo "==> clean build/ dist/"
rm -rf build dist

echo "==> py2app build (this takes ~30-90s)"
"$PYTHON" setup.py py2app --no-strip 2>&1 | tail -40

APP_PATH="$HERE/dist/Sisoul.app"

if [[ ! -d "$APP_PATH" ]]; then
  echo "ERROR: build failed, $APP_PATH 不存在" >&2
  exit 1
fi

echo ""
echo "==> 真验证 .app bundle 完整性"
test -f "$APP_PATH/Contents/Info.plist" || { echo "FAIL: no Info.plist" >&2; exit 1; }
test -f "$APP_PATH/Contents/MacOS/Sisoul" || { echo "FAIL: no Contents/MacOS/Sisoul launcher" >&2; exit 1; }
ls -la "$APP_PATH/Contents/MacOS/" | head -5
echo ""
echo "size: $(du -sh "$APP_PATH" | cut -f1)"

echo ""
echo "==> 试跑 --version (truly invoking launcher)"
"$APP_PATH/Contents/MacOS/Sisoul" --version || {
  echo "WARN: --version 返回非 0; 继续"
}

echo ""
echo "==> done"
echo ""
echo "Run with:  open $APP_PATH"
echo "Or from terminal:  $APP_PATH/Contents/MacOS/Sisoul"
echo ""
echo "Drag dist/Sisoul.app to /Applications to install."
