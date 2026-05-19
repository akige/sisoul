"""tests/test_hooks.py — smoke test 3 hooks bash 语法 + mock daemon endpoint 返回.

测试策略:
1. bash -n 语法检查 (已在 test_install_script.py 做, 这里不重复)
2. 用 HTTP mock server 模拟 sisoul daemon 在 9876
3. 跑 hook (subprocess), 验证:
   - session_start: stdout 含 <sisoul-preferences> 和 <sisoul-long-term-goals>
   - post_tool_use: destructive 命令触发 audit POST
   - stop: 发 session-summary + goal-progress POST
4. daemon 不在线时 hook 静默 exit 0 (不破坏 Claude Code)

Mock daemon 实现: 用 threading + http.server (stdlib, 无额外依赖)
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

import pytest

HOOKS_DIR = Path(__file__).parent.parent / "ops" / "hooks"
SESSION_START = HOOKS_DIR / "sisoul_session_start.sh"
POST_TOOL_USE = HOOKS_DIR / "sisoul_post_tool_use.sh"
STOP_HOOK = HOOKS_DIR / "sisoul_stop.sh"


# ── mock daemon server ────────────────────────────────────────

class _MockDaemonHandler(BaseHTTPRequestHandler):
    """最小化 sisoul daemon mock (只 log 请求)."""

    # 共享状态 (线程间通信)
    received_requests: ClassVar[list[dict]] = []

    def log_message(self, *args: object) -> None:  # noqa: ANN002
        pass  # 静默 access log

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = b""
        ctype = "application/json"

        # P2-5: hook 改走 /preferences/list + /goals/list (JSON list).
        # 保留旧 /preferences /long-term-goals plain text 端点兼容 (test 复用).
        if path == "/sisoul/preferences/list":
            body = json.dumps([
                {"id": "tailwind", "title": "使用 Tailwind", "body": "使用 Tailwind, Python 3.11+"},
            ]).encode("utf-8")
        elif path == "/sisoul/goals/list":
            body = json.dumps([
                {"id": "v1", "title": "完成 sisoul v1.0", "progress": "15%"},
            ]).encode("utf-8")
        elif path == "/sisoul/preferences":
            body = "当前偏好: 使用 Tailwind, Python 3.11+".encode("utf-8")
            ctype = "text/plain; charset=utf-8"
        elif path == "/sisoul/long-term-goals":
            body = "目标 1: 完成 sisoul v1.0 (进度 15%)".encode("utf-8")
            ctype = "text/plain; charset=utf-8"
        elif path == "/sisoul/health":
            body = json.dumps({"status": "ok"}).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

        _MockDaemonHandler.received_requests.append({"method": "GET", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length > 0 else b""

        try:
            body = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, ValueError):
            body = {"raw": raw.decode("utf-8", errors="replace")}

        _MockDaemonHandler.received_requests.append({
            "method": "POST",
            "path": path,
            "body": body,
        })

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')


class MockDaemon:
    """mock sisoul daemon 上下文管理器."""

    def __init__(self, port: int = 19876) -> None:
        self.port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        _MockDaemonHandler.received_requests.clear()
        self._server = HTTPServer(("127.0.0.1", self.port), _MockDaemonHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        # 等 server 就绪
        time.sleep(0.05)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()

    def requests(self) -> list[dict]:
        return list(_MockDaemonHandler.received_requests)

    def __enter__(self) -> "MockDaemon":
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()


def _run_hook(hook_path: Path, env: dict | None = None, timeout: int = 8) -> subprocess.CompletedProcess:
    """跑 hook script, 返回 CompletedProcess.

    P2-5: session_start hook 改走 SISOUL_BASE; 其他 hooks 仍用 SISOUL_PORT.
    两个都传, 老/新 hook 都能用.
    """
    base_env = {
        **os.environ,
        "SISOUL_PORT": "19876",
        "SISOUL_BASE": "http://127.0.0.1:19876",
    }
    if env:
        base_env.update(env)
        # 如果 env 给了 SISOUL_PORT, 自动同步 SISOUL_BASE
        if "SISOUL_PORT" in env and "SISOUL_BASE" not in env:
            base_env["SISOUL_BASE"] = f"http://127.0.0.1:{env['SISOUL_PORT']}"
    return subprocess.run(
        ["bash", str(hook_path)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=base_env,
    )


# ── session_start hook ────────────────────────────────────────

def test_session_start_with_daemon() -> None:
    """daemon 在线: session_start stdout 含 preferences + goals."""
    with MockDaemon(19876):
        result = _run_hook(SESSION_START)

    assert result.returncode == 0
    assert "<sisoul-preferences>" in result.stdout
    assert "</sisoul-preferences>" in result.stdout
    assert "Tailwind" in result.stdout


def test_session_start_goals_injected() -> None:
    """daemon 在线: session_start stdout 含 long-term-goals."""
    with MockDaemon(19876):
        result = _run_hook(SESSION_START)

    assert result.returncode == 0
    assert "<sisoul-long-term-goals>" in result.stdout
    assert "</sisoul-long-term-goals>" in result.stdout
    assert "sisoul v1.0" in result.stdout


def test_session_start_daemon_offline_silent() -> None:
    """daemon 不在线: session_start 静默 exit 0, 无报错."""
    # 不起 mock daemon, 用一个肯定没 server 的端口
    env = {"SISOUL_PORT": "19999"}
    result = subprocess.run(
        ["bash", str(SESSION_START)],
        capture_output=True,
        text=True,
        timeout=8,
        env={**os.environ, **env},
    )
    assert result.returncode == 0
    # 输出应为空 (daemon 不在线, 两个 if [[ -n ]] 都 false)
    assert result.stdout.strip() == ""


def test_session_start_calls_preferences_endpoint() -> None:
    """session_start (P2-5) 发 GET /sisoul/preferences/list (JSON list)."""
    with MockDaemon(19876) as mock:
        _run_hook(SESSION_START)
        reqs = mock.requests()

    paths_called = [r["path"] for r in reqs if r["method"] == "GET"]
    assert "/sisoul/preferences/list" in paths_called


def test_session_start_calls_goals_endpoint() -> None:
    """session_start (P2-5) 发 GET /sisoul/goals/list (JSON list)."""
    with MockDaemon(19876) as mock:
        _run_hook(SESSION_START)
        reqs = mock.requests()

    paths_called = [r["path"] for r in reqs if r["method"] == "GET"]
    assert "/sisoul/goals/list" in paths_called


# ── post_tool_use hook ────────────────────────────────────────

def test_post_tool_use_rm_triggers_audit() -> None:
    """TOOL_NAME=Bash + TOOL_INPUT 含 'rm ' → 发 POST /sisoul/audit."""
    env = {
        "TOOL_NAME": "Bash",
        "TOOL_INPUT": "rm -rf /tmp/test_file.txt",
        "PROMPT_HASH": "abc123",
        "CLAUDE_SESSION_ID": "test-session-1",
    }
    with MockDaemon(19876) as mock:
        result = _run_hook(POST_TOOL_USE, env=env)
        time.sleep(0.2)  # 后台 curl & 等完成
        reqs = mock.requests()

    assert result.returncode == 0
    audit_posts = [r for r in reqs if r["method"] == "POST" and r["path"] == "/sisoul/audit"]
    assert len(audit_posts) >= 1


def test_post_tool_use_git_reset_triggers_audit() -> None:
    """TOOL_INPUT 含 'git reset' → 触发 audit."""
    env = {
        "TOOL_NAME": "Bash",
        "TOOL_INPUT": "git reset --hard HEAD~1",
        "PROMPT_HASH": "def456",
        "CLAUDE_SESSION_ID": "test-session-2",
    }
    with MockDaemon(19876) as mock:
        result = _run_hook(POST_TOOL_USE, env=env)
        time.sleep(0.2)
        reqs = mock.requests()

    assert result.returncode == 0
    audit_posts = [r for r in reqs if r["method"] == "POST" and r["path"] == "/sisoul/audit"]
    assert len(audit_posts) >= 1


def test_post_tool_use_non_destructive_no_audit() -> None:
    """TOOL_NAME=Bash + 无害命令 → 不发 audit."""
    env = {
        "TOOL_NAME": "Bash",
        "TOOL_INPUT": "ls -la /tmp",
        "PROMPT_HASH": "ghi789",
        "CLAUDE_SESSION_ID": "test-session-3",
    }
    with MockDaemon(19876) as mock:
        result = _run_hook(POST_TOOL_USE, env=env)
        time.sleep(0.2)
        reqs = mock.requests()

    assert result.returncode == 0
    audit_posts = [r for r in reqs if r["method"] == "POST" and r["path"] == "/sisoul/audit"]
    assert len(audit_posts) == 0


def test_post_tool_use_non_bash_no_audit() -> None:
    """TOOL_NAME=Read (非 Bash) → 不发 audit, 即使 input 看起来 destructive."""
    env = {
        "TOOL_NAME": "Read",
        "TOOL_INPUT": "rm -rf /",  # 假装危险, 但工具是 Read
        "PROMPT_HASH": "jkl000",
        "CLAUDE_SESSION_ID": "test-session-4",
    }
    with MockDaemon(19876) as mock:
        result = _run_hook(POST_TOOL_USE, env=env)
        time.sleep(0.2)
        reqs = mock.requests()

    assert result.returncode == 0
    audit_posts = [r for r in reqs if r["method"] == "POST" and r["path"] == "/sisoul/audit"]
    assert len(audit_posts) == 0


def test_post_tool_use_daemon_offline_exit_0() -> None:
    """daemon 不在线: post_tool_use 静默 exit 0."""
    env = {
        "TOOL_NAME": "Bash",
        "TOOL_INPUT": "rm -rf /tmp/sisoul_test",
        "PROMPT_HASH": "offline_test",
        "CLAUDE_SESSION_ID": "offline-session",
        "SISOUL_PORT": "19999",
    }
    result = subprocess.run(
        ["bash", str(POST_TOOL_USE)],
        capture_output=True,
        text=True,
        timeout=8,
        env={**os.environ, **env},
    )
    assert result.returncode == 0


# ── stop hook ─────────────────────────────────────────────────

def test_stop_sends_session_summary() -> None:
    """stop hook 发 POST /sisoul/session-summary."""
    env = {
        "CLAUDE_SESSION_ID": "stop-test-session",
        "CLAUDE_TURNS": "15",
        "CLAUDE_DURATION": "300",
    }
    with MockDaemon(19876) as mock:
        result = _run_hook(STOP_HOOK, env=env)
        time.sleep(0.5)  # 后台 & 请求
        reqs = mock.requests()

    assert result.returncode == 0
    summary_posts = [r for r in reqs if r["method"] == "POST" and r["path"] == "/sisoul/session-summary"]
    assert len(summary_posts) >= 1


def test_stop_sends_goal_progress() -> None:
    """stop hook 发 POST /sisoul/goal-progress."""
    env = {
        "CLAUDE_SESSION_ID": "stop-test-session-gp",
        "CLAUDE_TURNS": "8",
        "CLAUDE_DURATION": "120",
    }
    with MockDaemon(19876) as mock:
        result = _run_hook(STOP_HOOK, env=env)
        time.sleep(0.5)
        reqs = mock.requests()

    assert result.returncode == 0
    gp_posts = [r for r in reqs if r["method"] == "POST" and r["path"] == "/sisoul/goal-progress"]
    assert len(gp_posts) >= 1


def test_stop_session_summary_has_session_id() -> None:
    """session-summary 请求 body 含 session_id."""
    env = {
        "CLAUDE_SESSION_ID": "my-unique-session-xyz",
        "CLAUDE_TURNS": "5",
        "CLAUDE_DURATION": "60",
    }
    with MockDaemon(19876) as mock:
        _run_hook(STOP_HOOK, env=env)
        time.sleep(0.5)
        reqs = mock.requests()

    summary_posts = [r for r in reqs if r["path"] == "/sisoul/session-summary"]
    assert len(summary_posts) >= 1
    body = summary_posts[0].get("body", {})
    assert body.get("session_id") == "my-unique-session-xyz"


def test_stop_daemon_offline_exit_0() -> None:
    """daemon 不在线: stop hook 静默 exit 0."""
    env = {
        "CLAUDE_SESSION_ID": "offline-stop",
        "CLAUDE_TURNS": "3",
        "CLAUDE_DURATION": "45",
        "SISOUL_PORT": "19999",
    }
    result = subprocess.run(
        ["bash", str(STOP_HOOK)],
        capture_output=True,
        text=True,
        timeout=8,
        env={**os.environ, **env},
    )
    assert result.returncode == 0
