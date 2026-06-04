"""Tests for sisoul.chat.double_ratchet (Signal Double Ratchet wrapper)."""

from __future__ import annotations

import os

import nacl.bindings as nb
import pytest

from sisoul.chat.double_ratchet import (
    ChatSession,
    CipherMessage,
    DoubleRatchetError,
)


def _bootstrap_pair(ad: bytes = b"sisoul-chat-ad"):
    """Create a synced Alice/Bob session pair sharing a 32B secret."""
    shared = os.urandom(32)
    bob_priv = os.urandom(32)
    bob_pub = nb.crypto_scalarmult_base(bob_priv)
    alice, init_msg = ChatSession.init_outbound(shared, bob_pub, ad, b"\x00init")
    bob, init_pt = ChatSession.init_inbound(shared, bob_priv, init_msg, ad)
    assert init_pt == b"\x00init"
    return alice, bob


# 1
def test_initial_handshake_succeeds():
    alice, bob = _bootstrap_pair()
    assert alice is not None and bob is not None


# 2
def test_alice_to_bob_10_messages():
    alice, bob = _bootstrap_pair()
    for i in range(10):
        ct = alice.encrypt(f"msg-{i}".encode())
        assert bob.decrypt(ct) == f"msg-{i}".encode()


# 3
def test_bob_to_alice_10_messages():
    alice, bob = _bootstrap_pair()
    # Need at least one A->B first so Bob has a ratchet from A; the library
    # handles bidirectional from init though, so try directly.
    for i in range(10):
        ct = bob.encrypt(f"reply-{i}".encode())
        assert alice.decrypt(ct) == f"reply-{i}".encode()


# 4
def test_interleaved_bidirectional():
    alice, bob = _bootstrap_pair()
    for i in range(5):
        a_ct = alice.encrypt(f"a{i}".encode())
        assert bob.decrypt(a_ct) == f"a{i}".encode()
        b_ct = bob.encrypt(f"b{i}".encode())
        assert alice.decrypt(b_ct) == f"b{i}".encode()


# 5
def test_dh_ratchet_advances_on_reply():
    """Every reply should rotate the DH ratchet (header.ratchet_pub differs)."""
    alice, bob = _bootstrap_pair()
    a_ct1 = alice.encrypt(b"a1")
    bob.decrypt(a_ct1)
    b_ct1 = bob.encrypt(b"b1")
    alice.decrypt(b_ct1)
    a_ct2 = alice.encrypt(b"a2")
    assert a_ct1.header_ratchet_pub != a_ct2.header_ratchet_pub, (
        "Alice's DH ratchet pub must change after receiving Bob's reply"
    )


# 6
def test_sending_chain_length_increments():
    alice, bob = _bootstrap_pair()
    initial = alice.sending_chain_length
    for i in range(5):
        alice.encrypt(f"x{i}".encode())
    assert alice.sending_chain_length == initial + 5


# 7
def test_decrypt_fails_on_ciphertext_tamper():
    alice, bob = _bootstrap_pair()
    ct = alice.encrypt(b"secret")
    # Flip a byte in the ciphertext
    tampered_bytes = bytearray(ct.ciphertext)
    tampered_bytes[-1] ^= 0xFF
    bad = CipherMessage(
        header_ratchet_pub=ct.header_ratchet_pub,
        previous_sending_chain_length=ct.previous_sending_chain_length,
        sending_chain_length=ct.sending_chain_length,
        ciphertext=bytes(tampered_bytes),
    )
    with pytest.raises(DoubleRatchetError):
        bob.decrypt(bad)


# 8
def test_decrypt_fails_on_header_tamper():
    alice, bob = _bootstrap_pair()
    ct = alice.encrypt(b"hello")
    bad = CipherMessage(
        header_ratchet_pub=ct.header_ratchet_pub,
        previous_sending_chain_length=ct.previous_sending_chain_length,
        sending_chain_length=ct.sending_chain_length + 99,  # wrong counter
        ciphertext=ct.ciphertext,
    )
    with pytest.raises(DoubleRatchetError):
        bob.decrypt(bad)


# 9
def test_forward_secrecy_replay_rejected():
    """A captured ciphertext, once decrypted, cannot be replayed: the
    underlying message key is consumed and a second decryption attempt fails.

    This is the Signal-style forward-secrecy property: each message key is
    used exactly once and then deleted, so even full session compromise *after*
    a message has been delivered cannot recover that message.
    """
    alice, bob = _bootstrap_pair()
    ct = alice.encrypt(b"one-shot")
    assert bob.decrypt(ct) == b"one-shot"
    # Second decryption of the same ciphertext must be rejected (message
    # key has been deleted from the chain).
    with pytest.raises(DoubleRatchetError):
        bob.decrypt(ct)


# 9b: extra forward-secrecy assertion (header ratchet pub changes after a
# DH rotation, so old chain keys are unrecoverable from current state alone)
def test_forward_secrecy_dh_rotation_changes_chain():
    alice, bob = _bootstrap_pair()
    early_ct = alice.encrypt(b"before-rotation")
    bob.decrypt(early_ct)
    # Force a DH rotation
    bob.decrypt(alice.encrypt(b"a"))
    reply = bob.encrypt(b"bob-reply")
    alice.decrypt(reply)
    after_ct = alice.encrypt(b"after-rotation")
    bob.decrypt(after_ct)
    # The two messages live in different DH chains
    assert early_ct.header_ratchet_pub != after_ct.header_ratchet_pub


# 10
def test_session_serialization_roundtrip():
    alice, bob = _bootstrap_pair()
    bob.decrypt(alice.encrypt(b"before"))
    blob = bob.serialize()
    restored = ChatSession.deserialize(blob)
    # Restored Bob can still decrypt new messages from Alice
    new_ct = alice.encrypt(b"after-restore")
    assert restored.decrypt(new_ct) == b"after-restore"


# 11
def test_cipher_message_json_roundtrip():
    alice, _ = _bootstrap_pair()
    ct = alice.encrypt(b"json-me")
    js = ct.to_json()
    restored = CipherMessage.from_json(js)
    assert restored.header_ratchet_pub == ct.header_ratchet_pub
    assert restored.sending_chain_length == ct.sending_chain_length
    assert restored.ciphertext == ct.ciphertext


# 12
def test_associated_data_mismatch_fails():
    alice, _bob = _bootstrap_pair(ad=b"correct-ad")
    # Bob with different AD
    shared = os.urandom(32)
    bob_priv = os.urandom(32)
    bob_pub = nb.crypto_scalarmult_base(bob_priv)
    _other_alice, init_msg = ChatSession.init_outbound(shared, bob_pub, b"ad1", b"hi")
    # Decrypt with wrong AD
    with pytest.raises(DoubleRatchetError):
        ChatSession.init_inbound(shared, bob_priv, init_msg, b"different-ad")
