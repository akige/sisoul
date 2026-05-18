"""sisoul ask 命令 (Phase 1 W5).

sisoul ask "question"

轻量 query, 走 active provider 跑一次 chat.
不替代 Claude CLI — 只用于元层轻量 query:
  sisoul ask "总结我上个月偏好"
  sisoul ask "我的长期目标进度怎样"

流程:
1. 读 ~/.sisoul/config.yaml → active_provider + api_key
2. 调 adapter.chat([{"role": "user", "content": question}])
3. 输出响应

TODO: Phase 1 W11 加 --with-preferences: 自动 inject vault preferences 作 system prompt.
"""

from __future__ import annotations

from pathlib import Path

import typer

# 模块级 import (便于测试 mock patch)
from sisoul.cli_commands.login import get_active_adapter


def cli_ask(
    question: str = typer.Argument(..., help="要问的问题"),
    no_stream: bool = typer.Option(
        False,
        "--no-stream",
        help="不用流式输出 (默认用流式)",
    ),
    config_path: Path = typer.Option(
        None,
        "--config",
        help="config.yaml 路径 (默认 ~/.sisoul/config.yaml)",
    ),
) -> None:
    """走 active provider 轻量 query (不替代 Claude CLI).

    Examples:
        sisoul ask "总结我上个月偏好"
        sisoul ask "我的长期目标进度怎样"
    """
    from sisoul.llm.base import LLMAdapterError

    adapter = get_active_adapter(config_path=config_path)
    messages = [{"role": "user", "content": question}]

    if no_stream:
        # 非流式: 一次返回全文
        try:
            response = adapter.chat(messages)
            typer.echo(response)
        except LLMAdapterError as e:
            typer.echo(f"❌ {e}", err=True)
            raise typer.Exit(code=1) from e
    else:
        # 流式: print chunks 实时输出
        try:
            for chunk in adapter.chat_stream(messages):
                typer.echo(chunk, nl=False)
            typer.echo("")  # 结尾换行
        except LLMAdapterError as e:
            typer.echo(f"\n❌ {e}", err=True)
            raise typer.Exit(code=1) from e


def run_ask(
    question: str,
    config_path: Path | None = None,
    stream: bool = False,
) -> str:
    """ask 主逻辑 (非 CLI wrapper, 单元测试直接调).

    Args:
        question: 问题字符串
        config_path: config.yaml 路径
        stream: True → 返回 Iterator[str], False → 返回全文 str

    Returns:
        str: 完整回复 (stream=False)

    Raises:
        typer.Exit(code=1): config 未配置 / API 错误
    """
    from sisoul.llm.base import LLMAdapterError

    adapter = get_active_adapter(config_path=config_path)
    messages = [{"role": "user", "content": question}]

    try:
        if stream:
            return adapter.chat_stream(messages)  # type: ignore[return-value]
        return adapter.chat(messages)
    except LLMAdapterError as e:
        typer.echo(f"❌ {e}", err=True)
        raise typer.Exit(code=1) from e
