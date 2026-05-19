"""tests · sync.claude_cli_hook (Phase 2 P2-5).

策略:
- 起一个临时 uvicorn 子进程 (后台 thread), 提供 /sisoul/preferences/list + /sisoul/goals/list
- 用真 bash 跑 hook 脚本 (via subprocess), 验证 stdout 包 <sisoul-preferences>...
- daemon 断 → 验证 silent exit 0 + stdout 空
- python-only path 用 query_daemon_for_inject 也覆盖一遍 (CI 上没 jq/bash 也能跑)
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.sync.claude_cli_hook import (
    DEFAULT_DAEMON_URL,
    install_hook,
    query_daemon_for_inject,
    render_hook_script,
)


# ────────────────────────────────────────────────────────────
# Fake daemon (FastAPI app)
# ────────────────────────────────────────────────────────────


def _make_app(prefs: list[dict], goals: list[dict]) -> FastAPI:
    app = FastAPI()

    @app.get("/sisoul/preferences/list")
    def _prefs() -> list[dict]:
        return prefs

    @app.get("/sisoul/goals/list")
    def _goals() -> list[dict]:
        return goals

    return app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def daemon_url() -> Iterator[str]:
    """启动后台 uvicorn 子线程, yield url, teardown 关掉."""
    import uvicorn

    port = _free_port()
    app = _make_app(
        prefs=[
            {"id": "coffee", "title": "Coffee taste", "body": "light roast pour-over"},
            {"id": "code-style", "title": "Code style", "body": "4-space indent in Python"},
        ],
        goals=[
            {"id": "ship-v1", "title": "Ship sisoul v1.0", "progress": "35%"},
        ],
    )
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)

    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    # wait until ready
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5
    import httpx
    while time.time() < deadline:
        try:
            r = httpx.get(f"{url}/sisoul/preferences/list", timeout=0.5)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.05)
    else:
        server.should_exit = True
        raise RuntimeError("uvicorn fixture failed to start")

    yield url

    server.should_exit = True
    th.join(timeout=3)


# ────────────────────────────────────────────────────────────
# 1. render_hook_script 结构性 sanity
# ────────────────────────────────────────────────────────────


class TestRenderScript:
    def test_renders_bash_shebang(self):
        s = render_hook_script()
        assert s.startswith("#!/bin/bash")
        assert "curl" in s
        assert "/sisoul/preferences/list" in s
        assert "/sisoul/goals/list" in s

    def test_renders_custom_url(self):
        s = render_hook_script(daemon_url="http://example.com:1234")
        assert "http://example.com:1234" in s

    def test_renders_custom_timeout(self):
        s = render_hook_script(timeout_s=7)
        assert "7" in s

    def test_renders_fail_open(self):
        s = render_hook_script()
        # 必含 exit 0 (silent fail-open)
        assert "exit 0" in s
        # 必含 jq fallback (python3 -c)
        assert "python3" in s


# ────────────────────────────────────────────────────────────
# 2. install_hook
# ────────────────────────────────────────────────────────────


class TestInstallHook:
    def test_install_to_tmp(self, tmp_path):
        target = tmp_path / "hooks" / "sisoul_session_start.sh"
        p = install_hook(target_path=target, daemon_url="http://127.0.0.1:9876")
        assert p == target
        assert target.exists()
        # 可执行
        mode = target.stat().st_mode
        assert mode & 0o111
        # 含 daemon url
        assert "http://127.0.0.1:9876" in target.read_text()

    def test_install_no_overwrite(self, tmp_path):
        target = tmp_path / "h.sh"
        target.write_text("pre-existing", encoding="utf-8")
        install_hook(target_path=target, overwrite=False)
        assert target.read_text() == "pre-existing"

    def test_install_overwrite_default(self, tmp_path):
        target = tmp_path / "h.sh"
        target.write_text("pre-existing", encoding="utf-8")
        install_hook(target_path=target)
        assert "#!/bin/bash" in target.read_text()


# ────────────────────────────────────────────────────────────
# 3. query_daemon_for_inject (python-only, 不需 bash/jq)
# ────────────────────────────────────────────────────────────


class TestQueryDaemonPython:
    def test_query_when_daemon_alive(self, daemon_url):
        out = query_daemon_for_inject(daemon_url=daemon_url, timeout_s=2)
        assert "<sisoul-preferences>" in out
        assert "</sisoul-preferences>" in out
        assert "Coffee taste" in out
        assert "<sisoul-long-term-goals>" in out
        assert "Ship sisoul v1.0" in out

    def test_query_when_daemon_dead_silent(self):
        # 拿一个保证空的端口
        port = _free_port()
        out = query_daemon_for_inject(daemon_url=f"http://127.0.0.1:{port}", timeout_s=0.3)
        assert out == ""


# ────────────────────────────────────────────────────────────
# 4. 真 bash subprocess 跑 hook
# ────────────────────────────────────────────────────────────


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash 缺")
class TestBashHookSubprocess:
    def test_hook_invokes_daemon(self, tmp_path, daemon_url):
        target = tmp_path / "hook.sh"
        install_hook(target_path=target, daemon_url=daemon_url, timeout_s=3)
        proc = subprocess.run(
            ["bash", str(target)],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "SISOUL_BASE": daemon_url},
        )
        assert proc.returncode == 0
        # stdout 必含 sisoul-preferences 段
        assert "<sisoul-preferences>" in proc.stdout
        assert "Coffee taste" in proc.stdout or "Code style" in proc.stdout
        assert "<sisoul-long-term-goals>" in proc.stdout

    def test_hook_silent_when_daemon_dead(self, tmp_path):
        # daemon 不存在端口
        dead_port = _free_port()
        target = tmp_path / "hook.sh"
        install_hook(
            target_path=target,
            daemon_url=f"http://127.0.0.1:{dead_port}",
            timeout_s=1,
        )
        proc = subprocess.run(
            ["bash", str(target)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # silent exit 0
        assert proc.returncode == 0
        # 不能 emit sisoul-preferences 段
        assert "<sisoul-preferences>" not in proc.stdout
        assert "<sisoul-long-term-goals>" not in proc.stdout
