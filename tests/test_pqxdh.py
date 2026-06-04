"""Tests for sisoul.chat.pqxdh (Post-Quantum Extended Triple Diffie-Hellman)."""

from __future__ import annotations

import pytest

from sisoul.chat.pqxdh import (
    BadSignatureError,
    PreKeyBundle,
    complete_handshake_initiator,
    complete_handshake_responder,
    generate_pre_key_bundle,
    pqxdh_mode,
    verify_bundle,
)


# 1
def test_backend_mode_is_real_or_shim():
    mode = pqxdh_mode()
    assert mode in ("real", "shim")


# 2
def test_generate_bundle_has_correct_sizes():
    km = generate_pre_key_bundle("did:key:zAlice")
    b = km.bundle
    assert len(b.x25519_pub) == 32, "X25519 pub must be 32 bytes"
    assert len(b.signed_pre_key_pub) == 32, "SPK pub must be 32 bytes"
    assert len(b.mlkem_pub) == 1568, "ML-KEM-1024 pub must be 1568 bytes"
    assert len(b.signature) == 64, "Ed25519 signature must be 64 bytes"
    assert b.did == "did:key:zAlice"


# 3
def test_handshake_roundtrip_real_or_shim():
    """Initiator + responder derive the same 32-byte shared secret."""
    alice = generate_pre_key_bundle("did:key:zAlice")
    bob = generate_pre_key_bundle("did:key:zBob")

    shared_a, ek_pub, ct = complete_handshake_initiator(alice, bob.bundle)
    shared_b = complete_handshake_responder(
        bob, "did:key:zAlice", alice.x25519_identity_pub, ek_pub, ct
    )
    assert shared_a == shared_b
    assert len(shared_a) == 32


# 4
def test_mlkem_ciphertext_correct_size():
    alice = generate_pre_key_bundle("did:key:zAlice")
    bob = generate_pre_key_bundle("did:key:zBob")
    _shared, _ek_pub, ct = complete_handshake_initiator(alice, bob.bundle)
    assert len(ct) == 1568, "ML-KEM-1024 ciphertext must be 1568 bytes"


# 5
def test_bundle_signature_verifies():
    """Self-signed bundle verifies with the matching Ed25519 public key."""
    from nacl.signing import SigningKey

    seed = b"\x01" * 32
    sk = SigningKey(seed)
    vk = bytes(sk.verify_key.encode())
    km = generate_pre_key_bundle("did:key:zVerify", ed25519_sign_priv=seed)
    verify_bundle(km.bundle, vk)  # must not raise


# 6
def test_bundle_signature_rejects_tampering():
    from nacl.signing import SigningKey

    seed = b"\x02" * 32
    vk = bytes(SigningKey(seed).verify_key.encode())
    km = generate_pre_key_bundle("did:key:zTamper", ed25519_sign_priv=seed)
    # Tamper with the X25519 pub
    bad = PreKeyBundle(
        did=km.bundle.did,
        x25519_pub=b"\x00" * 32,
        mlkem_pub=km.bundle.mlkem_pub,
        signed_pre_key_pub=km.bundle.signed_pre_key_pub,
        signature=km.bundle.signature,
        issued_at=km.bundle.issued_at,
        pqxdh_mode=km.bundle.pqxdh_mode,
    )
    with pytest.raises(BadSignatureError):
        verify_bundle(bad, vk)


# 7
def test_handshake_distinct_pairs_yield_distinct_secrets():
    a = generate_pre_key_bundle("did:key:zA")
    b = generate_pre_key_bundle("did:key:zB")
    c = generate_pre_key_bundle("did:key:zC")
    s_ab, _, _ = complete_handshake_initiator(a, b.bundle)
    s_ac, _, _ = complete_handshake_initiator(a, c.bundle)
    assert s_ab != s_ac, "different peers must produce different shared secrets"


# 8
def test_bundle_serialization_roundtrip():
    km = generate_pre_key_bundle("did:key:zSerial")
    js = km.bundle.to_json()
    restored = PreKeyBundle.from_json(js)
    assert restored.did == km.bundle.did
    assert restored.x25519_pub == km.bundle.x25519_pub
    assert restored.mlkem_pub == km.bundle.mlkem_pub
    assert restored.signature == km.bundle.signature
    assert restored.signed_pre_key_pub == km.bundle.signed_pre_key_pub


# 9
def test_handshake_secret_depends_on_did_binding():
    """Same key material but swapped DIDs in HKDF info → different output."""
    alice = generate_pre_key_bundle("did:key:zAlice")
    bob = generate_pre_key_bundle("did:key:zBob")
    s_correct, ek, ct = complete_handshake_initiator(alice, bob.bundle)
    # Responder uses *wrong* initiator DID
    s_wrong = complete_handshake_responder(
        bob, "did:key:zEve", alice.x25519_identity_pub, ek, ct
    )
    assert s_correct != s_wrong


# 10
def test_repeated_handshakes_use_fresh_ephemeral():
    alice = generate_pre_key_bundle("did:key:zRepeatA")
    bob = generate_pre_key_bundle("did:key:zRepeatB")
    s1, ek1, ct1 = complete_handshake_initiator(alice, bob.bundle)
    s2, ek2, ct2 = complete_handshake_initiator(alice, bob.bundle)
    assert ek1 != ek2, "ephemeral X25519 must be fresh each handshake"
    assert ct1 != ct2, "ML-KEM ciphertext must be fresh each handshake"
    # Both still verify
    assert s1 == complete_handshake_responder(
        bob, "did:key:zRepeatA", alice.x25519_identity_pub, ek1, ct1
    )
    assert s2 == complete_handshake_responder(
        bob, "did:key:zRepeatA", alice.x25519_identity_pub, ek2, ct2
    )
