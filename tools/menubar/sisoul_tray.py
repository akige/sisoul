#!/usr/bin/env python3
"""
sisoul menu-bar tray app (macOS, rumps).

顶部 menu bar 显示 sisoul daemon 状态 (🟢 Online · N peers / 🔴 Offline),
菜单提供 start/stop daemon, add friend, recent friends, founder chat,
borrow LLM, open dashboard 等常用动作.

依赖: rumps, httpx.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

import rumps

# httpx 是 sisoul-dev 主依赖, 直接 import; 离线/未装也能跑 (用 urllib fallback)
try:
    import httpx  # type: ignore

    _HAS_HTTPX = True
except Exception:  # pragma: no cover
    _HAS_HTTPX = False
    import urllib.error
    import urllib.request


__version__ = "0.1.0"

# ---------- 常量 ----------
DAEMON_HEALTH_URL = "http://127.0.0.1:9876/sisoul/health"
DAEMON_REFRESH_SEC = 10
HTTP_TIMEOUT_SEC = 5.0
DASHBOARD_URL = "https://akige.github.io/sisoul/"
INSTALL_DOC_URL = "https://github.com/akige/sisoul/blob/main/docs/INSTALL.md"

FRIENDS_FILE = Path(os.path.expanduser("~/.sisoul/identity/didkey_friends.json"))
DAEMON_PID_FILE = Path(os.path.expanduser("~/.sisoul/daemon.pid"))
LOG_DIR = Path(os.path.expanduser("~/.sisoul/logs"))
TRAY_LOG = LOG_DIR / "menubar_tray.log"
DAEMON_LOG = LOG_DIR / "daemon.log"

# ---------- log ----------
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(TRAY_LOG),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("sisoul-tray")


# ---------- sisoul binary 解析 ----------
def resolve_sisoul_bin() -> str:
    """优先 PATH 上的 sisoul, 其次 ~/.local/bin/sisoul, 再 venv."""
    found = shutil.which("sisoul")
    if found:
        return found
    fallback = Path(os.path.expanduser("~/.local/bin/sisoul"))
    if fallback.exists():
        return str(fallback)
    # py2app bundle 跑的时候 PATH 被清空, 加常见路径
    for cand in [
        "/Users/as/.local/bin/sisoul",
        "/opt/homebrew/bin/sisoul",
        "/usr/local/bin/sisoul",
    ]:
        if Path(cand).exists():
            return cand
    raise FileNotFoundError("sisoul CLI 没找到 (PATH / ~/.local/bin / homebrew 都不在)")


# ---------- HTTP helper ----------
def http_get_json(url: str, timeout: float = HTTP_TIMEOUT_SEC) -> Optional[dict]:
    if _HAS_HTTPX:
        try:
            r = httpx.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.debug("httpx GET %s failed: %s", url, e)
            return None
    # fallback
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.debug("urllib GET %s failed: %s", url, e)
        return None


# ---------- daemon 控制 ----------
def daemon_health() -> tuple[bool, str]:
    """
    Return (online, summary_str).
    summary_str 例: '🟢 Online · 5 peers' / '🔴 Offline'
    """
    data = http_get_json(DAEMON_HEALTH_URL)
    if not data:
        return False, "🔴 Offline"
    status_ok = data.get("status") == "ok"
    # peers: 当前 /sisoul/health 不暴露 peer count, 拉 net status 太重, 这里用 endpoints_implemented 当回执.
    n_endpoints = len(data.get("daemon", {}).get("endpoints_implemented", []))
    if status_ok:
        ver = data.get("version", "?")
        return True, f"🟢 Online · v{ver} · {n_endpoints} endpoint"
    return False, "🔴 Offline (health 非 ok)"


def daemon_running_pid() -> Optional[int]:
    if not DAEMON_PID_FILE.exists():
        return None
    try:
        pid = int(DAEMON_PID_FILE.read_text().strip())
        # 真活检
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        return None


def start_daemon(sisoul_bin: str) -> tuple[bool, str]:
    online, _ = daemon_health()
    if online:
        return True, "daemon 已在跑, 跳过 start."
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # nohup sisoul daemon start &
    try:
        with open(DAEMON_LOG, "ab") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} menubar start_daemon ===\n".encode())
            proc = subprocess.Popen(
                [sisoul_bin, "daemon", "start"],
                stdout=f,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        DAEMON_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        DAEMON_PID_FILE.write_text(str(proc.pid))
        log.info("daemon 启动 pid=%s log=%s", proc.pid, DAEMON_LOG)
        return True, f"daemon 已启动 (pid={proc.pid})"
    except Exception as e:
        log.exception("start_daemon 失败")
        return False, f"start 失败: {e}"


def stop_daemon(sisoul_bin: str) -> tuple[bool, str]:
    pid = daemon_running_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                try:
                    os.kill(pid, 0)
                    time.sleep(0.25)
                except ProcessLookupError:
                    break
            log.info("daemon stop via SIGTERM pid=%s", pid)
            DAEMON_PID_FILE.unlink(missing_ok=True)
            return True, f"已 SIGTERM (pid={pid})"
        except Exception as e:
            log.exception("stop_daemon SIGTERM 失败")
            return False, f"SIGTERM 失败: {e}"
    # fallback 走 CLI
    try:
        out = subprocess.run(
            [sisoul_bin, "daemon", "stop"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        log.info("daemon stop via CLI rc=%s out=%s err=%s", out.returncode, out.stdout, out.stderr)
        if out.returncode == 0:
            return True, "已通过 CLI 停止"
        return False, f"sisoul daemon stop rc={out.returncode}: {out.stderr or out.stdout}"
    except Exception as e:
        log.exception("stop_daemon CLI 失败")
        return False, f"stop 失败: {e}"


# ---------- friends ----------
def load_recent_friends(limit: int = 5) -> list[dict]:
    if not FRIENDS_FILE.exists():
        return []
    try:
        raw = json.loads(FRIENDS_FILE.read_text())
        if not isinstance(raw, list):
            return []
        # 按 added_at 倒序
        raw_sorted = sorted(
            raw, key=lambda f: f.get("added_at", ""), reverse=True
        )
        return raw_sorted[:limit]
    except Exception:
        log.exception("解析 didkey_friends.json 失败")
        return []


# ---------- subprocess helper ----------
def run_sisoul(sisoul_bin: str, args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """跑 sisoul 子命令, 真发生 subprocess.run, log 进 tray log."""
    cmd = [sisoul_bin, *args]
    log.info("RUN %s", " ".join(cmd))
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        log.info("RUN rc=%s stdout=%s stderr=%s", out.returncode, out.stdout[:500], out.stderr[:500])
        return out.returncode, out.stdout, out.stderr
    except subprocess.TimeoutExpired as e:
        log.warning("RUN timeout: %s", e)
        return 124, "", f"timeout after {timeout}s"
    except Exception as e:
        log.exception("RUN exception")
        return 1, "", str(e)


# ---------- App ----------
class SisoulTrayApp(rumps.App):
    def __init__(self) -> None:
        super().__init__(
            name="Sisoul",
            title="S",  # menu bar 文字图标 (无 icns 时退路)
            quit_button=None,
        )
        # 尝试加载 icon (有就用 image)
        candidate_icons = [
            Path(__file__).parent / "Sisoul.icns",
            Path(__file__).parent / "icon.icns",
            Path(__file__).parent / "icon.png",
        ]
        for ic in candidate_icons:
            if ic.exists():
                try:
                    self.icon = str(ic)
                    self.title = None
                    break
                except Exception:
                    pass

        try:
            self.sisoul_bin = resolve_sisoul_bin()
        except FileNotFoundError as e:
            self.sisoul_bin = ""
            log.error("%s", e)

        # ----- 菜单 -----
        self.status_item = rumps.MenuItem("🔴 Offline")
        self.status_item.set_callback(None)  # 只显示, 不点

        self.start_item = rumps.MenuItem("Start daemon", callback=self.on_start_daemon)
        self.stop_item = rumps.MenuItem("Stop daemon", callback=self.on_stop_daemon)

        self.add_friend_item = rumps.MenuItem("Add friend...", callback=self.on_add_friend)
        self.recent_friends_menu = rumps.MenuItem("Recent friends")

        self.ask_founder_item = rumps.MenuItem("Ask founder...", callback=self.on_ask_founder)
        self.borrow_llm_item = rumps.MenuItem("Borrow LLM...", callback=self.on_borrow_llm)

        self.open_dashboard_item = rumps.MenuItem(
            "Open dashboard", callback=self.on_open_dashboard
        )
        self.open_install_item = rumps.MenuItem(
            "Open INSTALL docs", callback=self.on_open_install
        )

        self.about_item = rumps.MenuItem(f"About Sisoul Tray v{__version__}",
                                         callback=self.on_about)
        self.quit_item = rumps.MenuItem("Quit", callback=rumps.quit_application)

        self.menu = [
            self.status_item,
            self.start_item,
            self.stop_item,
            None,
            self.add_friend_item,
            self.recent_friends_menu,
            None,
            self.ask_founder_item,
            self.borrow_llm_item,
            None,
            self.open_dashboard_item,
            self.open_install_item,
            None,
            self.about_item,
            self.quit_item,
        ]

        # 首刷
        self.refresh_now()

    # ---------- 刷新 ----------
    def refresh_now(self) -> None:
        # daemon status
        online, summary = daemon_health()
        self.status_item.title = summary
        # title 上额外加 dot (但 icns 模式下用图标, 这里只在文字模式补充)
        if self.icon is None:
            self.title = "S" + ("•" if online else "·")

        # recent friends
        friends = load_recent_friends(limit=5)
        # rumps MenuItem 子菜单需要重建
        new_sub = rumps.MenuItem("Recent friends")
        if not friends:
            new_sub.add(rumps.MenuItem("(no friends yet)"))
        else:
            for f in friends:
                nick = f.get("nickname") or f.get("did", "?")[:24]
                added = f.get("added_at", "")[:10]
                label = f"{nick}  ({added})"
                item = rumps.MenuItem(label, callback=self._make_friend_info_cb(f))
                new_sub.add(item)
        # 替换原 menu 位置
        self.menu.pop("Recent friends", None)
        # 重新插入 — rumps 不支持 insert at index 直接, 简化: 把整菜单重建
        self._rebuild_menu(new_sub)

    def _rebuild_menu(self, new_recent_sub: rumps.MenuItem) -> None:
        self.recent_friends_menu = new_recent_sub
        # rumps.App.menu 是 MenuItem ordered, 直接 clear 重填
        self.menu.clear()
        self.menu = [
            self.status_item,
            self.start_item,
            self.stop_item,
            None,
            self.add_friend_item,
            self.recent_friends_menu,
            None,
            self.ask_founder_item,
            self.borrow_llm_item,
            None,
            self.open_dashboard_item,
            self.open_install_item,
            None,
            self.about_item,
            self.quit_item,
        ]

    @rumps.timer(DAEMON_REFRESH_SEC)
    def on_tick(self, _sender):  # noqa: D401
        try:
            self.refresh_now()
        except Exception:
            log.exception("on_tick refresh 失败")

    # ---------- friend callbacks ----------
    def _make_friend_info_cb(self, f: dict):
        def _cb(_sender):
            nick = f.get("nickname") or f.get("did", "?")
            did = f.get("did", "?")
            added = f.get("added_at", "")
            rumps.alert(
                title=f"Friend: {nick}",
                message=f"DID: {did}\nadded: {added}\nmethod: {f.get('method','?')}",
                ok="OK",
            )
        return _cb

    # ---------- 菜单回调 ----------
    def on_start_daemon(self, _sender):
        if not self.sisoul_bin:
            rumps.alert("sisoul CLI 没找到", "请装 sisoul 到 PATH 或 ~/.local/bin/")
            return
        ok, msg = start_daemon(self.sisoul_bin)
        rumps.notification("Sisoul daemon", "start", msg)
        self.refresh_now()

    def on_stop_daemon(self, _sender):
        if not self.sisoul_bin:
            rumps.alert("sisoul CLI 没找到", "请装 sisoul")
            return
        ok, msg = stop_daemon(self.sisoul_bin)
        rumps.notification("Sisoul daemon", "stop", msg)
        self.refresh_now()

    def on_add_friend(self, _sender):
        if not self.sisoul_bin:
            rumps.alert("sisoul CLI 没找到", "请装 sisoul")
            return
        w = rumps.Window(
            title="Add friend",
            message="输入 @username (EAS Optimism) 或 did:key:...",
            default_text="@",
            ok="Add",
            cancel="Cancel",
            dimensions=(320, 40),
        )
        resp = w.run()
        if not resp.clicked:
            return
        target = (resp.text or "").strip()
        if not target:
            rumps.alert("Add friend", "输入为空")
            return
        rc, out, err = run_sisoul(self.sisoul_bin, ["friend", "add", target], timeout=60)
        if rc == 0:
            rumps.notification("Sisoul friend", "added", f"{target}\n{out[:120]}")
        else:
            rumps.alert(
                f"sisoul friend add {target} 失败 (rc={rc})",
                (err or out)[:600],
            )
        self.refresh_now()

    def on_ask_founder(self, _sender):
        if not self.sisoul_bin:
            rumps.alert("sisoul CLI 没找到", "请装 sisoul")
            return
        w = rumps.Window(
            title="Ask @founder",
            message="问 sisoul founder-agent 一个问题 (本地 case-graph + LLM)",
            default_text="sisoul 为什么不做 token?",
            ok="Ask",
            cancel="Cancel",
            dimensions=(380, 80),
        )
        resp = w.run()
        if not resp.clicked:
            return
        prompt = (resp.text or "").strip()
        if not prompt:
            return
        rc, out, err = run_sisoul(self.sisoul_bin, ["founder", "chat", prompt], timeout=90)
        body = out.strip() if rc == 0 else (err or out).strip()
        rumps.Window(
            title="@founder answer" if rc == 0 else f"@founder failed rc={rc}",
            message=body[:4000] if body else "(no output)",
            default_text="",
            ok="OK",
            cancel=None,
            dimensions=(520, 240),
        ).run()

    def on_borrow_llm(self, _sender):
        if not self.sisoul_bin:
            rumps.alert("sisoul CLI 没找到", "请装 sisoul")
            return
        w1 = rumps.Window(
            title="Borrow LLM — friend",
            message="输入要借的朋友 (@username 或 did:key:...)",
            default_text="@",
            ok="Next",
            cancel="Cancel",
            dimensions=(320, 40),
        )
        r1 = w1.run()
        if not r1.clicked:
            return
        friend = (r1.text or "").strip()
        if not friend:
            return
        w2 = rumps.Window(
            title=f"Borrow LLM from {friend}",
            message="prompt:",
            default_text="hello from sisoul borrow",
            ok="Send",
            cancel="Cancel",
            dimensions=(380, 80),
        )
        r2 = w2.run()
        if not r2.clicked:
            return
        prompt = (r2.text or "").strip()
        if not prompt:
            return
        rc, out, err = run_sisoul(
            self.sisoul_bin, ["borrow", "run", friend, prompt], timeout=120
        )
        body = out.strip() if rc == 0 else (err or out).strip()
        rumps.Window(
            title="borrow result" if rc == 0 else f"borrow failed rc={rc}",
            message=body[:4000] if body else "(no output)",
            default_text="",
            ok="OK",
            cancel=None,
            dimensions=(520, 240),
        ).run()

    def on_open_dashboard(self, _sender):
        webbrowser.open(DASHBOARD_URL)

    def on_open_install(self, _sender):
        webbrowser.open(INSTALL_DOC_URL)

    def on_about(self, _sender):
        rumps.alert(
            title="Sisoul Tray",
            message=(
                f"sisoul menu-bar tray v{__version__}\n"
                f"sisoul CLI: {self.sisoul_bin or '(missing)'}\n"
                f"log: {TRAY_LOG}\n"
                f"daemon: {DAEMON_HEALTH_URL}"
            ),
            ok="OK",
        )


# ---------- CLI entrypoint ----------
def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"--version", "-V", "version"}:
        print(f"sisoul-tray {__version__}")
        return 0
    if argv and argv[0] in {"--help", "-h", "help"}:
        print(
            "sisoul-tray — macOS menu-bar tray for sisoul daemon\n"
            "  --version    print version\n"
            "  --self-check probe daemon health + friends count, no GUI\n"
            "  (no args)    launch menu-bar app\n"
        )
        return 0
    if argv and argv[0] == "--self-check":
        online, summary = daemon_health()
        friends = load_recent_friends(limit=5)
        try:
            bin_ = resolve_sisoul_bin()
        except FileNotFoundError as e:
            bin_ = f"MISSING ({e})"
        print(json.dumps(
            {
                "tray_version": __version__,
                "sisoul_bin": bin_,
                "daemon_online": online,
                "daemon_summary": summary,
                "friends_count": len(friends),
                "friends_preview": [
                    {"nickname": f.get("nickname"), "did": f.get("did")[:24] + "..."}
                    for f in friends
                ],
                "log": str(TRAY_LOG),
            },
            indent=2,
            ensure_ascii=False,
        ))
        return 0

    app = SisoulTrayApp()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
