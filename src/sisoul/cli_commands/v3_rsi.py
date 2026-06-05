"""sisoul rsi · CLI 子 app (v3 RSI 框架 skeleton).

命令:
- sisoul rsi iterate --module <m>   跑一轮 RSI 迭代 (skeleton, 不真调 LLM)
- sisoul rsi history                 看过去迭代历史

接入: cli.py `app.add_typer(cli_rsi, name="rsi")`.

注: skeleton CLI 不真调 LLM, ``iterate`` 用一个内置 echo adapter 走通进化主循环,
把每轮结果 append 到 ``~/.sisoul/rsi_history.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from sisoul.v3.rsi import AlphaEvolveLoop, AllowlistViolation, Evaluator


cli_rsi = typer.Typer(
    name="rsi",
    help="v3 递归自我改进 (RSI) 框架 — Gödel / AlphaEvolve / DSPy / Federated (skeleton)",
    no_args_is_help=True,
)


HISTORY_PATH = Path.home() / ".sisoul" / "rsi_history.json"


class _EchoAdapter:
    """skeleton 用占位 LLM adapter — 不真调 LLM, 回显种子 (走通进化循环)."""

    def chat(self, messages: list[dict], **kwargs) -> str:  # noqa: D401
        content = messages[-1]["content"] if messages else ""
        return str(content)


def _load_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _append_history(entry: dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    hist = _load_history()
    hist.append(entry)
    HISTORY_PATH.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")


@cli_rsi.command("iterate")
def cmd_iterate(
    module: str = typer.Option(
        ..., "--module", "-m",
        help="被进化的目标 module dotted-path (必须在 allowlist, 禁 sisoul.v3.rsi.*)",
    ),
    max_iter: int = typer.Option(3, "--max-iter", help="进化代数 (默认 3)"),
    scope: str = typer.Option(
        "tests/test_v3_rsi.py", "--scope",
        help="pytest scope (默认只跑 RSI 自测, 避免全量)",
    ),
) -> None:
    """跑一轮 AlphaEvolve RSI 迭代 (skeleton, 不真调 LLM)."""
    try:
        evaluator = Evaluator(pytest_root=Path.cwd())
        loop = AlphaEvolveLoop(
            target_module=module,
            evaluator=evaluator,
            llm_adapter=_EchoAdapter(),
            n_candidates=3,
        )
    except AllowlistViolation as exc:
        typer.echo(f"❌ allowlist 拒绝: {exc}", err=True)
        raise typer.Exit(2)

    seed = f"# seed for {module}\n"
    typer.echo(f"🧬 RSI iterate · module={module} · max_iter={max_iter} · scope={scope}")
    result = loop.iterate(seed, max_iter=max_iter, scope=scope)

    entry = {
        "module": module,
        "max_iter": max_iter,
        "n_gen": result["n_gen"],
        "best_fitness": result["best_fitness"],
    }
    _append_history(entry)
    typer.echo(f"  代数: {result['n_gen']}  best_fitness: {result['best_fitness']}")
    typer.echo(f"  历史已记录: {HISTORY_PATH}")


@cli_rsi.command("history")
def cmd_history(
    limit: int = typer.Option(10, "--limit", "-n", help="显示最近 N 条 (默认 10)"),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """查看过去的 RSI 迭代历史."""
    hist = _load_history()
    if as_json:
        typer.echo(json.dumps(hist[-limit:], ensure_ascii=False, indent=2))
        return
    if not hist:
        typer.echo("(暂无 RSI 迭代历史 — 先跑 sisoul rsi iterate --module <m>)")
        return
    typer.echo(f"📜 RSI 迭代历史 (最近 {min(limit, len(hist))}/{len(hist)} 条):")
    for i, e in enumerate(hist[-limit:], 1):
        typer.echo(
            f"  {i}. module={e.get('module')} "
            f"gen={e.get('n_gen')} fitness={e.get('best_fitness')}"
        )
