"""M5 异步任务投递 (async_task.py) 测试.

核心验证: 借入方先 submit_task (借出方 serve loop 还没起) → 借出方"上线"后
照样取到任务执行 → 借入方 collect_results 拿到解密真结果 — 证明双方不必
同时在线. 全程 MemoryTransport (history deque 提供离线留存语义).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from sisoul.chat.transport import MemoryTransport
from sisoul.friend.async_task import (
    AsyncTask,
    async_result_topic_for,
    async_task_topic_for,
    collect_results,
    submit_task,
    async_serve_loop,
)
from sisoul.identity.did_key import generate_did_key_from_master
from sisoul.identity.seed import (
    generate_mnemonic,
    mnemonic_to_master_key,
    save_mnemonic_to_file,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_vault(tmp_path, name: str):
    """建一个带 seed.txt 的隔离 vault, 返回 (vault_dir, did)."""
    vault = tmp_path / name
    vault.mkdir()
    mnemonic = generate_mnemonic(128)
    save_mnemonic_to_file(mnemonic, vault / "seed.txt")
    master = mnemonic_to_master_key(mnemonic)
    did, _priv, _pub = generate_did_key_from_master(master, index=0)
    return vault, did


def _add_friend(lender_vault, borrower_did: str) -> None:
    """往 lender vault 写 didkey_friends.json (proxy_p2p._is_friend 查的路径)."""
    p = lender_vault / "didkey_friends.json"
    entries = json.loads(p.read_text()) if p.exists() else []
    entries.append({"did": borrower_did})
    p.write_text(json.dumps(entries))


def _mock_llm(prompt, model, provider="openai", api_key=None, **kw):
    """mock forwarder: echo 解密后的 prompt — 证 lender 端真解密成功."""
    response = f"MOCK[{model}]:{prompt.upper()}"
    return response, max(1, len(prompt) // 4), max(1, len(response) // 4)


@pytest.fixture
def two_vaults(tmp_path, monkeypatch):
    """borrower + lender 两套 vault; lender 把 borrower 加好友.

    SISOUL_VAULT 指向 lender vault — serve loop 端 _is_friend 读 env.
    borrower 侧 API 都显式传 vault_dir, 不受 env 影响.
    """
    b_vault, b_did = _make_vault(tmp_path, "borrower")
    l_vault, l_did = _make_vault(tmp_path, "lender")
    _add_friend(l_vault, b_did)
    monkeypatch.setenv("SISOUL_VAULT", str(l_vault))
    return b_vault, b_did, l_vault, l_did


# ── AsyncTask 序列化 ──────────────────────────────────────────────────────────


def test_async_task_dict_roundtrip():
    t = AsyncTask(
        task_id="atk_abc123",
        borrower_did="did:key:zBorrower",
        lender_did="did:key:zLender",
        model="gpt-test",
        provider="openai",
        encrypted_prompt_b64="c2VhbGVk",
        mode="strong-tie-auto",
        created_ts=1_760_000_000,
        status="queued",
        lend_request_id="lr_1",
    )
    d = t.to_dict()
    # dict 可 JSON 化 (要上 wire)
    restored = AsyncTask.from_dict(json.loads(json.dumps(d)))
    assert restored == t


def test_async_task_topics_distinct_and_deterministic():
    a = async_task_topic_for("did:key:zAAA")
    b = async_result_topic_for("did:key:zAAA")
    assert a.startswith("/sisoul/async-task/v1/")
    assert b.startswith("/sisoul/async-result/v1/")
    assert a != b
    assert a == async_task_topic_for("did:key:zAAA")  # deterministic


# ── 核心 e2e: 先投递, 后上线 ──────────────────────────────────────────────────


def test_e2e_offline_submit_then_lender_comes_online(two_vaults):
    """M5 核心: lender 离线时投 2 个任务 → lender 上线 → borrower 收齐真结果."""
    b_vault, b_did, l_vault, l_did = two_vaults

    async def _run():
        transport = MemoryTransport()

        # 1. borrower 投递 — 此刻 lender serve loop 根本没起 ("睡前丢任务")
        tid1 = await submit_task(
            transport, b_did, l_did, "gpt-test", "secret prompt alpha-123",
            provider="openai", vault_dir=b_vault, created_ts=1_760_000_000,
        )
        tid2 = await submit_task(
            transport, b_did, l_did, "gpt-test", "second question beta-456",
            provider="openai", vault_dir=b_vault, created_ts=1_760_000_001,
        )
        assert tid1 != tid2

        # 2. lender "早上上线" — 起 serve loop, 从 transport 留存取到旧任务
        serve = asyncio.create_task(
            async_serve_loop(transport, l_did, vault_dir=l_vault, forwarder=_mock_llm)
        )
        try:
            # 3. borrower 收结果
            results = await collect_results(
                transport, b_did, timeout=10.0, vault_dir=b_vault, max_results=2,
            )
        finally:
            serve.cancel()
            with pytest.raises(asyncio.CancelledError):
                await serve
        return tid1, tid2, results

    tid1, tid2, results = asyncio.run(_run())

    assert {r["task_id"] for r in results} == {tid1, tid2}
    by_id = {r["task_id"]: r for r in results}
    # 真结果: mock forwarder echo 解密后 prompt → 证 lender 真解密 + borrower 真解密
    assert by_id[tid1]["status"] == "done"
    assert by_id[tid1]["text"] == "MOCK[gpt-test]:SECRET PROMPT ALPHA-123"
    assert by_id[tid2]["status"] == "done"
    assert by_id[tid2]["text"] == "MOCK[gpt-test]:SECOND QUESTION BETA-456"
    assert by_id[tid1]["model_used"] == "gpt-test"
    assert by_id[tid1]["prompt_tokens"] > 0


# ── 加密: wire 上无明文 ───────────────────────────────────────────────────────


def test_wire_payload_contains_no_plaintext_prompt(two_vaults):
    b_vault, b_did, l_vault, l_did = two_vaults
    secret = "MAGIC_PLAINTEXT_TOKEN_2026"

    async def _run():
        transport = MemoryTransport()
        await submit_task(
            transport, b_did, l_did, "gpt-test", f"prompt with {secret} inside",
            vault_dir=b_vault, created_ts=1_760_000_000,
        )
        # MemoryTransport history = 实际 wire bytes
        return list(transport._history[async_task_topic_for(l_did)])

    payloads = asyncio.run(_run())
    assert len(payloads) == 1
    raw = payloads[0]
    assert secret.encode() not in raw
    # 封包里 encrypted_prompt_b64 也不含明文子串
    body = json.loads(raw)["body"]
    assert secret not in body["encrypted_prompt_b64"]


# ── 非好友被拒 ────────────────────────────────────────────────────────────────


def test_non_friend_task_denied(two_vaults, tmp_path):
    """陌生人 (不在 lender 好友列表) 的任务 → denied, 无结果文本."""
    _b_vault, _b_did, l_vault, l_did = two_vaults
    s_vault, s_did = _make_vault(tmp_path, "stranger")  # 没 _add_friend

    async def _run():
        transport = MemoryTransport()
        tid = await submit_task(
            transport, s_did, l_did, "gpt-test", "stranger prompt",
            vault_dir=s_vault, created_ts=1_760_000_000,
        )
        serve = asyncio.create_task(
            async_serve_loop(transport, l_did, vault_dir=l_vault, forwarder=_mock_llm)
        )
        try:
            results = await collect_results(
                transport, s_did, timeout=10.0, vault_dir=s_vault, max_results=1,
            )
        finally:
            serve.cancel()
            with pytest.raises(asyncio.CancelledError):
                await serve
        return tid, results

    tid, results = asyncio.run(_run())
    assert len(results) == 1
    assert results[0]["task_id"] == tid
    assert results[0]["status"] == "denied"
    assert results[0]["text"] is None
    assert "friend" in results[0]["reason"]


# ── 超时无结果不崩 ────────────────────────────────────────────────────────────


def test_collect_results_timeout_returns_empty(two_vaults):
    b_vault, b_did, _l_vault, _l_did = two_vaults

    async def _run():
        transport = MemoryTransport()  # 没人投结果
        return await collect_results(
            transport, b_did, timeout=0.3, vault_dir=b_vault,
        )

    results = asyncio.run(_run())
    assert results == []
