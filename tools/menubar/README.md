# Sisoul menu-bar tray (macOS native)

一个 rumps + py2app 写的 macOS menu-bar tray, 套在 `sisoul` daemon 外面。
顶部 menu bar 显示 daemon 状态 (🟢 Online / 🔴 Offline), 菜单提供 daemon 启停、加朋友、问 founder、借朋友 LLM 等常用动作。

## 截图描述

启动后, macOS 屏幕右上角 menu bar 出现一个文字图标 `S•` (或带 .icns 时是 sisoul logo)。
点开下拉菜单:

```
🟢 Online · v1.0.0-alpha · 1 endpoint
Start daemon
Stop daemon
─────────────
Add friend...
Recent friends ▸
  @akige  (2026-06-09)
  bob     (2026-06-06)
─────────────
Ask founder...
Borrow LLM...
─────────────
Open dashboard
Open INSTALL docs
─────────────
About Sisoul Tray v0.1.0
Quit
```

`S•` 后面的点会跟着 daemon online 状态实时变 (`•` = online, `·` = offline), 默认每 10 秒刷一次。

## 装机

### 方案 A: 开发态直接跑 (推荐先用这个验收)

```bash
# 假设你已经 clone sisoul-dev 到 ~/sisoul-dev, 且有 venv
cd ~/sisoul-dev
.venv/bin/python -m pip install rumps py2app httpx
.venv/bin/python tools/menubar/sisoul_tray.py
```

第一次跑会进 macOS Accessibility 提示, 允许后 menu bar 图标会出来。

测试不开 GUI (CI 友好):

```bash
.venv/bin/python tools/menubar/sisoul_tray.py --self-check
# 期望 JSON 输出: daemon_online / friends_count / sisoul_bin 路径
```

### 方案 B: 打成 `.app` 双击跑 (生产)

```bash
cd ~/sisoul-dev/tools/menubar
bash build_app.sh
# 等 30~90s, 产物在 dist/Sisoul.app

open dist/Sisoul.app
# 或拖到 /Applications:
cp -R dist/Sisoul.app /Applications/
```

之后 Finder / Spotlight 搜 "Sisoul" 双击就跑, 不依赖 venv。

### Makefile 快捷

```bash
make install      # pip install rumps py2app httpx
make dev          # 前台跑 (调试用, Ctrl-C 退)
make self-check   # 不开 GUI, 打印 daemon + friends 状态
make app          # build dist/Sisoul.app
make open         # build + open
make clean        # rm build/ dist/
```

## 菜单项说明

| 菜单 | 行为 |
|---|---|
| `🟢 Online · v… · N endpoint` | 静态显示, 不可点; 每 10s 刷新 |
| `Start daemon` | `sisoul daemon start` (nohup, log → `~/.sisoul/logs/daemon.log`) |
| `Stop daemon` | `kill -SIGTERM <pid>` → fallback `sisoul daemon stop` |
| `Add friend...` | native dialog 输 `@username` 或 `did:key:...` → 跑 `sisoul friend add <name>` |
| `Recent friends ▸` | 读 `~/.sisoul/identity/didkey_friends.json`, 列前 5, 点项弹 DID 详情 |
| `Ask founder...` | dialog 输 prompt → `sisoul founder chat <prompt>` → 弹结果窗 |
| `Borrow LLM...` | 两步 dialog (friend + prompt) → `sisoul borrow run <friend> <prompt>` |
| `Open dashboard` | 浏览器开 https://akige.github.io/sisoul/ |
| `Open INSTALL docs` | 浏览器开 INSTALL.md |
| `About` | 显示版本 / sisoul CLI 路径 / log 路径 |
| `Quit` | 退出 tray (不影响 daemon) |

## 依赖

- macOS 10.13+ (py2app 12 要求)
- Python 3.10+ (sisoul venv 是 3.12)
- `rumps>=0.4` — menu-bar app framework (BSD)
- `pyobjc-framework-Cocoa>=12` — rumps 拉
- `httpx>=0.27` — daemon health 探针 (sisoul-dev 主依赖)
- `py2app>=0.28` — 仅 build `.app` 时需要

sisoul CLI 路径用 `shutil.which('sisoul')` 找; fallback `~/.local/bin/sisoul`、`/opt/homebrew/bin/sisoul`、`/usr/local/bin/sisoul`。

也可在 `pyproject.toml` 里走 extras:

```bash
pip install -e '.[menubar]'
```

## 日志 / 故障排查

- tray 自身 log: `~/.sisoul/logs/menubar_tray.log`
- daemon log (经 tray 启停时): `~/.sisoul/logs/daemon.log`
- daemon health endpoint: `http://127.0.0.1:9876/sisoul/health`

如果菜单 status item 一直显示 `🔴 Offline` 但 `curl 127.0.0.1:9876/sisoul/health` 是 200:
- 看 `~/.sisoul/logs/menubar_tray.log` 是否有 httpx 错误
- 端口被防火墙拦 / 不是 127.0.0.1 (可改 `DAEMON_HEALTH_URL` 顶部常量)

如果 `Add friend...` / `Ask founder...` 没反应:
- 看 `~/.sisoul/logs/menubar_tray.log` 是否记录 `RUN /Users/as/.local/bin/sisoul friend add ...`
- subprocess 超时 30s/60s/90s/120s, 拉太久会 timeout=124, 调常量 `timeout=`

## 已知限制

- daemon health endpoint 当前只暴露 `status_ok`, 不暴露 peer count; 菜单显示 `N endpoint` 替代 (拉 `sisoul net status` 太重 + 阻塞 UI)
- `Recent friends` 子菜单更新需 tray 重启 (rumps 重建菜单 hack, 不影响主要功能)
- macOS 第一次跑会弹 Accessibility 授权; 跳过就跑, 不影响 menu-bar (只影响 osascript automation)

## 卸载

```bash
# tray 进程
pkill -f sisoul_tray.py
pkill -f Sisoul.app

# .app
rm -rf /Applications/Sisoul.app
# 项目目录
rm -rf ~/sisoul-dev/tools/menubar/build ~/sisoul-dev/tools/menubar/dist

# log
rm -f ~/.sisoul/logs/menubar_tray.log
```
