"""tests/test_install_script.py — smoke test bash install.sh 语法 + 关键行.

测试策略:
1. bash -n 语法检查 (不真跑)
2. 关键行存在性检查 (grep 等价, Path.read_text)
3. chmod +x 已生效 (文件可执行位)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

# 脚本路径
# P2-EF: ops/install.sh 改成 release 一行 curl|bash 装机器 (sigstore 验签);
# 旧的开发模式装机脚本搬到 ops/install-dev.sh (本测试集覆盖的就是这个).
REPO_ROOT = Path(__file__).parent.parent  # dev/sisoul/
INSTALL_SH = REPO_ROOT / "ops" / "install-dev.sh"
LAUNCHD_PLIST = REPO_ROOT / "ops" / "launchd" / "com.sisoul.daemon.plist"
SYSTEMD_SERVICE = REPO_ROOT / "ops" / "systemd" / "sisoul-daemon.service"


# ── install.sh ────────────────────────────────────────────────

def test_install_sh_exists() -> None:
    assert INSTALL_SH.exists(), f"install.sh 不存在: {INSTALL_SH}"


def test_install_sh_executable() -> None:
    """install.sh 有可执行位."""
    mode = os.stat(INSTALL_SH).st_mode
    assert mode & 0o111, "install.sh 无可执行位 (chmod +x 未跑?)"


def test_install_sh_bash_syntax() -> None:
    """bash -n 语法检查 (不真执行)."""
    result = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"bash -n 失败:\n{result.stderr}"


def test_install_sh_has_shebang() -> None:
    content = INSTALL_SH.read_text(encoding="utf-8")
    first_line = content.splitlines()[0]
    assert first_line.startswith("#!/"), f"缺 shebang: {first_line!r}"
    assert "bash" in first_line


def test_install_sh_has_set_euo() -> None:
    """set -euo pipefail 存在."""
    content = INSTALL_SH.read_text(encoding="utf-8")
    assert "set -euo pipefail" in content


def test_install_sh_detects_macos() -> None:
    content = INSTALL_SH.read_text(encoding="utf-8")
    assert "Darwin" in content
    assert "macos" in content


def test_install_sh_detects_linux() -> None:
    content = INSTALL_SH.read_text(encoding="utf-8")
    assert "Linux" in content
    assert "linux" in content


def test_install_sh_uses_uv() -> None:
    content = INSTALL_SH.read_text(encoding="utf-8")
    assert "uv" in content
    assert "uv venv" in content or "uv pip install" in content


def test_install_sh_creates_vault_dir() -> None:
    content = INSTALL_SH.read_text(encoding="utf-8")
    assert "SISOUL_VAULT_DIR" in content
    assert "mkdir -p" in content


def test_install_sh_installs_launchd() -> None:
    content = INSTALL_SH.read_text(encoding="utf-8")
    assert "launchctl" in content
    assert "LaunchAgents" in content


def test_install_sh_installs_systemd() -> None:
    content = INSTALL_SH.read_text(encoding="utf-8")
    assert "systemctl" in content
    assert "systemd/user" in content


def test_install_sh_mentions_hooks() -> None:
    """install.sh 提醒用户手动 cp hooks (不自动装)."""
    content = INSTALL_SH.read_text(encoding="utf-8")
    assert "hooks" in content
    assert "~/.claude/hooks" in content


def test_install_sh_replaces_user_placeholder() -> None:
    """install.sh 替换 SISOUL_USER 占位符."""
    content = INSTALL_SH.read_text(encoding="utf-8")
    assert "SISOUL_USER" in content  # 模板里有占位符引用
    assert 'sed "s|SISOUL_USER|' in content  # 替换逻辑


def test_install_sh_has_sisoul_init_hint() -> None:
    """next steps 里含 init 引导 (通过 $SISOUL_BIN init 形式)."""
    content = INSTALL_SH.read_text(encoding="utf-8")
    # install.sh 用 $SISOUL_BIN init 而非字面 "sisoul init"
    assert "init" in content
    assert "SISOUL_BIN" in content or "sisoul" in content.lower()


def test_install_sh_no_real_install_to_claude_hooks() -> None:
    """install.sh 绝不自动 cp 到 ~/.claude/hooks/ (只提示用户手动操作)."""
    content = INSTALL_SH.read_text(encoding="utf-8")
    # 不能有: cp 直接装 ~/.claude/hooks/
    # 允许: echo "cp ... ~/.claude/hooks/" 提示用户
    lines = content.splitlines()
    for line in lines:
        stripped = line.strip()
        # 跳过注释行
        if stripped.startswith("#"):
            continue
        # 跳过 echo 行 (提示文本)
        if "echo" in stripped:
            continue
        # 不应有 cp xxx ~/.claude/hooks/ 这样的真实命令
        assert "~/.claude/hooks/" not in stripped, (
            f"install.sh 不能自动 cp 到 ~/.claude/hooks/: {line!r}"
        )


# ── launchd plist ─────────────────────────────────────────────

def test_launchd_plist_exists() -> None:
    assert LAUNCHD_PLIST.exists()


def test_launchd_plist_valid_xml_syntax() -> None:
    """bash -c 'plutil -lint <file>' — macOS 专有, 跳过非 Mac."""
    import platform
    if platform.system() != "Darwin":
        pytest.skip("plutil 只在 macOS 可用")

    result = subprocess.run(
        ["plutil", "-lint", str(LAUNCHD_PLIST)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"plutil -lint 失败:\n{result.stderr}"


def test_launchd_plist_label() -> None:
    content = LAUNCHD_PLIST.read_text(encoding="utf-8")
    assert "com.sisoul.daemon" in content


def test_launchd_plist_run_at_load() -> None:
    content = LAUNCHD_PLIST.read_text(encoding="utf-8")
    assert "RunAtLoad" in content
    assert "<true/>" in content


def test_launchd_plist_keep_alive() -> None:
    content = LAUNCHD_PLIST.read_text(encoding="utf-8")
    assert "KeepAlive" in content


def test_launchd_plist_user_placeholder() -> None:
    """plist 含 SISOUL_USER 占位符 (install.sh 会替换, 避免 XML tag 冲突)."""
    content = LAUNCHD_PLIST.read_text(encoding="utf-8")
    assert "SISOUL_USER" in content


def test_launchd_plist_port_9876() -> None:
    content = LAUNCHD_PLIST.read_text(encoding="utf-8")
    assert "9876" in content


def test_launchd_plist_log_paths() -> None:
    content = LAUNCHD_PLIST.read_text(encoding="utf-8")
    assert "StandardOutPath" in content
    assert "StandardErrorPath" in content
    assert "sisoul-daemon" in content


# ── systemd service ───────────────────────────────────────────

def test_systemd_service_exists() -> None:
    assert SYSTEMD_SERVICE.exists()


def test_systemd_service_unit_section() -> None:
    content = SYSTEMD_SERVICE.read_text(encoding="utf-8")
    assert "[Unit]" in content


def test_systemd_service_service_section() -> None:
    content = SYSTEMD_SERVICE.read_text(encoding="utf-8")
    assert "[Service]" in content


def test_systemd_service_install_section() -> None:
    content = SYSTEMD_SERVICE.read_text(encoding="utf-8")
    assert "[Install]" in content


def test_systemd_service_description() -> None:
    content = SYSTEMD_SERVICE.read_text(encoding="utf-8")
    assert "Description=" in content
    assert "sisoul" in content.lower()


def test_systemd_service_exec_start() -> None:
    content = SYSTEMD_SERVICE.read_text(encoding="utf-8")
    assert "ExecStart=" in content
    assert "sisoul" in content
    assert "daemon" in content


def test_systemd_service_restart() -> None:
    content = SYSTEMD_SERVICE.read_text(encoding="utf-8")
    assert "Restart=" in content
    assert "on-failure" in content


def test_systemd_service_wanted_by_default() -> None:
    content = SYSTEMD_SERVICE.read_text(encoding="utf-8")
    assert "WantedBy=default.target" in content


def test_systemd_service_port_9876() -> None:
    content = SYSTEMD_SERVICE.read_text(encoding="utf-8")
    assert "9876" in content


# ── hooks ─────────────────────────────────────────────────────

HOOKS_DIR = REPO_ROOT / "ops" / "hooks"


@pytest.mark.parametrize("hook_name", [
    "sisoul_session_start.sh",
    "sisoul_post_tool_use.sh",
    "sisoul_stop.sh",
])
def test_hook_exists(hook_name: str) -> None:
    hook_path = HOOKS_DIR / hook_name
    assert hook_path.exists(), f"hook 不存在: {hook_path}"


@pytest.mark.parametrize("hook_name", [
    "sisoul_session_start.sh",
    "sisoul_post_tool_use.sh",
    "sisoul_stop.sh",
])
def test_hook_executable(hook_name: str) -> None:
    hook_path = HOOKS_DIR / hook_name
    mode = os.stat(hook_path).st_mode
    assert mode & 0o111, f"{hook_name} 无可执行位"


@pytest.mark.parametrize("hook_name", [
    "sisoul_session_start.sh",
    "sisoul_post_tool_use.sh",
    "sisoul_stop.sh",
])
def test_hook_bash_syntax(hook_name: str) -> None:
    hook_path = HOOKS_DIR / hook_name
    result = subprocess.run(
        ["bash", "-n", str(hook_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"{hook_name} bash -n 失败:\n{result.stderr}"
