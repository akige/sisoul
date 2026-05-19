"""tests for P3-5 跨链 attest (Arbitrum / Base / zkSync 扩 + mainnet hard gate).

覆盖:
- CHAIN_REGISTRY 4 chain 完整字段
- resolve_chain short / 全名 / 未知 / mainnet 全 case
- MAINNET_BLOCKED_CHAINS 一律拒
- _verify_testnet_rpc 各 chain mock RPC + chain_id 校验 (mismatch → EASError)
- upload_batch readonly 各 testnet 走 live-readonly
- upload_batch mainnet 走双 gate
- verify_attestation_onchain 各 chain → 对应 easscan endpoint
- list_history_onchain 各 chain → 对应 endpoint
- _easscan_graphql_url 表完整
- daemon /sisoul/attest/flush?chain= + /verify?chain= + /history?chain=
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from sisoul.onchain.eas import (
    ARBITRUM_SEPOLIA_CHAIN_ID,
    BASE_SEPOLIA_CHAIN_ID,
    CHAIN_ID_BY_NETWORK,
    CHAIN_REGISTRY,
    ChainConfig,
    EAS_CONTRACT_ARBITRUM_SEPOLIA,
    EAS_CONTRACT_BASE_SEPOLIA,
    EAS_CONTRACT_ZKSYNC_SEPOLIA,
    EASError,
    MAINNET_BLOCKED_CHAINS,
    NetworkNotSupportedError,
    OPTIMISM_SEPOLIA_CHAIN_ID,
    SHORT_TO_NETWORK,
    ZKSYNC_SEPOLIA_CHAIN_ID,
    AttestConfig,
    AttestQueue,
    AuditAttestation,
    list_history_onchain,
    resolve_chain,
    upload_batch,
    verify_attestation_onchain,
)
from sisoul.onchain.eas import _verify_testnet_rpc, _easscan_graphql_url  # type: ignore[attr-defined]


# ── 1. CHAIN_REGISTRY 完整性 ─────────────────────────────────────────────────


def test_chain_registry_has_4_chains():
    assert set(CHAIN_REGISTRY.keys()) == {"optimism", "arbitrum", "base", "zksync"}


def test_chain_registry_addresses():
    assert CHAIN_REGISTRY["arbitrum"].eas_contract == EAS_CONTRACT_ARBITRUM_SEPOLIA
    assert CHAIN_REGISTRY["base"].eas_contract == EAS_CONTRACT_BASE_SEPOLIA
    assert CHAIN_REGISTRY["zksync"].eas_contract == EAS_CONTRACT_ZKSYNC_SEPOLIA


def test_chain_registry_ids():
    assert CHAIN_REGISTRY["optimism"].chain_id == OPTIMISM_SEPOLIA_CHAIN_ID
    assert CHAIN_REGISTRY["arbitrum"].chain_id == ARBITRUM_SEPOLIA_CHAIN_ID
    assert CHAIN_REGISTRY["base"].chain_id == BASE_SEPOLIA_CHAIN_ID
    assert CHAIN_REGISTRY["zksync"].chain_id == ZKSYNC_SEPOLIA_CHAIN_ID
    # 全 4 chain 都不是 mainnet
    assert all(not cc.is_mainnet for cc in CHAIN_REGISTRY.values())


def test_short_to_network_mapping_full():
    assert SHORT_TO_NETWORK == {
        "optimism": "optimism-sepolia",
        "arbitrum": "arbitrum-sepolia",
        "base": "base-sepolia",
        "zksync": "zksync-sepolia",
    }


def test_chain_id_by_network_completeness():
    assert set(CHAIN_ID_BY_NETWORK.keys()) == {
        "optimism-sepolia",
        "arbitrum-sepolia",
        "base-sepolia",
        "zksync-sepolia",
    }


# ── 2. resolve_chain ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("short", ["optimism", "arbitrum", "base", "zksync"])
def test_resolve_chain_short(short: str):
    cc = resolve_chain(short)
    assert isinstance(cc, ChainConfig)
    assert cc.name == SHORT_TO_NETWORK[short]


@pytest.mark.parametrize(
    "fullname",
    ["optimism-sepolia", "arbitrum-sepolia", "base-sepolia", "zksync-sepolia"],
)
def test_resolve_chain_fullname(fullname: str):
    cc = resolve_chain(fullname)
    assert cc.name == fullname


def test_resolve_chain_case_insensitive():
    cc = resolve_chain("ARBITRUM")
    assert cc.name == "arbitrum-sepolia"


def test_resolve_chain_unknown():
    with pytest.raises(NetworkNotSupportedError):
        resolve_chain("polygon")


@pytest.mark.parametrize(
    "mainnet",
    ["optimism-mainnet", "arbitrum-mainnet", "base-mainnet", "zksync-mainnet"],
)
def test_resolve_chain_mainnet_blocked(mainnet: str):
    with pytest.raises(NetworkNotSupportedError):
        resolve_chain(mainnet)


def test_mainnet_blocked_chains_set():
    assert MAINNET_BLOCKED_CHAINS == {
        "optimism-mainnet",
        "arbitrum-mainnet",
        "base-mainnet",
        "zksync-mainnet",
    }


# ── 3. _verify_testnet_rpc mock RPC chain_id ─────────────────────────────────


@pytest.mark.parametrize(
    "network,expected_chain_id",
    [
        ("optimism-sepolia", OPTIMISM_SEPOLIA_CHAIN_ID),
        ("arbitrum-sepolia", ARBITRUM_SEPOLIA_CHAIN_ID),
        ("base-sepolia", BASE_SEPOLIA_CHAIN_ID),
        ("zksync-sepolia", ZKSYNC_SEPOLIA_CHAIN_ID),
    ],
)
def test_verify_testnet_rpc_ok(network: str, expected_chain_id: int):
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json = MagicMock(return_value={"result": hex(expected_chain_id)})

    with patch("httpx.post", return_value=fake_resp):
        # 不抛 = 通过
        _verify_testnet_rpc("https://fake.rpc", network)


def test_verify_testnet_rpc_chain_id_mismatch():
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json = MagicMock(return_value={"result": hex(1)})  # mainnet chain_id=1

    with patch("httpx.post", return_value=fake_resp):
        with pytest.raises(EASError, match="chain_id 不匹配"):
            _verify_testnet_rpc("https://fake.rpc", "arbitrum-sepolia")


def test_verify_testnet_rpc_mainnet_rejected():
    with pytest.raises(NetworkNotSupportedError):
        _verify_testnet_rpc("https://fake", "optimism-mainnet")


# ── 4. upload_batch readonly each chain ──────────────────────────────────────


@pytest.mark.parametrize(
    "short,expected_network",
    [
        ("optimism", "optimism-sepolia"),
        ("arbitrum", "arbitrum-sepolia"),
        ("base", "base-sepolia"),
        ("zksync", "zksync-sepolia"),
    ],
)
def test_upload_batch_readonly_each_chain(tmp_path: Path, short: str, expected_network: str):
    cc = resolve_chain(short)
    cfg = AttestConfig(
        network=cc.name,  # type: ignore[arg-type]
        rpc_url=cc.rpc_url,
        batch_size=10,
        private_key_path=None,
    )
    q = AttestQueue(db_path=tmp_path / "q.db")
    att = AuditAttestation.from_audit_payload(
        actor_did="did:sisoul:tester",
        action_type="rm",
        target="/tmp/x",
        prompt="hi",
        tool_name="pytest",
    )
    q.enqueue(att)

    # mock RPC OK
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json = MagicMock(return_value={"result": hex(cc.chain_id)})

    with patch("httpx.post", return_value=fake_resp):
        result = upload_batch(q, cfg, force=True)

    assert result.network == expected_network
    assert result.method == "live-readonly"
    assert result.count == 1
    q.close()


def test_upload_batch_readonly_rpc_failed_falls_back_to_mock(tmp_path: Path):
    cc = resolve_chain("base")
    cfg = AttestConfig(
        network=cc.name,  # type: ignore[arg-type]
        rpc_url=cc.rpc_url,
        private_key_path=None,
    )
    q = AttestQueue(db_path=tmp_path / "q.db")
    att = AuditAttestation.from_audit_payload(
        actor_did="did:sisoul:t", action_type="x", target="/", prompt="p", tool_name="t"
    )
    q.enqueue(att)

    def raise_oserror(*a, **kw):
        raise OSError("network down")

    with patch("httpx.post", side_effect=raise_oserror):
        result = upload_batch(q, cfg, force=True)
    assert result.method == "mock"
    q.close()


# ── 5. upload_batch mainnet double-gate ──────────────────────────────────────


@pytest.mark.parametrize(
    "mainnet",
    ["optimism-mainnet", "arbitrum-mainnet", "base-mainnet", "zksync-mainnet"],
)
def test_upload_batch_mainnet_blocked(tmp_path: Path, monkeypatch, mainnet: str):
    cfg = AttestConfig(network=mainnet)  # type: ignore[arg-type]
    q = AttestQueue(db_path=tmp_path / "q.db")
    q.enqueue(
        AuditAttestation.from_audit_payload(
            actor_did="d", action_type="x", target="/", prompt="p", tool_name="t"
        )
    )
    monkeypatch.delenv("EAS_ALLOW_MAINNET", raising=False)
    with pytest.raises(NetworkNotSupportedError):
        upload_batch(q, cfg, force=True)
    q.close()


# ── 6. easscan endpoint mapping ──────────────────────────────────────────────


def test_easscan_url_each_chain():
    assert "optimism-sepolia" in _easscan_graphql_url("optimism-sepolia")
    assert "arbitrum-sepolia" in _easscan_graphql_url("arbitrum-sepolia")
    assert "base-sepolia" in _easscan_graphql_url("base-sepolia")
    assert "zksync-sepolia" in _easscan_graphql_url("zksync-sepolia")


def test_easscan_url_unknown_raises():
    with pytest.raises(EASError):
        _easscan_graphql_url("polygon-amoy")


# ── 7. verify_attestation_onchain per-chain endpoint dispatch ────────────────


@pytest.mark.parametrize(
    "network,expected_host",
    [
        ("optimism-sepolia", "optimism-sepolia.easscan.org"),
        ("arbitrum-sepolia", "arbitrum-sepolia.easscan.org"),
        ("base-sepolia", "base-sepolia.easscan.org"),
        ("zksync-sepolia", "zksync-sepolia.easscan.org"),
    ],
)
def test_verify_onchain_endpoint_dispatch(network: str, expected_host: str):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        m = MagicMock()
        m.raise_for_status = MagicMock()
        m.json = MagicMock(return_value={"data": {"attestation": None}})
        return m

    with patch("httpx.post", side_effect=fake_post):
        r = verify_attestation_onchain("0xabc", network=network)  # type: ignore[arg-type]
    assert expected_host in captured["url"]
    assert r["valid"] is False  # null attestation


def test_verify_onchain_mock_returns_not_found():
    r = verify_attestation_onchain("0xabc", network="mock")
    assert r["valid"] is False
    assert "mock" in r["reason"]


@pytest.mark.parametrize(
    "mainnet",
    ["optimism-mainnet", "arbitrum-mainnet", "base-mainnet", "zksync-mainnet"],
)
def test_verify_onchain_mainnet_blocked(mainnet: str):
    with pytest.raises(NetworkNotSupportedError):
        verify_attestation_onchain("0x", network=mainnet)  # type: ignore[arg-type]


# ── 8. list_history_onchain per-chain endpoint ───────────────────────────────


@pytest.mark.parametrize(
    "network,expected_host",
    [
        ("optimism-sepolia", "optimism-sepolia.easscan.org"),
        ("arbitrum-sepolia", "arbitrum-sepolia.easscan.org"),
        ("base-sepolia", "base-sepolia.easscan.org"),
        ("zksync-sepolia", "zksync-sepolia.easscan.org"),
    ],
)
def test_list_history_onchain_per_chain(network: str, expected_host: str):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        m = MagicMock()
        m.raise_for_status = MagicMock()
        m.json = MagicMock(return_value={"data": {"attestations": []}})
        return m

    with patch("httpx.post", side_effect=fake_post):
        out = list_history_onchain(network=network, limit=5)  # type: ignore[arg-type]
    assert expected_host in captured["url"]
    assert out == []


# ── 9. daemon route ?chain= 路由 ────────────────────────────────────────────


def test_daemon_attest_flush_chain_query(tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sisoul.daemon_routes.attest import attest_router, audit_router

    app = FastAPI()
    app.include_router(attest_router)
    app.include_router(audit_router)
    client = TestClient(app)

    q_db = str(tmp_path / "q.db")
    # 入队 1 条
    r = client.post(
        "/sisoul/audit",
        json={
            "action_type": "rm",
            "target": "/tmp/x",
            "prompt": "p",
            "tool_name": "pytest",
            "actor_did": "did:sisoul:tester",
            "queue_db": q_db,
            "auto_flush": False,
        },
    )
    assert r.status_code == 200

    # flush ?chain=arbitrum — RPC mock 通
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock()
    fake_resp.json = MagicMock(return_value={"result": hex(ARBITRUM_SEPOLIA_CHAIN_ID)})
    with patch("httpx.post", return_value=fake_resp):
        r2 = client.post(
            "/sisoul/attest/flush?chain=arbitrum",
            json={"force": True, "queue_db": q_db},
        )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["network"] == "arbitrum-sepolia"
    assert body["method"] == "live-readonly"


def test_daemon_attest_history_chain_query():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sisoul.daemon_routes.attest import attest_router

    app = FastAPI()
    app.include_router(attest_router)
    client = TestClient(app)

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        m = MagicMock()
        m.raise_for_status = MagicMock()
        m.json = MagicMock(return_value={"data": {"attestations": []}})
        return m

    with patch("httpx.post", side_effect=fake_post):
        r = client.get(
            "/sisoul/attest/history?source=onchain&chain=base&limit=5"
        )
    assert r.status_code == 200, r.text
    assert "base-sepolia" in captured["url"]


def test_daemon_attest_flush_mainnet_rejected(tmp_path: Path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sisoul.daemon_routes.attest import attest_router

    app = FastAPI()
    app.include_router(attest_router)
    client = TestClient(app)
    monkeypatch.delenv("EAS_ALLOW_MAINNET", raising=False)

    # mainnet 全名走 query → resolve_chain 拒
    r = client.post(
        "/sisoul/attest/flush?chain=optimism-mainnet", json={"force": True}
    )
    assert r.status_code == 403
