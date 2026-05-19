"""Phase 1 波 2 集成测试: CLI 主入口 (cli.py 14 命令整合).

W1 Day 1 的 stub test (test_init_stub 等) 已删 (命令已真实现, 不再是 stub).
各命令的真实现单元测试在 test_{vault,llm,sync,cli_*}.py 里 (dev-A/B/C/D 写的).
本文件仅测 cli.py 主入口整合层.
"""

from __future__ import annotations

from typer.testing import CliRunner

from sisoul import __version__
from sisoul.cli import app

runner = CliRunner()


def test_version() -> None:
    """--version 输出版本号."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_all_14_commands() -> None:
    """--help 列出全部 14 命令 (含波 2 新加 ask / sync 子app / goals 子app / daemon)."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in [
        # Phase 1 已 ship (波 1 + 波 2)
        "init", "login", "ask", "remember", "status",
        "export", "restore", "sync", "goals", "daemon",
        # Phase 2 (波 3)
        "did",
        # Phase 3 (波 4)
        "p2p", "attest", "snapshot",
        # Phase 4 上半 (波 5) — friend/proxy/perms/borrow/lend/ledger 真实现
        "friend", "proxy", "perms", "borrow", "lend", "ledger",
        # stub 留 Phase 3 W37 (verify 还在 stub)
        "verify",
    ]:
        assert cmd in result.stdout, f"命令 '{cmd}' 未在 --help 输出"


def test_login_requires_provider() -> None:
    """login 必须传 --provider."""
    result = runner.invoke(app, ["login"])
    assert result.exit_code != 0  # missing required option


# test_friend / test_borrow / test_lend stub 测试已删 (波 5 真实现替换 stub).
# friend/borrow/lend 真实现单元测试在 test_friend_*.py / test_cli_friend.py / test_cli_borrow.py / test_cli_lend.py


def test_friend_subapp_help() -> None:
    """friend 是 Typer 子 app (波 5 dev-A)."""
    result = runner.invoke(app, ["friend", "--help"])
    assert result.exit_code == 0


def test_borrow_subapp_help() -> None:
    """borrow 是 Typer 子 app (波 5 dev-D)."""
    result = runner.invoke(app, ["borrow", "--help"])
    assert result.exit_code == 0


def test_verify_still_stub() -> None:
    """verify 仍是 Phase 3 stub."""
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 0
    assert "not implemented yet" in result.stdout


def test_sync_subapp_help() -> None:
    """sync 是 Typer 子 app, 有自己的 --help."""
    result = runner.invoke(app, ["sync", "--help"])
    assert result.exit_code == 0
    assert "vault" in result.stdout.lower() or "sync" in result.stdout.lower()


def test_goals_subapp_help() -> None:
    """goals 是 Typer 子 app, 有 list/add/progress 子命令."""
    result = runner.invoke(app, ["goals", "--help"])
    assert result.exit_code == 0
    for sub in ["list", "add", "progress"]:
        assert sub in result.stdout, f"子命令 '{sub}' 未在 goals --help 输出"
