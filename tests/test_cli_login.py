"""tests/test_cli_login.py — sisoul login 命令单元测试 (mock provider + 验证 config 写入).

全部测试 mock LLM provider, 不调真 API.
覆盖:
- run_login() 正常路径
- provider alias 规范化
- config.yaml 写入格式
- api_key 加密 + 解密 placeholder
- 验证失败 → exit 1
- 未知 provider → BadParameter
- ollama 无需 api_key
- get_active_adapter() 读 config
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

import click

from sisoul.cli_commands.login import (
    run_login,
    get_active_adapter,
    _read_config,
    _write_config,
    _encrypt_api_key_placeholder,
    _decrypt_api_key_placeholder,
    ALIAS_TO_CANONICAL,
)

# typer.Exit — 跨 typer 0.12 / 0.26 (vendored click) reproducible.
# 早期写法 `TyExit = click.exceptions.Exit` 在 typer 0.26+ aws-us 上 raise
# `typer._click.exceptions.Exit` 不匹配, Mac/aws-us 跨机不一致 (Round 9 真发现).
import typer as _typer
TyExit = _typer.Exit


class TestEncryptDecryptPlaceholder:
    def test_encrypt_returns_enc_prefix(self):
        # 现走 vault encryption: enc:v1:... (libsodium SecretBox) — 兼容旧 enc:b64:
        result = _encrypt_api_key_placeholder("sk-test-key")
        assert result.startswith("enc:v1:") or result.startswith("enc:b64:")

    def test_decrypt_roundtrip(self):
        original = "sk-ant-mykey123"
        encrypted = _encrypt_api_key_placeholder(original)
        decrypted = _decrypt_api_key_placeholder(encrypted)
        assert decrypted == original

    def test_decrypt_plain_text_passthrough(self):
        """兼容迁移期: 不带 enc: 前缀的直接返回."""
        result = _decrypt_api_key_placeholder("plain-key")
        assert result == "plain-key"

    def test_encrypted_different_from_original(self):
        key = "sk-visible-key"
        encrypted = _encrypt_api_key_placeholder(key)
        assert encrypted != key


class TestAliasToCanonical:
    def test_claude_alias(self):
        assert ALIAS_TO_CANONICAL["claude"] == "claude"

    def test_anthropic_alias(self):
        assert ALIAS_TO_CANONICAL["anthropic"] == "claude"

    def test_gpt_alias(self):
        assert ALIAS_TO_CANONICAL["gpt"] == "openai"

    def test_google_alias(self):
        assert ALIAS_TO_CANONICAL["google"] == "gemini"

    def test_local_alias(self):
        assert ALIAS_TO_CANONICAL["local"] == "ollama"


class TestRunLogin:
    def test_login_claude_writes_config(self, tmp_path: Path):
        """claude login → config.yaml 有 active_provider=claude."""
        config_path = tmp_path / "config.yaml"
        mock_verify = MagicMock(return_value=(True, "pong"))

        with patch("sisoul.cli_commands.login._verify_provider", mock_verify):
            config = run_login(
                provider="claude",
                api_key="sk-test",
                config_path=config_path,
                skip_verify=False,
                interactive=False,
            )

        assert config["active_provider"] == "claude"
        assert "claude" in config.get("providers", {})

    def test_login_writes_yaml_file(self, tmp_path: Path):
        """config.yaml 文件真的被写入."""
        config_path = tmp_path / "config.yaml"
        mock_verify = MagicMock(return_value=(True, "pong"))

        with patch("sisoul.cli_commands.login._verify_provider", mock_verify):
            run_login(
                provider="claude",
                api_key="sk-test",
                config_path=config_path,
                skip_verify=False,
                interactive=False,
            )

        assert config_path.exists()
        with open(config_path) as f:
            data = yaml.safe_load(f)
        assert data["active_provider"] == "claude"

    def test_login_encrypts_api_key_in_config(self, tmp_path: Path):
        """config.yaml 里 api_key 不是 plaintext."""
        config_path = tmp_path / "config.yaml"
        mock_verify = MagicMock(return_value=(True, "pong"))

        with patch("sisoul.cli_commands.login._verify_provider", mock_verify):
            run_login(
                provider="claude",
                api_key="sk-visible-key",
                config_path=config_path,
                skip_verify=False,
                interactive=False,
            )

        with open(config_path) as f:
            data = yaml.safe_load(f)
        stored_key = data["providers"]["claude"]["api_key"]
        assert stored_key != "sk-visible-key"
        assert stored_key.startswith("enc:v1:") or stored_key.startswith("enc:b64:")

    def test_login_alias_anthropic_saves_as_claude(self, tmp_path: Path):
        """anthropic alias → active_provider = claude."""
        config_path = tmp_path / "config.yaml"
        mock_verify = MagicMock(return_value=(True, "pong"))

        with patch("sisoul.cli_commands.login._verify_provider", mock_verify):
            config = run_login(
                provider="anthropic",  # alias
                api_key="sk-test",
                config_path=config_path,
                skip_verify=True,
                interactive=False,
            )

        assert config["active_provider"] == "claude"

    def test_login_unknown_provider_raises(self, tmp_path: Path):
        """未知 provider → typer.BadParameter."""
        import typer
        config_path = tmp_path / "config.yaml"
        with pytest.raises(typer.BadParameter, match="未知 provider"):
            run_login(
                provider="unknown-xyz",
                api_key="test",
                config_path=config_path,
                interactive=False,
            )

    def test_login_skip_verify(self, tmp_path: Path):
        """skip_verify=True → 不调 _verify_provider."""
        config_path = tmp_path / "config.yaml"
        mock_verify = MagicMock()

        with patch("sisoul.cli_commands.login._verify_provider", mock_verify):
            run_login(
                provider="openai",
                api_key="sk-test",
                config_path=config_path,
                skip_verify=True,
                interactive=False,
            )

        mock_verify.assert_not_called()

    def test_login_verify_failure_exits(self, tmp_path: Path):
        """验证失败 → typer.Exit(code=1)."""
        config_path = tmp_path / "config.yaml"
        mock_verify = MagicMock(return_value=(False, "401 Unauthorized"))

        with patch("sisoul.cli_commands.login._verify_provider", mock_verify):
            with pytest.raises(TyExit) as exc_info:
                run_login(
                    provider="claude",
                    api_key="bad-key",
                    config_path=config_path,
                    skip_verify=False,
                    interactive=False,
                )
        assert exc_info.value.exit_code == 1

    def test_login_ollama_no_api_key(self, tmp_path: Path):
        """ollama 不需要 api_key, api_key=None 写 config."""
        config_path = tmp_path / "config.yaml"
        mock_verify = MagicMock(return_value=(True, "ollama ok"))

        with patch("sisoul.cli_commands.login._verify_provider", mock_verify):
            config = run_login(
                provider="ollama",
                api_key=None,
                config_path=config_path,
                skip_verify=True,
                interactive=False,
            )

        assert config["active_provider"] == "ollama"
        assert config["providers"]["ollama"]["api_key"] is None

    def test_login_overwrites_existing_provider(self, tmp_path: Path):
        """多次 login 同一 provider → 覆写."""
        config_path = tmp_path / "config.yaml"
        mock_verify = MagicMock(return_value=(True, "pong"))

        with patch("sisoul.cli_commands.login._verify_provider", mock_verify):
            run_login(
                provider="openai",
                api_key="sk-first",
                config_path=config_path,
                skip_verify=True,
                interactive=False,
            )
            run_login(
                provider="openai",
                api_key="sk-second",
                config_path=config_path,
                skip_verify=True,
                interactive=False,
            )

        with open(config_path) as f:
            data = yaml.safe_load(f)
        stored = _decrypt_api_key_placeholder(data["providers"]["openai"]["api_key"])
        assert stored == "sk-second"

    def test_login_multiple_providers(self, tmp_path: Path):
        """多 provider login → config 同时保存多个."""
        config_path = tmp_path / "config.yaml"
        mock_verify = MagicMock(return_value=(True, "ok"))

        with patch("sisoul.cli_commands.login._verify_provider", mock_verify):
            run_login(
                provider="claude", api_key="sk-claude", config_path=config_path,
                skip_verify=True, interactive=False,
            )
            run_login(
                provider="openai", api_key="sk-openai", config_path=config_path,
                skip_verify=True, interactive=False,
            )

        with open(config_path) as f:
            data = yaml.safe_load(f)
        assert "claude" in data["providers"]
        assert "openai" in data["providers"]
        assert data["active_provider"] == "openai"  # 最后 login 的成为 active

    def test_login_stores_model_name(self, tmp_path: Path):
        """config.yaml 存 model 名."""
        config_path = tmp_path / "config.yaml"
        with patch("sisoul.cli_commands.login._verify_provider", return_value=(True, "ok")):
            run_login(
                provider="claude", api_key="test", config_path=config_path,
                skip_verify=True, interactive=False,
            )

        with open(config_path) as f:
            data = yaml.safe_load(f)
        assert data["providers"]["claude"]["model"] == "claude-opus-4-7"


class TestGetActiveAdapter:
    def test_returns_adapter_for_active_provider(self, tmp_path: Path):
        """get_active_adapter 从 config 读 active provider, 返回正确 adapter."""
        from sisoul.llm.anthropic import AnthropicAdapter

        config_path = tmp_path / "config.yaml"
        config = {
            "active_provider": "claude",
            "providers": {
                "claude": {
                    "api_key": _encrypt_api_key_placeholder("sk-test"),
                    "model": "claude-opus-4-7",
                }
            },
        }
        _write_config(config_path, config)

        adapter = get_active_adapter(config_path=config_path)
        assert isinstance(adapter, AnthropicAdapter)
        assert adapter.api_key == "sk-test"
        assert adapter.model == "claude-opus-4-7"

    def test_no_config_exits(self, tmp_path: Path):
        """config 不存在 → typer.Exit(1)."""
        config_path = tmp_path / "nonexistent.yaml"
        with pytest.raises(TyExit) as exc_info:
            get_active_adapter(config_path=config_path)
        assert exc_info.value.exit_code == 1

    def test_no_active_provider_exits(self, tmp_path: Path):
        """config 里没有 active_provider → typer.Exit(1)."""
        config_path = tmp_path / "config.yaml"
        _write_config(config_path, {"providers": {}})

        with pytest.raises(TyExit) as exc_info:
            get_active_adapter(config_path=config_path)
        assert exc_info.value.exit_code == 1

    def test_ollama_adapter_no_api_key(self, tmp_path: Path):
        """ollama active → api_key=None 的 OllamaAdapter."""
        from sisoul.llm.ollama import OllamaAdapter

        config_path = tmp_path / "config.yaml"
        config = {
            "active_provider": "ollama",
            "providers": {
                "ollama": {
                    "api_key": None,
                    "model": "llama3.2",
                }
            },
        }
        _write_config(config_path, config)

        adapter = get_active_adapter(config_path=config_path)
        assert isinstance(adapter, OllamaAdapter)
