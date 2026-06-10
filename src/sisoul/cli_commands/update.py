"""sisoul update — 检查 + 自升级 (2026-06-10).

装在 git clone (install.sh 默认 ~/sisoul-app) 时真跑 git pull + pip install;
其他装法 (brew/pip) 打印对应升级命令. 0 自营服务: 只比对 GitHub raw 的
pyproject.toml version.
"""
from __future__ import annotations

import subprocess
import sys

import typer


def cli_update(
    check_only: bool = typer.Option(
        False, "--check", help="只检查不升级"
    ),
    force: bool = typer.Option(
        False, "--force", help="跳过 24h 缓存强制查"
    ),
) -> None:
    """检查新版本; 装在 git clone 时直接升级 (git pull + pip install)."""
    from sisoul.update_check import check_update, repo_root

    r = check_update(force=force)
    cur, latest = r["current"], r["latest"]
    if latest is None:
        typer.echo(f"  当前 {cur} · 检查失败 (离线 / GitHub 不可达), 稍后再试")
        raise typer.Exit(1)
    if not r["update_available"]:
        typer.echo(f"  已是最新: {cur}")
        return

    typer.echo(f"  新版本: {cur} → {latest}")
    if check_only:
        typer.echo("  升级: sisoul update")
        return

    root = repo_root()
    if root is None:
        typer.echo("  非 git clone 安装, 用一行命令升级:")
        typer.echo(
            "  curl -sSL https://raw.githubusercontent.com/akige/sisoul/main/install.sh | bash"
        )
        return

    typer.echo(f"  git clone 检测到: {root}")
    typer.echo("  → git pull --ff-only")
    pull = subprocess.run(
        ["git", "pull", "--ff-only"], cwd=root, capture_output=True, text=True
    )
    if pull.returncode != 0:
        typer.echo(f"  git pull 失败: {pull.stderr.strip()[:200]}")
        raise typer.Exit(1)
    typer.echo(f"  {pull.stdout.strip().splitlines()[-1] if pull.stdout.strip() else 'pulled'}")

    typer.echo("  → pip install -e '.[daemon,crypto,chat,llm]'")
    pip = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-e", ".[daemon,crypto,chat,llm]"],
        cwd=root, capture_output=True, text=True,
    )
    if pip.returncode != 0:
        typer.echo(f"  pip install 失败: {pip.stderr.strip()[:200]}")
        raise typer.Exit(1)

    typer.echo("  ✓ 升级完成. daemon 在跑的话重启生效:")
    typer.echo("    pkill -f 'sisoul daemon'; sisoul daemon &")
