"""tests for sisoul.identity.did (Phase 2 W21-W22, dev-B).

覆盖:
- DID 数据结构 + W3C DID Document 序列化
- handle 校验 (合法 / 非法 / 长度边界)
- ENS subdomain 计算 + namehash
- ENS 注册 (mock + mainnet 拒绝)
- DID 注册流程 + 重复 check
- resolve (本地 registry hit / miss / 双格式)
- Privy social recovery mock (确定性 + 反向 case)
- 朋友关系 stub
- live testnet smoke (默认 skip, SISOUL_TEST_LIVE_TESTNET=1 才跑)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sisoul.identity.did import (
    DID,
    DIDError,
    DIDNotFoundError,
    HandleAlreadyTakenError,
    InvalidHandleError,
    NetworkNotSupportedError,
    ServiceEndpoint,
    SISOUL_ENS_ROOT,
    compute_ens_subdomain,
    compute_namehash,
    derive_public_key,
    link_friend_did,
    link_social_recovery,
    list_local_dids,
    load_registry,
    register_did,
    register_ens_subdomain,
    resolve_did,
    save_registry,
    validate_handle,
)


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "sisoul-test"


@pytest.fixture
def registry_path(vault_root: Path) -> Path:
    return vault_root / "identity" / "dids.json"


# ── handle 校验 ─────────────────────────────────────────────────────────────


class TestValidateHandle:
    def test_lowercase_valid(self) -> None:
        assert validate_handle("alice") == "alice"

    def test_strip_and_lowercase(self) -> None:
        assert validate_handle("  Alice  ") == "alice"

    def test_with_digits_hyphen(self) -> None:
        assert validate_handle("alice-007") == "alice-007"

    def test_min_length_3(self) -> None:
        assert validate_handle("abc") == "abc"

    def test_too_short(self) -> None:
        with pytest.raises(InvalidHandleError, match="至少 3"):
            validate_handle("ab")

    def test_too_long(self) -> None:
        with pytest.raises(InvalidHandleError, match="最多 63"):
            validate_handle("a" * 64)

    def test_empty(self) -> None:
        with pytest.raises(InvalidHandleError, match="不能为空"):
            validate_handle("")

    def test_not_str(self) -> None:
        with pytest.raises(InvalidHandleError):
            validate_handle(123)  # type: ignore[arg-type]

    def test_leading_hyphen_reject(self) -> None:
        with pytest.raises(InvalidHandleError, match="非法字符"):
            validate_handle("-alice")

    def test_trailing_hyphen_reject(self) -> None:
        with pytest.raises(InvalidHandleError, match="非法字符"):
            validate_handle("alice-")

    def test_special_char_reject(self) -> None:
        with pytest.raises(InvalidHandleError):
            validate_handle("alice@bob")


# ── ENS subdomain + namehash ─────────────────────────────────────────────────


class TestEnsSubdomain:
    def test_compute(self) -> None:
        assert compute_ens_subdomain("alice") == "alice.sisoul.eth"

    def test_compute_normalizes(self) -> None:
        assert compute_ens_subdomain("ALICE") == "alice.sisoul.eth"

    def test_namehash_deterministic(self) -> None:
        h1 = compute_namehash("alice.sisoul.eth")
        h2 = compute_namehash("alice.sisoul.eth")
        assert h1 == h2
        assert h1.startswith("0x")
        assert len(h1) == 66  # 0x + 32 bytes hex

    def test_namehash_differs_per_name(self) -> None:
        assert compute_namehash("alice.sisoul.eth") != compute_namehash("bob.sisoul.eth")

    def test_namehash_empty_is_zero(self) -> None:
        assert compute_namehash("") == "0x" + "0" * 64


# ── public key 派生 ─────────────────────────────────────────────────────────


class TestDerivePublicKey:
    def test_deterministic_from_seed(self) -> None:
        seed = b"deadbeef" * 8
        k1 = derive_public_key("alice", master_seed=seed)
        k2 = derive_public_key("alice", master_seed=seed)
        assert k1 == k2

    def test_differs_per_handle(self) -> None:
        seed = b"deadbeef" * 8
        assert derive_public_key("alice", master_seed=seed) != derive_public_key(
            "bob", master_seed=seed
        )

    def test_seed_priority_over_social(self) -> None:
        seed = b"deadbeef" * 8
        k_seed = derive_public_key("alice", master_seed=seed, social_id="x")
        k_social = derive_public_key("alice", social_id="x")
        assert k_seed != k_social

    def test_fallback_to_handle_only(self) -> None:
        # 无 seed 无 social → mock 仍能派生 (确定性)
        k1 = derive_public_key("alice")
        k2 = derive_public_key("alice")
        assert k1 == k2
        assert k1.startswith("z")


# ── ENS 注册 ────────────────────────────────────────────────────────────────


class TestRegisterEnsSubdomain:
    def test_mock_returns_tx_hash(self) -> None:
        r = register_ens_subdomain("alice", "zkey", network="mock", live=False)
        assert r["ens_subdomain"] == "alice.sisoul.eth"
        assert r["tx_hash"].startswith("0x")
        assert r["network"] == "mock"
        assert r["method"] == "mock"

    def test_sepolia_mock_when_not_live(self) -> None:
        r = register_ens_subdomain("alice", "zkey", network="sepolia", live=False)
        assert r["network"] == "sepolia"
        assert r["method"] == "mock"
        assert r["tx_hash"] is not None

    def test_mainnet_rejected(self) -> None:
        with pytest.raises(NetworkNotSupportedError, match="mainnet"):
            register_ens_subdomain("alice", "zkey", network="mainnet")

    def test_invalid_handle_propagates(self) -> None:
        with pytest.raises(InvalidHandleError):
            register_ens_subdomain("a@b", "zkey", network="mock")

    def test_namehash_in_result(self) -> None:
        r = register_ens_subdomain("alice", "zkey", network="mock")
        assert r["namehash"].startswith("0x")
        assert len(r["namehash"]) == 66


# ── DID 数据结构 + 序列化 ────────────────────────────────────────────────────


class TestDIDStruct:
    def test_construct_defaults(self) -> None:
        d = DID(handle="alice", public_key="zkey")
        assert d.method == "sisoul"
        assert d.did_string == "did:sisoul:alice"
        assert d.ens_subdomain == "alice.sisoul.eth"
        assert d.controllers == ["did:sisoul:alice"]
        assert d.created_at  # auto-filled
        assert d.network == "sepolia"

    def test_w3c_did_document(self) -> None:
        d = DID(handle="alice", public_key="zkey123")
        doc = d.to_did_document()
        assert "@context" in doc
        assert doc["id"] == "did:sisoul:alice"
        assert "ens:alice.sisoul.eth" in doc["alsoKnownAs"]
        assert doc["verificationMethod"][0]["publicKeyMultibase"] == "zkey123"
        assert doc["authentication"] == ["did:sisoul:alice#key-1"]

    def test_roundtrip_serialize(self) -> None:
        d = DID(
            handle="alice",
            public_key="zkey",
            services=[
                ServiceEndpoint(
                    id="did:sisoul:alice#daemon",
                    type="SisoulDaemon",
                    service_endpoint="http://127.0.0.1:9876",
                )
            ],
        )
        as_dict = d.to_dict()
        restored = DID.from_dict(as_dict)
        assert restored.handle == d.handle
        assert restored.public_key == d.public_key
        assert len(restored.services) == 1
        assert restored.services[0].service_endpoint == "http://127.0.0.1:9876"


# ── 顶层 register_did ───────────────────────────────────────────────────────


class TestRegisterDid:
    def test_basic_register(self, registry_path: Path) -> None:
        d = register_did("alice", network="mock", registry_path=registry_path)
        assert d.handle == "alice"
        assert d.network == "mock"
        assert d.ens_tx_hash and d.ens_tx_hash.startswith("0x")
        assert registry_path.exists()

    def test_duplicate_rejected(self, registry_path: Path) -> None:
        register_did("alice", network="mock", registry_path=registry_path)
        with pytest.raises(HandleAlreadyTakenError):
            register_did("alice", network="mock", registry_path=registry_path)

    def test_invalid_handle_rejected(self, registry_path: Path) -> None:
        with pytest.raises(InvalidHandleError):
            register_did("", network="mock", registry_path=registry_path)

    def test_mainnet_rejected(self, registry_path: Path) -> None:
        with pytest.raises(NetworkNotSupportedError):
            register_did("alice", network="mainnet", registry_path=registry_path)

    def test_register_with_master_seed(self, registry_path: Path) -> None:
        seed = b"seed" * 16  # 64B
        d = register_did(
            "alice", network="mock", master_seed=seed, registry_path=registry_path
        )
        # 同 seed 派生应一致 (虽然 alice 已 taken, 改 bob)
        d2 = register_did(
            "bob", network="mock", master_seed=seed, registry_path=registry_path
        )
        assert d.public_key != d2.public_key  # 不同 handle 派生不同

    def test_register_with_social(self, registry_path: Path) -> None:
        d = register_did(
            "alice",
            network="mock",
            social_provider="github",
            social_id="user-123",
            registry_path=registry_path,
        )
        assert d.social_provider == "github"
        assert d.social_recovery_id == "user-123"


# ── resolve ─────────────────────────────────────────────────────────────────


class TestResolve:
    def test_resolve_by_did_string(self, registry_path: Path) -> None:
        register_did("alice", network="mock", registry_path=registry_path)
        d = resolve_did("did:sisoul:alice", registry_path=registry_path)
        assert d.handle == "alice"

    def test_resolve_by_ens(self, registry_path: Path) -> None:
        register_did("alice", network="mock", registry_path=registry_path)
        d = resolve_did("alice.sisoul.eth", registry_path=registry_path)
        assert d.handle == "alice"

    def test_resolve_not_found(self, registry_path: Path) -> None:
        with pytest.raises(DIDNotFoundError):
            resolve_did("did:sisoul:ghost", registry_path=registry_path)

    def test_resolve_invalid_format(self, registry_path: Path) -> None:
        with pytest.raises(DIDError, match="无法解析"):
            resolve_did("garbage", registry_path=registry_path)


# ── list_local_dids ─────────────────────────────────────────────────────────


class TestListLocal:
    def test_empty(self, registry_path: Path) -> None:
        assert list_local_dids(registry_path=registry_path) == []

    def test_after_register(self, registry_path: Path) -> None:
        register_did("alice", network="mock", registry_path=registry_path)
        register_did("bob", network="mock", registry_path=registry_path)
        dids = list_local_dids(registry_path=registry_path)
        assert {d.handle for d in dids} == {"alice", "bob"}

    def test_load_registry_corrupt_returns_empty(self, registry_path: Path) -> None:
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text("not json", encoding="utf-8")
        assert load_registry(registry_path) == []

    def test_save_load_roundtrip(self, registry_path: Path) -> None:
        d = DID(handle="alice", public_key="zkey", network="mock")
        save_registry([d.to_dict()], registry_path)
        loaded = list_local_dids(registry_path)
        assert loaded[0].handle == "alice"


# ── Privy social recovery (mock) ────────────────────────────────────────────


class TestSocialRecovery:
    def test_github_with_token(self) -> None:
        r = link_social_recovery("github", oauth_token="gh_abc")
        assert r.provider == "github"
        assert r.user_id  # uuid format
        assert r.embedded_wallet_address.startswith("0x")
        assert len(r.embedded_wallet_address) == 42

    def test_deterministic_same_token(self) -> None:
        r1 = link_social_recovery("github", oauth_token="gh_abc")
        r2 = link_social_recovery("github", oauth_token="gh_abc")
        assert r1.user_id == r2.user_id
        assert r1.embedded_wallet_address == r2.embedded_wallet_address

    def test_different_per_token(self) -> None:
        r1 = link_social_recovery("github", oauth_token="t1")
        r2 = link_social_recovery("github", oauth_token="t2")
        assert r1.user_id != r2.user_id

    def test_email_provider(self) -> None:
        r = link_social_recovery("email", user_email="alice@example.com")
        assert r.provider == "email"

    def test_email_missing_email(self) -> None:
        with pytest.raises(DIDError, match="user_email"):
            link_social_recovery("email")

    def test_oauth_missing_token(self) -> None:
        with pytest.raises(DIDError, match="oauth_token"):
            link_social_recovery("google")

    def test_invalid_provider(self) -> None:
        with pytest.raises(DIDError, match="不支持"):
            link_social_recovery("myspace", oauth_token="x")  # type: ignore[arg-type]


# ── link-friend stub ────────────────────────────────────────────────────────


class TestLinkFriend:
    def test_stub_records(self, registry_path: Path) -> None:
        d = register_did("alice", network="mock", registry_path=registry_path)
        rec = link_friend_did(d, "did:sisoul:bob", registry_path=registry_path)
        assert rec["stub"] is True
        assert rec["own_did"] == "did:sisoul:alice"
        assert rec["friend_did"] == "did:sisoul:bob"
        # friends.json 已落
        friends_fp = registry_path.parent / "friends.json"
        assert friends_fp.exists()

    def test_stub_accepts_ens_form(self, registry_path: Path) -> None:
        d = register_did("alice", network="mock", registry_path=registry_path)
        rec = link_friend_did(d, "bob.sisoul.eth", registry_path=registry_path)
        assert rec["friend_did"] == "bob.sisoul.eth"

    def test_stub_rejects_bad_format(self, registry_path: Path) -> None:
        d = register_did("alice", network="mock", registry_path=registry_path)
        with pytest.raises(DIDError, match="格式不对"):
            link_friend_did(d, "not-a-did", registry_path=registry_path)


# ── live testnet smoke (默认 skip) ─────────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get("SISOUL_TEST_LIVE_TESTNET") != "1",
    reason="需要 SISOUL_TEST_LIVE_TESTNET=1 + 公网 Sepolia RPC",
)
class TestLiveTestnetSmoke:
    def test_sepolia_readonly_chain_id(self) -> None:
        """真连 Sepolia 公共 RPC, 验 chain_id=11155111 + 不发 tx."""
        r = register_ens_subdomain(
            "ciuser",
            "ztestkey",
            network="sepolia",
            live=True,
        )
        assert r["network"] == "sepolia"
        assert r["method"] == "live-readonly"
        assert r["chain_id"] == 11155111
        assert r["tx_hash"] is None  # readonly smoke
