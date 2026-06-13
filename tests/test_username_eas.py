"""Tests for sisoul.onchain.username_eas — EAS-backed username registry (Workstream B).

Covers the offline-deterministic surface (derivation, schema UID, encoding, mainnet
gate, indexer-response parsing). The live on-chain send + real easscan query are
exercised separately on mac with funded OP gas.
"""

from __future__ import annotations

import json

import pytest

from sisoul.onchain import username_eas as ue

ANVIL_MNEMONIC = "test test test test test test test test test test test junk"
ANVIL_ADDR0 = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


@pytest.fixture
def seed_file(tmp_path):
    from sisoul.identity.seed import save_mnemonic_to_file

    p = tmp_path / "seed.txt"
    save_mnemonic_to_file(ANVIL_MNEMONIC, p)
    return p


# ── EVM derivation ────────────────────────────────────────────────────────────


def test_derive_evm_deterministic():
    acct = ue.derive_evm_account(ANVIL_MNEMONIC)
    assert acct.address == ANVIL_ADDR0
    assert acct.private_key.startswith("0x") and len(acct.private_key) == 66


def test_evm_public_hides_private_key():
    acct = ue.derive_evm_account(ANVIL_MNEMONIC)
    pub = acct.public()
    assert pub["evm_address"] == ANVIL_ADDR0
    assert "private_key" not in pub
    assert pub["derivation_path"] == ue.EVM_DERIVATION_PATH


def test_load_evm_from_seed_file(seed_file):
    acct = ue.load_evm_account(seed_file)
    assert acct.address == ANVIL_ADDR0


# ── schema UID + data encoding ────────────────────────────────────────────────


def test_schema_uid_deterministic_and_pinned():
    # Must be stable: clients resolve against this UID before anyone registers it.
    assert ue.USERNAME_SCHEMA_UID == ue.compute_schema_uid()
    assert ue.USERNAME_SCHEMA_UID == (
        "0x1055ec1c7948a75e70c097fc3f96abedd31b538b8e4a12b2507176f4b62424ee"
    )
    assert ue.USERNAME_SCHEMA_UID.startswith("0x") and len(ue.USERNAME_SCHEMA_UID) == 66


def test_encode_username_data():
    blob, issued = ue.encode_username_data("alice", "did:key:z6MkABC", issued_at=1_717_000_000)
    assert blob.startswith("0x")
    assert issued == 1_717_000_000


# ── build plan (offline) ──────────────────────────────────────────────────────


def test_build_register_plan(seed_file):
    plan = ue.build_register_plan(
        "alice", "did:key:z6MkABC", network="optimism-sepolia", seed_path=seed_file
    )
    assert plan["method"] == "dry-run"
    assert plan["evm_address"] == ANVIL_ADDR0
    assert plan["schema_uid"] == ue.USERNAME_SCHEMA_UID
    assert plan["network"] == "optimism-sepolia"
    assert plan["is_mainnet"] is False
    assert plan["data"].startswith("0x")


def test_register_dry_run_sends_nothing(seed_file):
    # dry_run must never touch the network — works even with no RPC.
    res = ue.register_username(
        "alice", "did:key:z6MkABC", network="optimism-mainnet", seed_path=seed_file, dry_run=True
    )
    assert res["method"] == "dry-run"
    assert res["is_mainnet"] is True
    assert res["evm_address"] == ANVIL_ADDR0


# ── mainnet gate ──────────────────────────────────────────────────────────────


def test_mainnet_blocked_without_gate(seed_file, monkeypatch):
    monkeypatch.delenv(ue.ALLOW_MAINNET_ENV, raising=False)
    with pytest.raises(ue.MainnetBlockedError):
        ue.register_username(
            "alice", "did:key:z6MkABC", network="optimism-mainnet",
            seed_path=seed_file, dry_run=False, allow_mainnet=True,
        )


def test_mainnet_blocked_without_allow_flag(seed_file, monkeypatch):
    monkeypatch.setenv(ue.ALLOW_MAINNET_ENV, "1")
    with pytest.raises(ue.MainnetBlockedError):
        ue.register_username(
            "alice", "did:key:z6MkABC", network="optimism-mainnet",
            seed_path=seed_file, dry_run=False, allow_mainnet=False,
        )


# ── resolve / discover parsing (mock indexer) ─────────────────────────────────


def _att(username, did, t):
    decoded = json.dumps(
        [
            {"name": "username", "value": {"name": "username", "type": "string", "value": username}},
            {"name": "did_key", "value": {"name": "did_key", "type": "string", "value": did}},
            {"name": "issued_at", "value": {"name": "issued_at", "type": "uint64", "value": t}},
        ]
    )
    return {"attester": "0xabc", "time": t, "decodedDataJson": decoded}


def test_resolve_first_claim_wins(monkeypatch):
    # ordered asc by time: alice claimed by didA first, then a squatter didB.
    rows = {"attestations": [_att("alice", "did:key:zA", 100), _att("alice", "did:key:zB", 200)]}
    monkeypatch.setattr(ue, "_easscan_query", lambda *a, **k: rows)
    assert ue.resolve_username("alice", network="optimism-mainnet") == "did:key:zA"


def test_resolve_no_match(monkeypatch):
    rows = {"attestations": [_att("alice", "did:key:zA", 100)]}
    monkeypatch.setattr(ue, "_easscan_query", lambda *a, **k: rows)
    assert ue.resolve_username("bob", network="optimism-mainnet") is None


def test_discover_dedups_by_username(monkeypatch):
    rows = {"attestations": [_att("alice", "did:key:zA", 200), _att("alice", "did:key:zB", 100)]}
    monkeypatch.setattr(ue, "_easscan_query", lambda *a, **k: rows)
    got = ue.discover(network="optimism-mainnet")
    assert len(got) == 1 and got[0]["username"] == "alice"
    assert got[0]["did_key"] == "did:key:zA"  # most recent kept (desc order)


# ── #12: helios trustless chain_id 校验接上 ───────────────────────────────────


class _FakeChainStatus:
    def __init__(self, in_sync=True, mode="helios"):
        self.in_sync = in_sync
        self.mode = mode


class _FakeHelios:
    def __init__(self, status, chain_id_hex="0xa"):
        self._status = status
        self._chain_id_hex = chain_id_hex
        self.calls = []

    def status(self, chain):
        return self._status

    def call_sync(self, chain, method, params, **kw):
        self.calls.append((chain, method))
        if method == "eth_chainId":
            return self._chain_id_hex
        raise AssertionError(method)


def test_helios_chain_id_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("SISOUL_HELIOS_DISABLE", "1")
    assert ue._helios_chain_id("optimism-mainnet") is None


def test_helios_chain_id_unsupported_chain_returns_none(monkeypatch):
    monkeypatch.delenv("SISOUL_HELIOS_DISABLE", raising=False)
    # op-sepolia maps to "" (helios 0.11.1 不原生支持) → None (走公共 RPC)
    assert ue._helios_chain_id("optimism-sepolia") is None


def test_helios_chain_id_trustless_when_in_sync(monkeypatch):
    monkeypatch.delenv("SISOUL_HELIOS_DISABLE", raising=False)
    fake = _FakeHelios(_FakeChainStatus(in_sync=True, mode="helios"), chain_id_hex="0xa")
    import sisoul.rpc.helios_client as hc
    monkeypatch.setattr(hc, "get_default_client", lambda: fake)
    cid = ue._helios_chain_id("optimism-mainnet")  # op-mainnet → 10 == 0xa
    assert cid == 10
    assert ("op-mainnet", "eth_chainId") in fake.calls


def test_helios_chain_id_not_in_sync_returns_none(monkeypatch):
    monkeypatch.delenv("SISOUL_HELIOS_DISABLE", raising=False)
    fake = _FakeHelios(_FakeChainStatus(in_sync=False, mode="helios"))
    import sisoul.rpc.helios_client as hc
    monkeypatch.setattr(hc, "get_default_client", lambda: fake)
    assert ue._helios_chain_id("optimism-mainnet") is None


def test_helios_chain_id_no_client_returns_none(monkeypatch):
    monkeypatch.delenv("SISOUL_HELIOS_DISABLE", raising=False)
    import sisoul.rpc.helios_client as hc
    monkeypatch.setattr(hc, "get_default_client", lambda: None)
    assert ue._helios_chain_id("optimism-mainnet") is None


class _FakeW3Eth:
    def __init__(self, chain_id):
        self.chain_id = chain_id


class _FakeW3:
    def __init__(self, chain_id):
        self.eth = _FakeW3Eth(chain_id)


def test_verify_chain_id_trustless_path(monkeypatch):
    """helios 给出正确 chain_id → mode=trustless, 不碰公共 RPC."""
    chain = ue.resolve_chain("optimism-mainnet")
    monkeypatch.setattr(ue, "_helios_chain_id", lambda network: 10)
    # w3 故意给错 (如果被用就会 mismatch); helios 优先 → 应忽略 w3
    cid, mode = ue._verify_chain_id(_FakeW3(999), chain)
    assert cid == 10
    assert mode == "trustless"


def test_verify_chain_id_trusted_fallback(monkeypatch):
    """helios 不可用 → 退公共 RPC (w3.eth.chain_id), mode=trusted."""
    chain = ue.resolve_chain("optimism-sepolia")
    monkeypatch.setattr(ue, "_helios_chain_id", lambda network: None)
    cid, mode = ue._verify_chain_id(_FakeW3(ue.OPTIMISM_SEPOLIA_CHAIN_ID), chain)
    assert cid == ue.OPTIMISM_SEPOLIA_CHAIN_ID
    assert mode == "trusted"


def test_verify_chain_id_mismatch_raises(monkeypatch):
    """RPC/helios 报错的链 → UsernameEASError (防发错链)."""
    chain = ue.resolve_chain("optimism-sepolia")
    monkeypatch.setattr(ue, "_helios_chain_id", lambda network: None)
    with pytest.raises(ue.UsernameEASError, match="chain_id"):
        ue._verify_chain_id(_FakeW3(999999), chain)
