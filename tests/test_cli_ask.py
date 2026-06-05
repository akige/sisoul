"""tests/test_cli_ask.py — sisoul ask 命令单元测试.

mock get_active_adapter + LLMAdapter, 不调真 API.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest

from sisoul.cli_commands.ask import run_ask
from sisoul.llm.base import LLMAdapterError

# typer.Exit — 跨 typer 0.12 / 0.26 (vendored click) reproducible.
import typer as _typer
TyExit = _typer.Exit


def _make_mock_adapter(response: str = "test response") -> MagicMock:
    """构造 mock LLMAdapter."""
    adapter = MagicMock()
    adapter.chat.return_value = response
    adapter.chat_stream.return_value = iter(response.split())
    return adapter


class TestRunAsk:
    def test_run_ask_returns_response(self, tmp_path: Path):
        """run_ask 正常路径: 返回 LLM 响应."""
        from sisoul.cli_commands.login import _write_config, _encrypt_api_key_placeholder
        config_path = tmp_path / "config.yaml"
        _write_config(config_path, {
            "active_provider": "claude",
            "providers": {
                "claude": {
                    "api_key": _encrypt_api_key_placeholder("sk-test"),
                    "model": "claude-opus-4-7",
                }
            }
        })

        mock_adapter = _make_mock_adapter("Hello from Claude!")
        with patch("sisoul.cli_commands.ask.get_active_adapter", return_value=mock_adapter):
            result = run_ask("say hello", config_path=config_path)

        assert result == "Hello from Claude!"

    def test_run_ask_passes_question_as_user_message(self, tmp_path: Path):
        """run_ask 把 question 包成 {"role": "user", "content": question}."""
        mock_adapter = _make_mock_adapter("response")
        with patch("sisoul.cli_commands.ask.get_active_adapter", return_value=mock_adapter):
            run_ask("what is Python?")

        call_args = mock_adapter.chat.call_args
        messages = call_args.args[0]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "what is Python?"

    def test_run_ask_uses_adapter_from_config(self, tmp_path: Path):
        """run_ask 调 get_active_adapter(config_path=...) 传入正确 config_path."""
        config_path = tmp_path / "config.yaml"
        mock_adapter = _make_mock_adapter("ok")

        with patch("sisoul.cli_commands.ask.get_active_adapter", return_value=mock_adapter) as mock_get:
            run_ask("question", config_path=config_path)

        mock_get.assert_called_once_with(config_path=config_path)

    def test_run_ask_llm_error_exits(self, tmp_path: Path):
        """LLMAdapterError → typer.Exit(1)."""
        mock_adapter = MagicMock()
        mock_adapter.chat.side_effect = LLMAdapterError("API rate limit", provider="claude")

        with patch("sisoul.cli_commands.ask.get_active_adapter", return_value=mock_adapter):
            with pytest.raises(TyExit) as exc_info:
                run_ask("question")

        assert exc_info.value.exit_code == 1

    def test_run_ask_stream_mode(self, tmp_path: Path):
        """stream=True → 返回 Iterator, 不直接 str."""
        mock_adapter = _make_mock_adapter("streaming response")

        with patch("sisoul.cli_commands.ask.get_active_adapter", return_value=mock_adapter):
            result = run_ask("question", stream=True)

        # 验证 chat_stream 被调用 (不是 chat)
        mock_adapter.chat_stream.assert_called_once()
        mock_adapter.chat.assert_not_called()

    def test_run_ask_no_config_exits(self, tmp_path: Path):
        """config 不存在 → get_active_adapter 触发 typer.Exit(1)."""
        config_path = tmp_path / "nonexistent.yaml"

        # get_active_adapter 会抛 typer.Exit(code=1) = click.exceptions.Exit
        with pytest.raises(TyExit) as exc_info:
            run_ask("question", config_path=config_path)

        assert exc_info.value.exit_code == 1


class TestCliAskCLI:
    """测试 typer CLI 包装 (cli_ask 函数)."""

    def test_cli_ask_invoke(self, tmp_path: Path):
        """通过 typer runner 调 cli_ask."""
        from typer.testing import CliRunner
        import typer

        # 构建最小 typer app
        test_app = typer.Typer()

        from sisoul.cli_commands.ask import cli_ask
        test_app.command()(cli_ask)

        runner = CliRunner()
        mock_adapter = _make_mock_adapter("test answer")

        with patch("sisoul.cli_commands.ask.get_active_adapter", return_value=mock_adapter):
            # mock chat_stream to return iter
            mock_adapter.chat_stream.return_value = iter(["test answer"])
            result = runner.invoke(test_app, ["say hello"])

        # 默认流式, 所以 chat_stream 被调
        assert result.exit_code == 0
        assert "test answer" in result.stdout

    def test_cli_ask_no_stream_flag(self, tmp_path: Path):
        """--no-stream → 调 chat() 不调 chat_stream()."""
        from typer.testing import CliRunner
        import typer

        test_app = typer.Typer()
        from sisoul.cli_commands.ask import cli_ask
        test_app.command()(cli_ask)

        runner = CliRunner()
        mock_adapter = _make_mock_adapter("sync response")

        with patch("sisoul.cli_commands.ask.get_active_adapter", return_value=mock_adapter):
            result = runner.invoke(test_app, ["say hello", "--no-stream"])

        assert result.exit_code == 0
        assert "sync response" in result.stdout
        mock_adapter.chat.assert_called_once()
        mock_adapter.chat_stream.assert_not_called()

    def test_cli_ask_requires_question(self):
        """无 question 参数 → exit != 0."""
        from typer.testing import CliRunner
        import typer

        test_app = typer.Typer()
        from sisoul.cli_commands.ask import cli_ask
        test_app.command()(cli_ask)

        runner = CliRunner()
        result = runner.invoke(test_app, [])
        assert result.exit_code != 0
