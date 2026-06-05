#!/usr/bin/env bash
# Install sisoul daemon as system autostart service.
# Linux: systemd user service. Mac: launchd LaunchAgent.
# Uninstall: see ops/init/README.md (intentionally manual).
set -euo pipefail

OS="$(uname -s)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$OS" in
    Linux)
        mkdir -p "$HOME/.config/systemd/user"
        cp "$SCRIPT_DIR/sisoul-daemon.service" "$HOME/.config/systemd/user/"
        systemctl --user daemon-reload
        systemctl --user enable --now sisoul-daemon
        echo "OK sisoul-daemon enabled via systemd user."
        systemctl --user status sisoul-daemon --no-pager | head -5
        ;;
    Darwin)
        mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.sisoul/logs"
        sed "s|/Users/USER|$HOME|g" "$SCRIPT_DIR/com.sisoul.daemon.plist" > "$HOME/Library/LaunchAgents/com.sisoul.daemon.plist"
        echo "OK plist installed at ~/Library/LaunchAgents/com.sisoul.daemon.plist"
        echo "  Activate: launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.sisoul.daemon.plist"
        echo "  Restart:  launchctl kickstart -k gui/\$(id -u)/com.sisoul.daemon"
        echo "  Logs:     ~/.sisoul/logs/daemon.{out,err}.log"
        echo "  Uninstall: see ops/init/README.md"
        ;;
    *)
        echo "ERROR: unsupported OS: $OS"
        exit 1
        ;;
esac
