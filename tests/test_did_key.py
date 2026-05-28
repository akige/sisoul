"""Wave B' P0-3 · did:key 测试 (W3C-CCG did:key method)."""

from __future__ import annotations

import pytest
from nacl.public import PrivateKey

from sisoul.identity.did_key import (
    DID_KEY_SCHEME,
    ED25519_PUB_MULTICODEC_PREFIX,
    X25519_PUB_MULTICODEC_PREFIX,
    DidKey,
    DidKeyError,
    InvalidDidKeyFormatError,
    UnsupportedMulticodecError,
    base58btc_decode,
    base58btc_encode,
    decode_did_key,
    derive_did_key_keypair,
    did_key_to_pubkey,
    encode_did_key,
    generate_did_key,
    generate_did_key_from_master,
    verify_did_key,
)
from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key


class TestBase58btc:
    def test_empty(self):
        assert base58btc_encode(b"") == ""
        assert base58btc_decode("") == b""

    def test_single_byte_roundtrip(self):
        for b in [0x00, 0x01, 0x42, 0xFF]:
            data = bytes([b])
            assert base58btc_decode(base58btc_encode(data)) == data

    def test_leading_zeros(self):
        data = b"\x00\x00\x42"
        enc = base58btc_encode(data)
        assert enc.startswith("11")
        assert base58btc_decode(enc) == data

    def test_random_32B_roundtrip(self):
        for _ in range(10):
            data = PrivateKey.generate().public_key.encode()
            assert base58btc_decode(base58btc_encode(data)) == data

    def test_invalid_chars_raise(self):
        with pytest.raises(InvalidDidKeyFormatError):
            base58btc_decode("0OIl")


class TestEncodeDecodeDidKey:
    def test_x25519_roundtrip(self):
        pub = PrivateKey.generate().public_key.encode()
        did = encode_did_key(pub, key_type="X25519")
        assert did.startswith(DID_KEY_SCHEME + "z")
        dk = decode_did_key(did)
        assert dk.pubkey == pub
        assert dk.multicodec_prefix == X25519_PUB_MULTICODEC_PREFIX
        assert dk.key_type == "X25519"

    def test_ed25519_roundtrip(self):
        pub = PrivateKey.generate().public_key.encode()
        did = encode_did_key(pub, key_type="Ed25519")
        dk = decode_did_key(did)
        assert dk.pubkey == pub
        assert dk.multicodec_prefix == ED25519_PUB_MULTICODEC_PREFIX
        assert dk.key_type == "Ed25519"

    def test_invalid_pubkey_length(self):
        with pytest.raises(ValueError, match="32B"):
            encode_did_key(b"x" * 16)

    def test_invalid_key_type(self):
        with pytest.raises(ValueError, match="未知 key_type"):
            encode_did_key(b"x" * 32, key_type="secp256k1")

    def test_decode_missing_scheme(self):
        with pytest.raises(InvalidDidKeyFormatError, match="did:key:"):
            decode_did_key("did:sisoul:alice")

    def test_decode_missing_multibase(self):
        with pytest.raises(InvalidDidKeyFormatError, match="multibase"):
            decode_did_key("did:key:abc")

    def test_decode_truncated_payload(self):
        with pytest.raises(InvalidDidKeyFormatError):
            decode_did_key("did:key:z1")

    def test_decode_unsupported_multicodec(self):
        fake_payload = b"\xe7\x01" + b"\x00" * 33
        fake_did = f"did:key:z{base58btc_encode(fake_payload)}"
        with pytest.raises(UnsupportedMulticodecError):
            decode_did_key(fake_did)

    def test_decode_empty_string(self):
        with pytest.raises(InvalidDidKeyFormatError):
            decode_did_key("")


class TestUtilityFunctions:
    def test_did_key_to_pubkey(self):
        pub = PrivateKey.generate().public_key.encode()
        did = encode_did_key(pub)
        assert did_key_to_pubkey(did) == pub

    def test_verify_did_key_valid(self):
        pub = PrivateKey.generate().public_key.encode()
        assert verify_did_key(encode_did_key(pub)) is True

    def test_verify_did_key_invalid(self):
        assert verify_did_key("did:sisoul:alice") is False
        assert verify_did_key("not-a-did") is False
        assert verify_did_key("") is False
        assert verify_did_key("did:key:zINVALID0OIl") is False


class TestGenerateFromMaster:
    def test_generate_did_key_string(self):
        master = mnemonic_to_master_key(generate_mnemonic(128))
        did = generate_did_key(master)
        assert did.startswith("did:key:z")
        assert verify_did_key(did)

    def test_generate_returns_keypair(self):
        master = mnemonic_to_master_key(generate_mnemonic(128))
        did, priv, pub = generate_did_key_from_master(master)
        assert isinstance(priv, PrivateKey)
        assert priv.public_key.encode() == pub.encode()
        assert did_key_to_pubkey(did) == pub.encode()

    def test_cross_device_consistency(self):
        mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        master1 = mnemonic_to_master_key(mnemonic)
        master2 = mnemonic_to_master_key(mnemonic)
        assert generate_did_key(master1) == generate_did_key(master2)

    def test_passphrase_changes_did(self):
        mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        m_no_pass = mnemonic_to_master_key(mnemonic)
        m_with_pass = mnemonic_to_master_key(mnemonic, passphrase="secret")
        assert generate_did_key(m_no_pass) != generate_did_key(m_with_pass)

    def test_index_separates_keys(self):
        master = mnemonic_to_master_key(generate_mnemonic(128))
        assert generate_did_key(master, index=0) != generate_did_key(master, index=1)
        assert generate_did_key(master, index=1) != generate_did_key(master, index=2)

    def test_invalid_master_seed_raises(self):
        with pytest.raises(ValueError):
            generate_did_key(b"")
        with pytest.raises(ValueError):
            generate_did_key("not-bytes")  # type: ignore[arg-type]

    def test_invalid_index_raises(self):
        master = mnemonic_to_master_key(generate_mnemonic(128))
        with pytest.raises(ValueError):
            generate_did_key(master, index=-1)


class TestDeriveDidKeyKeypair:
    def test_keypair_matches_generate(self):
        master = mnemonic_to_master_key(generate_mnemonic(128))
        did, priv1, pub1 = generate_did_key_from_master(master)
        priv2, pub2 = derive_did_key_keypair(master)
        assert priv1.encode() == priv2.encode()
        assert pub1.encode() == pub2.encode()


class TestResolveDidKeyIntegration:
    def test_resolve_did_key_via_resolve_did(self):
        from sisoul.identity.did import resolve_did
        master = mnemonic_to_master_key(generate_mnemonic(128))
        did_str = generate_did_key(master)
        did_obj = resolve_did(did_str)
        assert did_obj.method == "key"
        assert did_obj.did_string == did_str
        assert did_obj.public_key == did_str[len("did:key:"):]
        assert did_obj.network == "mock"

    def test_resolve_invalid_did_key_raises(self):
        from sisoul.identity.did import DIDError, resolve_did
        with pytest.raises(DIDError):
            resolve_did("did:key:zINVALID0OIl")

    def test_resolve_did_key_no_registry_needed(self, tmp_path):
        from sisoul.identity.did import resolve_did
        master = mnemonic_to_master_key(generate_mnemonic(128))
        did_str = generate_did_key(master)
        did_obj = resolve_did(did_str, registry_path=tmp_path / "empty.json")
        assert did_obj.did_string == did_str
