#!/usr/bin/env bash
# Install the sisoul founder-agent daemon (@founder) as a systemd system service.
#
# Substitutes the __USER__ / __HOME__ placeholders in
# sisoul-founder-daemon.service for the *invoking* user, then installs +
# enables the unit. Idempotent. Requires sudo for the system unit dir.
#
# Usage:
#   ops/init/install-founder-daemon.sh            # install + enable + start
#   DRY_RUN=1 ops/init/install-founder-daemon.sh  # print rendered unit, do nothing
#
# Uninstall (manual, intentional):
#   sudo systemctl disable --now sisoul-founder-daemon
#   sudo rm /etc/systemd/system/sisoul-founder-daemon.service
#   sudo systemctl daemon-reload
set -euo pipefail

OS="$(uname -s)"
if [ "$OS" != "Linux" ]; then
    echo "ERROR: this installer is Linux/systemd only (got: $OS)." >&2
    echo "       On macOS use the launchd plist (ops/init/sisoul-founder-daemon.plist)." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/sisoul-founder-daemon.service"
UNIT_NAME="sisoul-founder-daemon.service"
DEST="/etc/systemd/system/$UNIT_NAME"

# Target user/home = the real (non-sudo) invoking user when possible.
TARGET_USER="${SUDO_USER:-$USER}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
TARGET_HOME="${TARGET_HOME:-$HOME}"

if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: template not found: $TEMPLATE" >&2
    exit 1
fi

RENDERED="$(sed -e "s|__USER__|${TARGET_USER}|g" -e "s|__HOME__|${TARGET_HOME}|g" "$TEMPLATE")"

if [ -n "${DRY_RUN:-}" ]; then
    echo "# DRY_RUN — rendered unit (user=$TARGET_USER home=$TARGET_HOME):"
    echo "$RENDERED"
    exit 0
fi

echo "$RENDERED" | sudo tee "$DEST" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now "$UNIT_NAME"
echo "OK $UNIT_NAME installed (user=$TARGET_USER home=$TARGET_HOME)."
sudo systemctl status "$UNIT_NAME" --no-pager | head -5 || true
