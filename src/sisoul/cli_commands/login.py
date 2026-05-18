"""sisoul login 命令 (Phase 1 W5).

sisoul login --provider X [--api-key KEY]

流程:
1. provider alias 规范化 (anthropic → claude, gpt → openai 等)
2. api_key: --api-key 传入 > 读 provider 对应 env var > interactive prompt
3. 验证: chat("say 'pong'", max_tokens=10) 看是否返回 (1 token 回环)
4. 写 ~/.sisoul/config.yaml (active_provider + api_key_encrypted placeholder)
5. 输出: logged in / 验证响应

config.yaml 结构:
    active_provider: claude
    providers:
      claude:
        api_key: "<TODO: vault encryption W4>"
        model: claude-opus-4-7
      openai:
        api_key: "<encrypted placeholder>"
        model: gpt-4o

TODO: api_key 加密 — 目前 plaintext (Phase 1 W4 vault encryption 接进来后替换).
TODO: 调 vault encryption 的 encrypt_bytes, 用 master_key_hash 派生加密.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer
import yaml

from sisoul.llm import PROVIDER_ALIASES, LLMAdapterError, get_adapter

# config 文件路径
DEFAULT_CONFIG_DIR = Path.home() / ".sisoul"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.yaml"

# provider env var 映射 (用于 hint 用户)
PROVIDER_ENV_VARS: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "ollama": "",  # 无 key
    "openrouter": "OPENROUTER_API_KEY",
}

# provider → 标准名映射 (alias → canonical)
ALIAS_TO_CANONICAL: dict[str, str] = {
    "claude": "claude",
    "anthropic": "claude",
    "openai": "openai",
    "gpt": "openai",
    "gpt4o": "openai",
    "gemini": "gemini",
    "google": "gemini",
    "ollama": "ollama",
    "local": "ollama",
    "openrouter": "openrouter",
}


def _read_config(config_path: Path) -> dict:
    """读 config.yaml. 不存在 → 返回空 dict."""
    if not config_path.exists():
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data
    except Exception:
        return {}


def _write_config(config_path: Path, config: dict) -> None:
    """写 config.yaml. 自动建目录."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _encrypt_api_key_placeholder(api_key: str) -> str:
    """API key 加密占位 (TODO: Phase 1 W4 vault encryption 接进来后替换真加密).

    目前: base64 编码 (非加密, 只是混淆), 实际应用 libsodium SecretBox.
    """
    import base64
    # TODO: 替换为 vault.encrypt_bytes(api_key.encode(), master_key) 后 base64
    encoded = base64.b64encode(api_key.encode()).decode()
    return f"enc:b64:{encoded}"  # 加前缀方便后续迁移识别


def _decrypt_api_key_placeholder(encrypted: str) -> str:
    """解密 placeholder (配对 _encrypt_api_key_placeholder).

    TODO: 真加密时替换.
    """
    import base64
    if encrypted.startswith("enc:b64:"):
        return base64.b64decode(encrypted[8:]).decode()
    return encrypted  # 兼容老格式 (迁移期)


def _verify_provider(provider: str, api_key: str | None) -> tuple[bool, str]:
    """验证 provider: chat "say 'pong'" max_tokens=10.

    Returns:
        (success: bool, response_or_error: str)
    """
    try:
        adapter = get_adapter(provider, api_key=api_key, model=None)
        response = adapter.chat(
            [{"role": "user", "content": "say 'pong'"}],
            max_tokens=10,
        )
        return True, response.strip()
    except LLMAdapterError as e:
        return False, str(e)
    except Exception as e:
        return False, f"unexpected error: {e}"


def run_login(
    provider: str,
    api_key: str | None = None,
    config_path: Path | None = None,
    skip_verify: bool = False,
    interactive: bool = True,
) -> dict:
    """login 主逻辑. 返回更新后的 config dict.

    Args:
        provider: provider 名称 (alias 或 canonical)
        api_key: API key (None → 读 env 或 interactive prompt)
        config_path: config.yaml 路径 (None → ~/.sisoul/config.yaml)
        skip_verify: 跳过 API 验证 (单元测试 mock 用)
        interactive: False → 不 prompt (非交互模式, 单元测试用)

    Returns:
        更新后的 config dict

    Raises:
        typer.BadParameter: 未知 provider
        typer.Exit(code=1): 验证失败
    """
    config_path = config_path or DEFAULT_CONFIG_PATH

    # 1. 规范化 provider
    canonical = ALIAS_TO_CANONICAL.get(provider.lower().strip())
    if canonical is None:
        supported = sorted(set(ALIAS_TO_CANONICAL.keys()))
        raise typer.BadParameter(
            f"未知 provider: {provider!r}. 支持: {supported}"
        )

    # 2. 获取 api_key
    if canonical == "ollama":
        api_key = None  # ollama 本地无 key
    elif api_key is None:
        # 读 env
        env_var = PROVIDER_ENV_VARS.get(canonical, "")
        if env_var:
            api_key = os.environ.get(env_var)
        if api_key is None and interactive:
            # interactive prompt
            env_hint = f" (或 set {env_var})" if env_var else ""
            api_key = typer.prompt(
                f"{canonical} API key{env_hint}",
                hide_input=True,
            ).strip()
        if api_key is None and canonical != "ollama":
            raise typer.BadParameter(
                f"未提供 API key. 请: --api-key KEY 或 export {PROVIDER_ENV_VARS.get(canonical, 'KEY')}"
            )

    # 3. 验证 (真调 API)
    if not skip_verify:
        typer.echo(f"验证 {canonical}...", err=False)
        success, response = _verify_provider(canonical, api_key)
        if success:
            typer.echo(f"验证响应: {response!r}")
        else:
            typer.echo(f"验证失败: {response}", err=True)
            raise typer.Exit(code=1)
    else:
        response = "(skipped)"

    # 4. 写 config.yaml
    config = _read_config(config_path)
    config["active_provider"] = canonical
    if "providers" not in config:
        config["providers"] = {}

    provider_entry: dict = {"model": _get_default_model(canonical)}
    if api_key:
        # TODO: 替换 _encrypt_api_key_placeholder 为真 vault encryption
        provider_entry["api_key"] = _encrypt_api_key_placeholder(api_key)
    else:
        provider_entry["api_key"] = None

    config["providers"][canonical] = provider_entry
    _write_config(config_path, config)

    typer.echo(f"✅ logged in to {canonical} (model: {provider_entry['model']})")
    typer.echo(f"   config: {config_path}")
    return config


def _get_default_model(canonical: str) -> str:
    """返回各 provider 默认 model."""
    from sisoul.llm import (
        AnthropicAdapter, OpenAIAdapter, GeminiAdapter,
        OllamaAdapter, OpenRouterAdapter,
    )
    defaults = {
        "claude": AnthropicAdapter.DEFAULT_MODEL,
        "openai": OpenAIAdapter.DEFAULT_MODEL,
        "gemini": GeminiAdapter.DEFAULT_MODEL,
        "ollama": OllamaAdapter.DEFAULT_MODEL,
        "openrouter": OpenRouterAdapter.DEFAULT_MODEL,
    }
    return defaults.get(canonical, "unknown")


def get_active_adapter(config_path: Path | None = None):
    """从 config.yaml 读 active provider + api_key, 返回 LLMAdapter 实例.

    被 ask.py 调用.

    Raises:
        typer.Exit(code=1): config 不存在 / provider 未配置
    """
    config_path = config_path or DEFAULT_CONFIG_PATH
    config = _read_config(config_path)

    active = config.get("active_provider")
    if not active:
        typer.echo(
            "❌ 未配置 active provider. 请先: sisoul login --provider claude",
            err=True,
        )
        raise typer.Exit(code=1)

    providers = config.get("providers", {})
    provider_cfg = providers.get(active, {})
    encrypted_key = provider_cfg.get("api_key")
    model = provider_cfg.get("model")

    api_key = None
    if encrypted_key:
        api_key = _decrypt_api_key_placeholder(encrypted_key)

    try:
        return get_adapter(active, api_key=api_key, model=model)
    except LLMAdapterError as e:
        typer.echo(f"❌ adapter 初始化失败: {e}", err=True)
        raise typer.Exit(code=1) from e


# typer CLI 包装 (cli.py 整合用)
def cli_login(
    provider: str = typer.Option(
        ...,
        "--provider",
        "-p",
        help="LLM provider: claude / openai / gemini / ollama / openrouter",
    ),
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        help="API key (也可 set env ANTHROPIC_API_KEY 等)",
    ),
    skip_verify: bool = typer.Option(
        False,
        "--skip-verify",
        help="跳过 API 验证 (测试用)",
    ),
) -> None:
    """接 LLM provider (claude / openai / gemini / ollama / openrouter)."""
    run_login(provider=provider, api_key=api_key, skip_verify=skip_verify)
