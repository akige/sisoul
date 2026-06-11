"""M5 异步 borrow CLI 接入测试 (sisoul borrow async-submit / async-collect).

验证 CLI 真把 prompt 投到 transport (打回 task_id), 借出方 serve loop 处理后,
async-collect 真收回解密结果. 全程 MemoryTransport (monkeypatch
``_resolve_async_transport`` 注入共享 bus).
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from typer.testing import CliRunner

import sisoul.cli_commands.borrow as borrow_cli
from sisoul.chat.transport import MemoryTransport
from sisoul.cli_commands.borrow import borrow_app
from sisoul.friend.async_task import async_serve_loop
from sisoul.identity.did_key import generate_did_key_from_master
from sisoul.identity.seed import (
    generate_mnemonic,
    mnemonic_to_master_key,
    save_mnemonic_to_file,
)

runner = CliRunner()


def _make_vault(tmp_path, name: str):
    vault = tmp_path / name
    vault.mkdir()
    mnemonic = generate_mnemonic(128)
    save_mnemonic_to_file(mnemonic, vault / "seed.txt")
    master = mnemonic_to_master_key(mnemonic)
    did, _priv, _pub = generate_did_key_from_master(master, index=0)
    return vault, did


def _add_friend(lender_vault, borrower_did: str) -> None:
    p = lender_vault / "didkey_friends.json"
    p.write_text(json.dumps([{"did": borrower_did}]))


def _mock_llm(prompt, model, provider="openai", api_key=None, **kw):
    response = f"MOCK[{model}]:{prompt.upper()}"
    return response, max(1, len(prompt) // 4), max(1, len(response) // 4)


def test_cli_async_submit_returns_task_id(tmp_path, monkeypatch):
    """async-submit 真把 prompt 投到 transport, 打印 task_id."""
    b_vault, _b_did = _make_vault(tmp_path, "borrower")
    _l_vault, l_did = _make_vault(tmp_path, "lender")  # 真 did:key (X25519)
    transport = MemoryTransport()
    monkeypatch.setattr(borrow_cli, "_resolve_async_transport", lambda: transport)
    monkeypatch.setenv("SISOUL_VAULT", str(b_vault))

    result = runner.invoke(
        borrow_app,
        ["async-submit", "--lender", l_did,
         "--model", "gpt-test", "--prompt", "hello async borrow", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["submitted"] == 1
    assert payload["task_ids"][0].startswith("atk_")
    # 真投到 transport: lender task topic history 有 1 条
    from sisoul.friend.async_task import async_task_topic_for
    assert len(transport._history[async_task_topic_for(l_did)]) == 1
    # prompt 明文不在 wire / 不在 CLI 输出
    assert "hello async borrow" not in result.output


def test_cli_async_submit_multiple_prompts(tmp_path, monkeypatch):
    b_vault, _ = _make_vault(tmp_path, "borrower")
    _l_vault, l_did = _make_vault(tmp_path, "lender")
    transport = MemoryTransport()
    monkeypatch.setattr(borrow_cli, "_resolve_async_transport", lambda: transport)
    monkeypatch.setenv("SISOUL_VAULT", str(b_vault))

    result = runner.invoke(
        borrow_app,
        ["async-submit", "--lender", l_did, "--model", "m",
         "-p", "q1", "-p", "q2", "-p", "q3", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["submitted"] == 3
    assert len(set(payload["task_ids"])) == 3


def test_cli_submit_then_collect_roundtrip(tmp_path, monkeypatch):
    """e2e: CLI submit → lender serve loop 处理 → CLI collect 拿真解密结果."""
    b_vault, b_did = _make_vault(tmp_path, "borrower")
    l_vault, l_did = _make_vault(tmp_path, "lender")
    _add_friend(l_vault, b_did)
    transport = MemoryTransport()
    monkeypatch.setattr(borrow_cli, "_resolve_async_transport", lambda: transport)

    # 1. borrower 投递 (lender 还没起 serve loop)
    monkeypatch.setenv("SISOUL_VAULT", str(b_vault))
    sub = runner.invoke(
        borrow_app,
        ["async-submit", "--lender", l_did, "--model", "gpt-test",
         "--prompt", "secret prompt zzz", "--json"],
    )
    assert sub.exit_code == 0, sub.output
    tid = json.loads(sub.output)["task_ids"][0]

    # 2. lender "上线": 起 serve loop 处理留存任务 (独立线程跑独立 loop)
    def _serve():
        async def _go():
            import os
            os.environ["SISOUL_VAULT"] = str(l_vault)  # _is_friend 读 env
            serve = asyncio.create_task(
                async_serve_loop(transport, l_did, vault_dir=l_vault, forwarder=_mock_llm)
            )
            await asyncio.sleep(1.0)  # 让它处理完 + 回投结果
            serve.cancel()
            try:
                await serve
            except asyncio.CancelledError:
                pass
        asyncio.run(_go())

    th = threading.Thread(target=_serve)
    th.start()
    th.join()

    # 3. borrower collect — 拿回解密真结果
    monkeypatch.setenv("SISOUL_VAULT", str(b_vault))
    col = runner.invoke(
        borrow_app,
        ["async-collect", "--timeout", "5", "--max", "1", "--json"],
    )
    assert col.exit_code == 0, col.output
    results = json.loads(col.output)["results"]
    assert len(results) == 1
    assert results[0]["task_id"] == tid
    assert results[0]["status"] == "done"
    assert results[0]["text"] == "MOCK[gpt-test]:SECRET PROMPT ZZZ"
