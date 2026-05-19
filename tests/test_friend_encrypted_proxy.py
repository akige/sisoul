"""Phase 4 W54-W58 · 波 5 dev-B.

测试加密 proxy: libsodium box + per-friend key 派生 + e2e 加密往返.
"""

from __future__ import annotations

import time
import uuid

import pytest

from sisoul.friend.encrypted_proxy import (
    BOX_NONCE_SIZE,
    EncryptedProxy,
    ProxyDecryptError,
    ProxyError,
    ProxyPermissionError,
    ProxySession,
    ProxySessionMetadata,
    derive_friend_session_keypair,
    get_global_proxy,
    set_global_proxy,
)
from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def alice_master() -> bytes:
    return mnemonic_to_master_key(generate_mnemonic(128))


@pytest.fixture
def bob_master() -> bytes:
    return mnemonic_to_master_key(generate_mnemonic(128))


@pytest.fixture
def alice_keypair(alice_master):
    return derive_friend_session_keypair(alice_master, friend_index=0)


@pytest.fixture
def bob_keypair(bob_master):
    return derive_friend_session_keypair(bob_master, friend_index=0)


def _mock_forwarder(prompt, model, provider="anthropic", api_key=None, **kw):
    """mock LLM forwarder: 返"ECHO: <prompt>" + 估算 tokens."""
    response = f"ECHO[{model}]: {prompt}"
    return response, max(1, len(prompt) // 4), max(1, len(response) // 4)


@pytest.fixture
def alice_proxy(alice_keypair):
    priv, pub = alice_keypair
    return EncryptedProxy(
        self_priv=priv, self_pub=pub,
        self_did="alice.sisoul.eth",
        forwarder=_mock_forwarder,
    )


@pytest.fixture
def bob_proxy(bob_keypair):
    priv, pub = bob_keypair
    return EncryptedProxy(
        self_priv=priv, self_pub=pub,
        self_did="bob.sisoul.eth",
        forwarder=_mock_forwarder,
        llm_api_key="sk-fake-bob",
    )


# ── keypair 派生 ─────────────────────────────────────────────────────────────


class TestDeriveKeypair:
    def test_decisive_same_seed_same_keypair(self, alice_master):
        p1, pk1 = derive_friend_session_keypair(alice_master, friend_index=0)
        p2, pk2 = derive_friend_session_keypair(alice_master, friend_index=0)
        assert p1.encode() == p2.encode()
        assert pk1.encode() == pk2.encode()

    def test_different_index_different_keypair(self, alice_master):
        _, pk_0 = derive_friend_session_keypair(alice_master, friend_index=0)
        _, pk_1 = derive_friend_session_keypair(alice_master, friend_index=1)
        assert pk_0.encode() != pk_1.encode()

    def test_different_seed_different_keypair(self, alice_master, bob_master):
        _, pk_a = derive_friend_session_keypair(alice_master, friend_index=0)
        _, pk_b = derive_friend_session_keypair(bob_master, friend_index=0)
        assert pk_a.encode() != pk_b.encode()

    def test_pubkey_32B(self, alice_master):
        _, pk = derive_friend_session_keypair(alice_master, friend_index=0)
        assert len(pk.encode()) == 32

    def test_invalid_seed(self):
        with pytest.raises(ValueError, match="非空 bytes"):
            derive_friend_session_keypair(b"", friend_index=0)

    def test_invalid_index(self, alice_master):
        with pytest.raises(ValueError):
            derive_friend_session_keypair(alice_master, friend_index=-1)


# ── Box 加解密 ────────────────────────────────────────────────────────────────


class TestBoxEncryptDecrypt:
    def test_roundtrip_str(self, alice_proxy, bob_keypair):
        _, bob_pub = bob_keypair
        plaintext = "Alice's secret prompt: how to hack mainframe?"
        blob = alice_proxy.encrypt_for(bob_pub.encode(), plaintext)
        # blob = nonce(24) + ciphertext + mac(16)
        assert len(blob) >= BOX_NONCE_SIZE + 16 + len(plaintext)
        # Bob 解
        bob_priv, _ = bob_keypair
        from sisoul.friend.encrypted_proxy import EncryptedProxy
        bob_dec = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_priv.public_key,
            self_did="bob.sisoul.eth",
        )
        decrypted = bob_dec.decrypt_from(alice_proxy.self_pub.encode(), blob)
        assert decrypted.decode("utf-8") == plaintext

    def test_roundtrip_bytes(self, alice_proxy, bob_keypair):
        _, bob_pub = bob_keypair
        plaintext = b"\x00\xff binary \x01\x02"
        blob = alice_proxy.encrypt_for(bob_pub.encode(), plaintext)
        bob_priv, _ = bob_keypair
        bob_dec = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_priv.public_key,
            self_did="bob.sisoul.eth",
        )
        decrypted = bob_dec.decrypt_from(alice_proxy.self_pub.encode(), blob)
        assert decrypted == plaintext

    def test_wrong_key_fails(self, alice_proxy, bob_keypair, alice_master):
        _, bob_pub = bob_keypair
        blob = alice_proxy.encrypt_for(bob_pub.encode(), "secret")
        # 用错 key 解
        wrong_priv, _ = derive_friend_session_keypair(alice_master, friend_index=99)
        wrong_proxy = EncryptedProxy(
            self_priv=wrong_priv, self_pub=wrong_priv.public_key,
            self_did="x.sisoul.eth",
        )
        with pytest.raises(ProxyDecryptError):
            wrong_proxy.decrypt_from(alice_proxy.self_pub.encode(), blob)

    def test_tampered_blob_fails(self, alice_proxy, bob_keypair):
        _, bob_pub = bob_keypair
        blob = alice_proxy.encrypt_for(bob_pub.encode(), "secret")
        tampered = bytearray(blob)
        tampered[-1] ^= 0x01
        bob_priv, _ = bob_keypair
        bob_dec = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_priv.public_key,
            self_did="bob.sisoul.eth",
        )
        with pytest.raises(ProxyDecryptError):
            bob_dec.decrypt_from(alice_proxy.self_pub.encode(), bytes(tampered))

    def test_invalid_pubkey_size(self, alice_proxy):
        with pytest.raises(ValueError, match="必须 32B"):
            alice_proxy.encrypt_for(b"too-short", "x")

    def test_blob_too_short(self, alice_proxy, bob_keypair):
        _, bob_pub = bob_keypair
        with pytest.raises(ValueError, match="太短"):
            alice_proxy.decrypt_from(bob_pub.encode(), b"x" * 10)


# ── EncryptedProxy class 基础 ────────────────────────────────────────────────


class TestEncryptedProxyInit:
    def test_priv_pub_mismatch_raises(self, alice_keypair, bob_keypair):
        alice_priv, _ = alice_keypair
        _, bob_pub = bob_keypair
        with pytest.raises(ValueError, match="不匹配"):
            EncryptedProxy(
                self_priv=alice_priv, self_pub=bob_pub,
                self_did="x.sisoul.eth",
            )

    def test_empty_did(self, alice_keypair):
        priv, pub = alice_keypair
        with pytest.raises(ValueError, match="非空"):
            EncryptedProxy(self_priv=priv, self_pub=pub, self_did="")


# ── proxy_chat_request E2E ───────────────────────────────────────────────────


class TestProxyChatRequest:
    def test_e2e_roundtrip(self, alice_proxy, bob_proxy):
        prompt = "What's the meaning of life, universe and everything?"
        # Alice 加密 prompt 给 Bob
        encrypted_prompt = alice_proxy.encrypt_for(
            bob_proxy.self_pub.encode(), prompt
        )
        # Bob 收 → decrypt → call LLM (mock) → encrypt response
        encrypted_response, meta = bob_proxy.proxy_chat_request(
            borrower_did="alice.sisoul.eth",
            borrower_pubkey=alice_proxy.self_pub.encode(),
            encrypted_prompt=encrypted_prompt,
            target_model="claude-opus-4-7",
        )
        # Alice 解 response
        response_bytes = alice_proxy.decrypt_from(
            bob_proxy.self_pub.encode(), encrypted_response
        )
        response = response_bytes.decode("utf-8")
        assert response == f"ECHO[claude-opus-4-7]: {prompt}"

        # metadata 校验
        assert meta.borrower_did == "alice.sisoul.eth"
        assert meta.lender_did == "bob.sisoul.eth"
        assert meta.target_model == "claude-opus-4-7"
        assert meta.status == "completed"
        assert meta.prompt_token_count >= 1
        assert meta.response_token_count >= 1
        assert meta.started_ts <= meta.ended_ts
        assert meta.error_class is None

    def test_session_added_to_list(self, alice_proxy, bob_proxy):
        prompt = "session test prompt"
        encrypted_prompt = alice_proxy.encrypt_for(
            bob_proxy.self_pub.encode(), prompt
        )
        _, meta = bob_proxy.proxy_chat_request(
            borrower_did="alice.sisoul.eth",
            borrower_pubkey=alice_proxy.self_pub.encode(),
            encrypted_prompt=encrypted_prompt,
            target_model="claude-opus-4-7",
        )
        sessions = bob_proxy.list_sessions()
        assert any(s.session_id == meta.session_id for s in sessions)

    def test_metadata_safe_dict_no_prompt(self, alice_proxy, bob_proxy):
        prompt = "extremely-confidential-keyword-xyzzy-9876"
        encrypted_prompt = alice_proxy.encrypt_for(
            bob_proxy.self_pub.encode(), prompt
        )
        _, meta = bob_proxy.proxy_chat_request(
            borrower_did="alice.sisoul.eth",
            borrower_pubkey=alice_proxy.self_pub.encode(),
            encrypted_prompt=encrypted_prompt,
            target_model="claude-opus-4-7",
        )
        safe = meta.to_safe_dict()
        # safe dict 任何 value 不该含 prompt 字串
        for k, v in safe.items():
            if isinstance(v, str):
                assert "xyzzy" not in v, f"metadata field {k} 含 prompt 字串"

    def test_permission_denied(self, alice_proxy, bob_keypair):
        bob_priv, bob_pub = bob_keypair

        def deny_all(**kw):
            raise ProxyPermissionError("denied for test")

        bob_proxy = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_pub,
            self_did="bob.sisoul.eth",
            forwarder=_mock_forwarder,
            permission_checker=deny_all,
        )
        encrypted_prompt = alice_proxy.encrypt_for(bob_pub.encode(), "x")
        with pytest.raises(ProxyPermissionError):
            bob_proxy.proxy_chat_request(
                borrower_did="alice.sisoul.eth",
                borrower_pubkey=alice_proxy.self_pub.encode(),
                encrypted_prompt=encrypted_prompt,
                target_model="claude-opus-4-7",
            )
        # 应有 failed session
        sessions = bob_proxy.list_sessions()
        assert any(s.status == "failed" and s.error_class == "ProxyPermissionError"
                   for s in sessions)

    def test_forwarder_error_wrapped(self, alice_proxy, bob_keypair):
        bob_priv, bob_pub = bob_keypair

        def boom(**kw):
            raise RuntimeError("LLM API down")

        bob_proxy = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_pub,
            self_did="bob.sisoul.eth",
            forwarder=boom,
        )
        encrypted_prompt = alice_proxy.encrypt_for(bob_pub.encode(), "x")
        with pytest.raises(ProxyError):
            bob_proxy.proxy_chat_request(
                borrower_did="alice.sisoul.eth",
                borrower_pubkey=alice_proxy.self_pub.encode(),
                encrypted_prompt=encrypted_prompt,
                target_model="claude-opus-4-7",
            )

    def test_corrupt_encrypted_prompt(self, alice_proxy, bob_proxy):
        # 用错的 pubkey 加密 → Bob 解失败
        bad_blob = b"\x00" * 100
        with pytest.raises(ProxyDecryptError):
            bob_proxy.proxy_chat_request(
                borrower_did="alice.sisoul.eth",
                borrower_pubkey=alice_proxy.self_pub.encode(),
                encrypted_prompt=bad_blob,
                target_model="claude-opus-4-7",
            )

    def test_ledger_hook_called(self, alice_proxy, bob_keypair):
        bob_priv, bob_pub = bob_keypair
        captured = []

        def ledger(meta):
            captured.append(meta)

        bob_proxy = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_pub,
            self_did="bob.sisoul.eth",
            forwarder=_mock_forwarder,
            ledger_writer=ledger,
        )
        enc = alice_proxy.encrypt_for(bob_pub.encode(), "test")
        bob_proxy.proxy_chat_request(
            borrower_did="alice.sisoul.eth",
            borrower_pubkey=alice_proxy.self_pub.encode(),
            encrypted_prompt=enc,
            target_model="claude-opus-4-7",
        )
        assert len(captured) == 1
        assert captured[0].status == "completed"

    def test_ledger_writer_exception_swallowed(self, alice_proxy, bob_keypair):
        bob_priv, bob_pub = bob_keypair

        def bad_ledger(meta):
            raise RuntimeError("ledger DB down")

        bob_proxy = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_pub,
            self_did="bob.sisoul.eth",
            forwarder=_mock_forwarder,
            ledger_writer=bad_ledger,
        )
        enc = alice_proxy.encrypt_for(bob_pub.encode(), "test")
        # ledger 故障不阻塞 proxy
        encrypted_response, meta = bob_proxy.proxy_chat_request(
            borrower_did="alice.sisoul.eth",
            borrower_pubkey=alice_proxy.self_pub.encode(),
            encrypted_prompt=enc,
            target_model="claude-opus-4-7",
        )
        assert meta.status == "completed"


# ── session 管理 ─────────────────────────────────────────────────────────────


class TestSessionManagement:
    def test_get_session(self, alice_proxy, bob_proxy):
        enc = alice_proxy.encrypt_for(bob_proxy.self_pub.encode(), "p")
        _, meta = bob_proxy.proxy_chat_request(
            borrower_did="alice.sisoul.eth",
            borrower_pubkey=alice_proxy.self_pub.encode(),
            encrypted_prompt=enc,
            target_model="claude-opus-4-7",
        )
        fetched = bob_proxy.get_session(meta.session_id)
        assert fetched is not None
        assert fetched.session_id == meta.session_id

    def test_get_session_missing(self, bob_proxy):
        assert bob_proxy.get_session("nonexistent") is None

    def test_end_session(self, alice_proxy, bob_proxy):
        enc = alice_proxy.encrypt_for(bob_proxy.self_pub.encode(), "p")
        _, meta = bob_proxy.proxy_chat_request(
            borrower_did="alice.sisoul.eth",
            borrower_pubkey=alice_proxy.self_pub.encode(),
            encrypted_prompt=enc,
            target_model="claude-opus-4-7",
        )
        ended = bob_proxy.end_session(meta.session_id)
        assert ended is not None
        # 再次 end 返 None
        assert bob_proxy.end_session(meta.session_id) is None


# ── global proxy ─────────────────────────────────────────────────────────────


class TestGlobalProxy:
    def test_set_get_clear(self, alice_proxy):
        set_global_proxy(None)
        assert get_global_proxy() is None
        set_global_proxy(alice_proxy)
        assert get_global_proxy() is alice_proxy
        set_global_proxy(None)
        assert get_global_proxy() is None


# ── metadata 白名单 ──────────────────────────────────────────────────────────


class TestMetadataWhitelist:
    def test_safe_dict_only_whitelist(self):
        meta = ProxySessionMetadata(
            session_id="sid",
            borrower_did="a.eth",
            lender_did="b.eth",
            target_model="claude",
            provider="anthropic",
            started_ts=time.time(),
        )
        safe = meta.to_safe_dict()
        from sisoul.friend.encrypted_proxy import _METADATA_WHITELIST
        for k in safe.keys():
            assert k in _METADATA_WHITELIST


# ── 异步 wrapper ─────────────────────────────────────────────────────────────


class TestAsyncWrapper:
    async def test_async_proxy_chat(self, alice_proxy, bob_proxy):
        from sisoul.friend.encrypted_proxy import proxy_chat_request_async

        enc = alice_proxy.encrypt_for(bob_proxy.self_pub.encode(), "async test")
        encrypted_response, meta = await proxy_chat_request_async(
            proxy=bob_proxy,
            borrower_did="alice.sisoul.eth",
            borrower_pubkey=alice_proxy.self_pub.encode(),
            encrypted_prompt=enc,
            target_model="claude-opus-4-7",
        )
        assert meta.status == "completed"
        resp = alice_proxy.decrypt_from(bob_proxy.self_pub.encode(), encrypted_response)
        assert b"async test" in resp


# ── enforce_no_disk_write 静态检查 ───────────────────────────────────────────


class TestEnforceNoDiskWrite:
    def test_no_leak_passes(self, tmp_path):
        # 在 tmp_path 写跟 prompt/response 无关的内容
        (tmp_path / "clean.txt").write_text("nothing-of-interest")
        # 应不抛
        EncryptedProxy.enforce_no_disk_write(
            prompt_substring="UNIQUE-PROMPT-TOKEN-AAAA",
            response_substring="UNIQUE-RESPONSE-TOKEN-BBBB",
            check_paths=[str(tmp_path)],
        )

    def test_leak_detected(self, tmp_path):
        leak_token = f"LEAK-{uuid.uuid4().hex}"
        (tmp_path / "evil.txt").write_text(f"oh no the prompt leaked: {leak_token}")
        from sisoul.friend.encrypted_proxy import ProxyDiskWriteViolation
        with pytest.raises(ProxyDiskWriteViolation):
            EncryptedProxy.enforce_no_disk_write(
                prompt_substring=leak_token,
                response_substring="UNIQUE-RESPONSE-NEVER-SEEN",
                check_paths=[str(tmp_path)],
            )
