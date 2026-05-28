"""Wave B' P0-3 · did:key + friend libsodium box 集成测试.

覆盖:
- Alice / Bob 不同 BIP-39 → 不同 did:key
- did:key 解出 pubkey → 喂给 EncryptedProxy 真做 libsodium box encrypt/decrypt
- friend add CLI 集成
- 跟 did:sisoul (mock DID) 共存
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from sisoul.cli_commands.friend import friend_app
from sisoul.friend.encrypted_proxy import EncryptedProxy
from sisoul.identity.did_key import (
    did_key_to_pubkey,
    generate_did_key_from_master,
    verify_did_key,
)
from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def alice_master():
    return mnemonic_to_master_key(generate_mnemonic(128))


@pytest.fixture
def bob_master():
    return mnemonic_to_master_key(generate_mnemonic(128))


class TestAliceBobDidKeyIdentity:
    def test_alice_bob_distinct_did_keys(self, alice_master, bob_master):
        alice_did = generate_did_key_from_master(alice_master)[0]
        bob_did = generate_did_key_from_master(bob_master)[0]
        assert alice_did != bob_did
        assert verify_did_key(alice_did)
        assert verify_did_key(bob_did)

    def test_did_key_to_pubkey_roundtrip(self, alice_master):
        alice_did, alice_priv, alice_pub = generate_did_key_from_master(alice_master)
        assert did_key_to_pubkey(alice_did) == alice_pub.encode()


class TestDidKeyBoxRoundtrip:
    def test_alice_encrypt_to_bob_did_key(self, alice_master, bob_master):
        bob_did, bob_priv, bob_pub = generate_did_key_from_master(bob_master)
        alice_did, alice_priv, alice_pub = generate_did_key_from_master(alice_master)

        alice_proxy = EncryptedProxy(
            self_priv=alice_priv, self_pub=alice_pub, self_did=alice_did
        )
        bob_pub_bytes = did_key_to_pubkey(bob_did)
        plaintext = "borrow request from Alice (1k token Claude opus)"
        encrypted = alice_proxy.encrypt_for(bob_pub_bytes, plaintext)
        assert encrypted != plaintext.encode()
        assert len(encrypted) >= 24 + 16 + len(plaintext)

        bob_proxy = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_pub, self_did=bob_did
        )
        decrypted = bob_proxy.decrypt_from(did_key_to_pubkey(alice_did), encrypted)
        assert decrypted.decode("utf-8") == plaintext

    def test_bidirectional_did_key_box(self, alice_master, bob_master):
        alice_did, alice_priv, alice_pub = generate_did_key_from_master(alice_master)
        bob_did, bob_priv, bob_pub = generate_did_key_from_master(bob_master)

        alice = EncryptedProxy(self_priv=alice_priv, self_pub=alice_pub, self_did=alice_did)
        bob = EncryptedProxy(self_priv=bob_priv, self_pub=bob_pub, self_did=bob_did)

        enc_ab = alice.encrypt_for(did_key_to_pubkey(bob_did), "hi bob")
        assert bob.decrypt_from(did_key_to_pubkey(alice_did), enc_ab).decode() == "hi bob"

        enc_ba = bob.encrypt_for(did_key_to_pubkey(alice_did), "hi alice")
        assert alice.decrypt_from(did_key_to_pubkey(bob_did), enc_ba).decode() == "hi alice"

    def test_wrong_pubkey_fails_decrypt(self, alice_master, bob_master):
        from sisoul.friend.encrypted_proxy import ProxyDecryptError

        alice_did, alice_priv, alice_pub = generate_did_key_from_master(alice_master)
        bob_did, bob_priv, bob_pub = generate_did_key_from_master(bob_master)
        eve_master = mnemonic_to_master_key(generate_mnemonic(128))
        eve_did, eve_priv, eve_pub = generate_did_key_from_master(eve_master)

        alice = EncryptedProxy(self_priv=alice_priv, self_pub=alice_pub, self_did=alice_did)
        eve = EncryptedProxy(self_priv=eve_priv, self_pub=eve_pub, self_did=eve_did)

        enc = alice.encrypt_for(did_key_to_pubkey(bob_did), "secret")
        with pytest.raises(ProxyDecryptError):
            eve.decrypt_from(did_key_to_pubkey(bob_did), enc)


class TestFriendAddCli:
    def test_add_did_key_friend(self, runner, tmp_path, bob_master):
        bob_did = generate_did_key_from_master(bob_master)[0]

        result = runner.invoke(
            friend_app,
            ["add", bob_did, "--nickname", "Bob", "--vault-dir", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "added" in result.output.lower() or "OK" in result.output

        fp = tmp_path / "identity" / "didkey_friends.json"
        assert fp.exists()
        entries = json.loads(fp.read_text())
        assert len(entries) == 1
        assert entries[0]["did"] == bob_did
        assert entries[0]["nickname"] == "Bob"
        assert entries[0]["method"] == "did:key"

    def test_add_invalid_did_key_fails(self, runner, tmp_path):
        result = runner.invoke(
            friend_app,
            ["add", "did:key:zINVALID0OIl", "--vault-dir", str(tmp_path)],
        )
        assert result.exit_code == 1
        assert "ERROR" in result.output or "格式错误" in result.output

    def test_add_did_sisoul_rejected(self, runner, tmp_path):
        result = runner.invoke(
            friend_app,
            ["add", "did:sisoul:alice", "--vault-dir", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_add_idempotent(self, runner, tmp_path, bob_master):
        bob_did = generate_did_key_from_master(bob_master)[0]

        r1 = runner.invoke(
            friend_app, ["add", bob_did, "--vault-dir", str(tmp_path)]
        )
        assert r1.exit_code == 0
        r2 = runner.invoke(
            friend_app, ["add", bob_did, "--nickname", "Bob v2", "--vault-dir", str(tmp_path)]
        )
        assert r2.exit_code == 0
        fp = tmp_path / "identity" / "didkey_friends.json"
        entries = json.loads(fp.read_text())
        assert len(entries) == 1
        assert entries[0]["nickname"] == "Bob v2"

    def test_add_json_output(self, runner, tmp_path, bob_master):
        bob_did = generate_did_key_from_master(bob_master)[0]
        result = runner.invoke(
            friend_app,
            ["add", bob_did, "--vault-dir", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["action"] in ("added", "updated")
        assert data["record"]["did"] == bob_did


class TestCoexistenceWithDidSisoul:
    def test_both_methods_resolvable(self, tmp_path, alice_master):
        from sisoul.identity.did import register_did, resolve_did

        registry = tmp_path / "dids.json"
        did_sisoul = register_did(
            "alicetest",
            network="mock",
            master_seed=alice_master,
            registry_path=registry,
        )

        did_key_str = generate_did_key_from_master(alice_master)[0]

        resolved_sisoul = resolve_did(did_sisoul.did_string, registry_path=registry)
        assert resolved_sisoul.method == "sisoul"
        assert resolved_sisoul.handle == "alicetest"

        resolved_key = resolve_did(did_key_str, registry_path=registry)
        assert resolved_key.method == "key"
        assert resolved_key.did_string == did_key_str
