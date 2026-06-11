"""M5 异步 borrow daemon endpoints 测试.

POST /sisoul/borrow/async/submit  → 200 + task_id (真投到 transport)
GET  /sisoul/borrow/async/collect → 200 + results (e2e: lender 处理后拿真结果)

用 set_default_transport 注入共享 MemoryTransport; SISOUL_VAULT 临时隔离 vault
(带 seed.txt). 不用 `with TestClient` (避免触发 daemon startup 起 kubo).
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from fastapi.testclient import TestClient

from sisoul.chat.transport import (
    MemoryTransport,
    set_default_transport,
)
from sisoul.daemon import create_app
from sisoul.friend.async_task import async_serve_loop, async_task_topic_for
from sisoul.identity.did_key import generate_did_key_from_master
from sisoul.identity.seed import (
    generate_mnemonic,
    mnemonic_to_master_key,
    save_mnemonic_to_file,
)


def _make_vault(tmp_path, name: str):
    vault = tmp_path / name
    vault.mkdir()
    mnemonic = generate_mnemonic(128)
    save_mnemonic_to_file(mnemonic, vault / "seed.txt")
    master = mnemonic_to_master_key(mnemonic)
    did, _priv, _pub = generate_did_key_from_master(master, index=0)
    return vault, did


def _mock_llm(prompt, model, provider="openai", api_key=None, **kw):
    response = f"MOCK[{model}]:{prompt.upper()}"
    return response, max(1, len(prompt) // 4), max(1, len(response) // 4)


@pytest.fixture
def _clean_default_transport():
    yield
    set_default_transport(None)


def test_submit_endpoint_200_and_task_id(tmp_path, monkeypatch, _clean_default_transport):
    b_vault, _b_did = _make_vault(tmp_path, "borrower")
    _l_vault, l_did = _make_vault(tmp_path, "lender")  # 真 did:key
    transport = MemoryTransport()
    set_default_transport(transport)
    monkeypatch.setenv("SISOUL_VAULT", str(b_vault))

    client = TestClient(create_app(), raise_server_exceptions=False)
    r = client.post(
        "/sisoul/borrow/async/submit",
        json={"lender_did": l_did, "model": "gpt-test",
              "provider": "openai", "prompt": "endpoint secret prompt"},
    )
    assert r.status_code == 200, r.text
    tid = r.json()["task_id"]
    assert tid.startswith("atk_")
    # 真投到 transport
    assert len(transport._history[async_task_topic_for(l_did)]) == 1


def test_submit_endpoint_400_missing_fields(tmp_path, monkeypatch, _clean_default_transport):
    b_vault, _ = _make_vault(tmp_path, "borrower")
    set_default_transport(MemoryTransport())
    monkeypatch.setenv("SISOUL_VAULT", str(b_vault))
    client = TestClient(create_app(), raise_server_exceptions=False)
    r = client.post("/sisoul/borrow/async/submit", json={"model": "m"})
    assert r.status_code == 400, r.text
    assert "error" in r.json()


def test_collect_endpoint_200_empty(tmp_path, monkeypatch, _clean_default_transport):
    b_vault, _ = _make_vault(tmp_path, "borrower")
    set_default_transport(MemoryTransport())
    monkeypatch.setenv("SISOUL_VAULT", str(b_vault))
    client = TestClient(create_app(), raise_server_exceptions=False)
    r = client.get("/sisoul/borrow/async/collect", params={"timeout": 0.3})
    assert r.status_code == 200, r.text
    assert r.json()["results"] == []


def test_submit_collect_roundtrip_via_endpoints(tmp_path, monkeypatch, _clean_default_transport):
    """e2e: POST submit → lender serve loop → GET collect 拿真解密结果."""
    b_vault, b_did = _make_vault(tmp_path, "borrower")
    l_vault, l_did = _make_vault(tmp_path, "lender")
    (l_vault / "didkey_friends.json").write_text(json.dumps([{"did": b_did}]))

    transport = MemoryTransport()
    set_default_transport(transport)
    client = TestClient(create_app(), raise_server_exceptions=False)

    # 1. borrower 投递
    monkeypatch.setenv("SISOUL_VAULT", str(b_vault))
    r = client.post(
        "/sisoul/borrow/async/submit",
        json={"lender_did": l_did, "model": "gpt-test", "prompt": "rt secret"},
    )
    assert r.status_code == 200, r.text
    tid = r.json()["task_id"]

    # 2. lender 上线处理 (独立线程独立 loop)
    def _serve():
        async def _go():
            os.environ["SISOUL_VAULT"] = str(l_vault)
            serve = asyncio.create_task(
                async_serve_loop(transport, l_did, vault_dir=l_vault, forwarder=_mock_llm)
            )
            await asyncio.sleep(1.0)
            serve.cancel()
            try:
                await serve
            except asyncio.CancelledError:
                pass
        asyncio.run(_go())

    import threading
    th = threading.Thread(target=_serve)
    th.start()
    th.join()

    # 3. borrower collect
    monkeypatch.setenv("SISOUL_VAULT", str(b_vault))
    r2 = client.get("/sisoul/borrow/async/collect", params={"timeout": 5})
    assert r2.status_code == 200, r2.text
    results = r2.json()["results"]
    assert len(results) == 1
    assert results[0]["task_id"] == tid
    assert results[0]["status"] == "done"
    assert results[0]["text"] == "MOCK[gpt-test]:RT SECRET"
