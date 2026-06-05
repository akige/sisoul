# sisoul daemon autostart

Install / start / uninstall sisoul daemon on Linux (systemd) or Mac (launchd).

## Install

```bash
bash ops/init/install-autostart.sh
```

This copies the service unit / plist file to the user's autostart directory.

## Activate (first time, Mac only)

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.sisoul.daemon.plist
```

Linux activates automatically via `--now`.

## Restart (atomic)

```bash
# Linux
systemctl --user restart sisoul-daemon

# Mac (atomic kill+restart, KeepAlive resurrects)
launchctl kickstart -k gui/$(id -u)/com.sisoul.daemon
```

## Status / logs

```bash
# Linux
systemctl --user status sisoul-daemon
journalctl --user -u sisoul-daemon -f

# Mac
launchctl list | grep sisoul
tail -f ~/.sisoul/logs/daemon.err.log
```

## Uninstall

Linux:

```bash
systemctl --user disable --now sisoul-daemon
rm ~/.config/systemd/user/sisoul-daemon.service
systemctl --user daemon-reload
```

Mac (intentionally manual to avoid plist corruption — known issue with bootout that overwrites plist as JSON array):

1. Remove plist file: `rm ~/Library/LaunchAgents/com.sisoul.daemon.plist`
2. Kill running daemon: `pkill -f "sisoul daemon start"` (KeepAlive cannot resurrect after plist removed)

## Resource limits

`sisoul-daemon.service`:
- `MemoryMax=2G` (foundation daemon ~400MB)
- `CPUQuota=200%` (2 cores max)
