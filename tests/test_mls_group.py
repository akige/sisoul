"""Tests for MLS group chat (RFC 9420 skeleton).

Covers: wire codec round-trips, 3-member create/send/receive, add+Welcome flow,
forward secrecy on join and on removal, epoch advance per commit, replay /
malformed / tamper rejection, 100-member scale, state serialization, and the
GossipSub topic integration.
"""

from __future__ import annotations

import time

import pytest

from sisoul.chat.mls import MLSGroup, MLSGroupError
from sisoul.chat.mls_protocol import (
    Add,
    ContentType,
    FramedContent,
    MLSMessage,
    MLSProtocolError,
    Reader,
    Remove,
    Welcome,
    WireFormat,
    Writer,
)
from sisoul.chat.mls_topic import MLSTopic, mls_topic_for, MLS_TOPIC_PREFIX
from sisoul.chat.transport import MemoryTransport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_group(group_id: str, members: list[str]) -> dict[str, MLSGroup]:
    """Create a group: members[0] is creator, the rest join via Welcome."""
    creator = MLSGroup(group_id, members, my_did=members[0])
    out = {members[0]: creator}
    for did in members[1:]:
        out[did] = MLSGroup.from_welcome(creator.create_welcome(did), did)
    return out


# ---------------------------------------------------------------------------
# 1. Wire codec (mls_protocol)
# ---------------------------------------------------------------------------

def test_varint_small_roundtrip():
    for v in (0, 1, 63):
        data = Writer().opaque(b"x" * v).bytes()
        assert Reader(data).opaque() == b"x" * v


def test_varint_medium_roundtrip():
    for v in (64, 300, 16383):
        data = Writer().opaque(b"y" * v).bytes()
        assert Reader(data).opaque() == b"y" * v


def test_varint_large_roundtrip():
    v = 70000  # needs the 4-byte varint branch (> 2^14)
    data = Writer().opaque(b"z" * v).bytes()
    assert Reader(data).opaque() == b"z" * v


def test_fixed_int_roundtrip():
    data = Writer().u8(200).u16(40000).u32(3_000_000_000).u64(1 << 40).bytes()
    r = Reader(data)
    assert r.u8() == 200
    assert r.u16() == 40000
    assert r.u32() == 3_000_000_000
    assert r.u64() == 1 << 40


def test_reader_truncated_raises():
    with pytest.raises(MLSProtocolError):
        Reader(b"\x05ab").opaque()  # claims 5 bytes, only 2 present


def test_mls_message_roundtrip():
    msg = MLSMessage(WireFormat.PRIVATE_MESSAGE, b"payload")
    decoded = MLSMessage.decode(msg.encode())
    assert decoded.wire_format == WireFormat.PRIVATE_MESSAGE
    assert decoded.body == b"payload"


def test_mls_message_bad_version_raises():
    bad = Writer().u16(0x9999).u16(1).opaque(b"x").bytes()
    with pytest.raises(MLSProtocolError):
        MLSMessage.decode(bad)


def test_framed_content_roundtrip():
    fc = FramedContent("gid", 7, "did:key:zA", ContentType.APPLICATION, b"ct", generation=4)
    decoded = FramedContent.decode(fc.encode())
    assert decoded.group_id == "gid"
    assert decoded.epoch == 7
    assert decoded.sender_did == "did:key:zA"
    assert decoded.content_type == ContentType.APPLICATION
    assert decoded.generation == 4
    assert decoded.content == b"ct"


def test_welcome_roundtrip():
    w = Welcome(1, "gid", 3, ["A", "B", "C"], b"sealed")
    decoded = Welcome.decode(w.encode())
    assert decoded.group_id == "gid"
    assert decoded.epoch == 3
    assert decoded.members == ["A", "B", "C"]
    assert decoded.encrypted_group_secrets == b"sealed"


def test_add_proposal_roundtrip():
    w = Writer()
    Add("did:key:zNew", b"\x01" * 32).encode(w)
    a = Add.decode(Reader(w.bytes()))
    assert a.member_did == "did:key:zNew"
    assert a.identity_key == b"\x01" * 32


def test_remove_proposal_roundtrip():
    w = Writer()
    Remove("did:key:zGone").encode(w)
    assert Remove.decode(Reader(w.bytes())).member_did == "did:key:zGone"


# ---------------------------------------------------------------------------
# 2. 3-member create + send/receive
# ---------------------------------------------------------------------------

def test_three_member_create():
    g = _make_group("g", ["A", "B", "C"])
    assert g["A"].epoch == g["B"].epoch == g["C"].epoch == 0
    assert g["A"].members == ["A", "B", "C"]


def test_three_member_a_to_all():
    g = _make_group("g", ["A", "B", "C"])
    ct = g["A"].encrypt(b"hello group")
    assert g["B"].decrypt(ct, "A") == b"hello group"
    assert g["C"].decrypt(ct, "A") == b"hello group"


def test_three_member_each_sends():
    g = _make_group("g", ["A", "B", "C"])
    for sender in ("A", "B", "C"):
        ct = g[sender].encrypt(f"from {sender}".encode())
        for receiver in ("A", "B", "C"):
            if receiver == sender:
                continue
            assert g[receiver].decrypt(ct, sender) == f"from {sender}".encode()


def test_multiple_messages_distinct_generations():
    g = _make_group("g", ["A", "B"])
    c1 = g["A"].encrypt(b"one")
    c2 = g["A"].encrypt(b"two")
    assert c1 != c2  # generation counter advances
    assert g["B"].decrypt(c1, "A") == b"one"
    assert g["B"].decrypt(c2, "A") == b"two"


# ---------------------------------------------------------------------------
# 3. Rejection paths (replay / malformed / tamper / wrong group)
# ---------------------------------------------------------------------------

def test_group_id_mismatch_rejected():
    g = _make_group("g", ["A", "B"])
    other = MLSGroup("other-group", ["A", "B"], my_did="B")
    ct = g["A"].encrypt(b"x")
    with pytest.raises(MLSGroupError, match="group_id"):
        other.decrypt(ct, "A")


def test_non_member_sender_rejected():
    g = _make_group("g", ["A", "B"])
    # craft a message whose header claims a non-member sender
    fc = FramedContent("g", 0, "Z", ContentType.APPLICATION, b"ct", generation=0)
    forged = MLSMessage(WireFormat.PRIVATE_MESSAGE, fc.encode()).encode()
    with pytest.raises(MLSGroupError):
        g["B"].decrypt(forged, "Z")


def test_sender_did_header_mismatch_rejected():
    g = _make_group("g", ["A", "B", "C"])
    ct = g["A"].encrypt(b"x")
    with pytest.raises(MLSGroupError, match="sender_did mismatch"):
        g["B"].decrypt(ct, "C")  # claim it came from C, header says A


def test_replay_rejected():
    g = _make_group("g", ["A", "B"])
    ct = g["A"].encrypt(b"once")
    assert g["B"].decrypt(ct, "A") == b"once"
    with pytest.raises(MLSGroupError, match="replay"):
        g["B"].decrypt(ct, "A")


def test_malformed_message_rejected():
    g = _make_group("g", ["A", "B"])
    with pytest.raises(MLSGroupError, match="malformed"):
        g["B"].decrypt(b"\x00\x01\x02garbage", "A")


def test_tampered_ciphertext_rejected():
    g = _make_group("g", ["A", "B"])
    ct = bytearray(g["A"].encrypt(b"secret"))
    ct[-1] ^= 0xFF  # flip a ciphertext bit → AEAD auth fail
    with pytest.raises(MLSGroupError):
        g["B"].decrypt(bytes(ct), "A")


def test_wrong_wire_format_to_decrypt_rejected():
    g = _make_group("g", ["A", "B"])
    welcome = g["A"].create_welcome("B")  # a WELCOME, not a PRIVATE_MESSAGE
    with pytest.raises(MLSGroupError):
        g["B"].decrypt(welcome, "A")


# ---------------------------------------------------------------------------
# 4. Add member + Welcome flow + forward secrecy on join
# ---------------------------------------------------------------------------

def test_add_member_advances_epoch():
    g = _make_group("g", ["A", "B", "C"])
    commit = g["A"].add_member("D")
    g["B"].apply_commit(commit)
    g["C"].apply_commit(commit)
    assert g["A"].epoch == 1
    assert g["B"].epoch == 1 and g["C"].epoch == 1


def test_added_member_decrypts_new_epoch_message():
    g = _make_group("g", ["A", "B", "C"])
    commit = g["A"].add_member("D")
    g["B"].apply_commit(commit)
    g["C"].apply_commit(commit)
    d = MLSGroup.from_welcome(g["A"].create_welcome("D"), "D")
    ct = g["A"].encrypt(b"welcome D")
    assert d.decrypt(ct, "A") == b"welcome D"
    assert g["B"].decrypt(ct, "A") == b"welcome D"


def test_new_member_cannot_read_pre_join_epoch():
    """Forward secrecy on join: D must not decrypt an epoch-0 message."""
    g = _make_group("g", ["A", "B", "C"])
    old_ct = g["A"].encrypt(b"before D joined")  # epoch 0
    commit = g["A"].add_member("D")
    g["B"].apply_commit(commit)
    g["C"].apply_commit(commit)
    d = MLSGroup.from_welcome(g["A"].create_welcome("D"), "D")
    assert d.epoch == 1
    with pytest.raises(MLSGroupError, match="epoch mismatch"):
        d.decrypt(old_ct, "A")


def test_add_duplicate_member_raises():
    g = _make_group("g", ["A", "B"])
    with pytest.raises(MLSGroupError, match="already"):
        g["A"].add_member("B")


def test_roster_updates_after_add():
    g = _make_group("g", ["A", "B"])
    commit = g["A"].add_member("C")
    g["B"].apply_commit(commit)
    assert g["A"].members == ["A", "B", "C"]
    assert g["B"].members == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# 5. Remove member + forward secrecy on removal
# ---------------------------------------------------------------------------

def test_remove_member_advances_epoch():
    g = _make_group("g", ["A", "B", "C"])
    commit = g["A"].remove_member("C")
    g["B"].apply_commit(commit)
    assert g["A"].epoch == 1 and g["B"].epoch == 1
    assert g["A"].members == ["A", "B"]


def test_removed_member_becomes_inactive():
    g = _make_group("g", ["A", "B", "C"])
    commit = g["A"].remove_member("C")
    g["C"].apply_commit(commit)  # C processes its own eviction
    assert not g["C"]._active
    with pytest.raises(MLSGroupError, match="inactive"):
        g["C"].encrypt(b"should fail")


def test_post_removal_forward_secrecy():
    """Removed member cannot decrypt messages in the post-removal epoch."""
    g = _make_group("g", ["A", "B", "C"])
    commit = g["A"].remove_member("C")
    g["B"].apply_commit(commit)
    g["C"].apply_commit(commit)
    ct = g["A"].encrypt(b"after C removed")
    assert g["B"].decrypt(ct, "A") == b"after C removed"
    with pytest.raises(MLSGroupError):
        g["C"].decrypt(ct, "A")


def test_remaining_members_communicate_after_removal():
    g = _make_group("g", ["A", "B", "C", "D"])
    commit = g["A"].remove_member("C")
    for did in ("B", "D"):
        g[did].apply_commit(commit)
    ct = g["B"].encrypt(b"still here")
    assert g["A"].decrypt(ct, "B") == b"still here"
    assert g["D"].decrypt(ct, "B") == b"still here"


def test_remove_nonmember_raises():
    g = _make_group("g", ["A", "B"])
    with pytest.raises(MLSGroupError, match="not a member"):
        g["A"].remove_member("Z")


def test_remove_self_raises():
    g = _make_group("g", ["A", "B"])
    with pytest.raises(MLSGroupError, match="cannot remove self"):
        g["A"].remove_member("A")


# ---------------------------------------------------------------------------
# 6. Epoch advance per commit + ratchet_epoch
# ---------------------------------------------------------------------------

def test_epoch_advances_every_commit():
    g = _make_group("g", ["A", "B"])
    assert g["A"].ratchet_epoch() == 0
    g["B"].apply_commit(g["A"].add_member("C"))
    assert g["A"].ratchet_epoch() == 1
    g["B"].apply_commit(g["A"].add_member("D"))
    assert g["A"].ratchet_epoch() == 2
    g["B"].apply_commit(g["A"].remove_member("C"))
    assert g["A"].ratchet_epoch() == 3
    assert g["B"].ratchet_epoch() == 3


def test_cross_epoch_message_undecryptable():
    g = _make_group("g", ["A", "B"])
    ct_e0 = g["A"].encrypt(b"epoch0")
    g["B"].apply_commit(g["A"].add_member("C"))
    # B is now at epoch 1; the epoch-0 ciphertext no longer decrypts.
    with pytest.raises(MLSGroupError, match="epoch mismatch"):
        g["B"].decrypt(ct_e0, "A")


# ---------------------------------------------------------------------------
# 7. Scale (100 members)
# ---------------------------------------------------------------------------

def test_hundred_member_scale():
    members = [f"did:key:m{i:03d}" for i in range(100)]
    start = time.perf_counter()
    creator = MLSGroup("big-group", members, my_did=members[0])
    # join a sample of members via Welcome
    sample = members[1:11]
    joined = {d: MLSGroup.from_welcome(creator.create_welcome(d), d) for d in sample}
    ct = creator.encrypt(b"broadcast to 100")
    for d in sample:
        assert joined[d].decrypt(ct, members[0]) == b"broadcast to 100"
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert len(creator.members) == 100
    # pure-Python skeleton must stay in the ms range on a 2-vCPU box
    assert elapsed_ms < 5000, f"100-member flow too slow: {elapsed_ms:.0f}ms"


def test_hundred_member_add_and_remove():
    members = [f"m{i}" for i in range(100)]
    creator = MLSGroup("big", members, my_did="m0")
    creator.add_member("m100")
    assert creator.epoch == 1
    assert "m100" in creator.members and len(creator.members) == 101
    creator.remove_member("m50")
    assert creator.epoch == 2
    assert "m50" not in creator.members and len(creator.members) == 100


# ---------------------------------------------------------------------------
# 8. State serialization
# ---------------------------------------------------------------------------

def test_serialize_restore_equivalence():
    g = _make_group("g", ["A", "B"])
    blob = g["A"].serialize_state()
    restored = MLSGroup.from_state(blob)
    ct = restored.encrypt(b"after restore")
    assert g["B"].decrypt(ct, "A") == b"after restore"


def test_serialize_preserves_fields():
    g = _make_group("g", ["A", "B", "C"])
    g["A"].add_member("D")  # advance to epoch 1
    restored = MLSGroup.from_state(g["A"].serialize_state())
    assert restored.group_id == "g"
    assert restored.epoch == 1
    assert restored.members == ["A", "B", "C", "D"]
    assert restored.my_did == "A"


def test_serialize_preserves_replay_state():
    g = _make_group("g", ["A", "B"])
    ct = g["A"].encrypt(b"msg")
    g["B"].decrypt(ct, "A")
    restored = MLSGroup.from_state(g["B"].serialize_state())
    with pytest.raises(MLSGroupError, match="replay"):
        restored.decrypt(ct, "A")  # replay-seen survives serialization


def test_from_state_bad_blob_raises():
    with pytest.raises(MLSGroupError):
        MLSGroup.from_state(b"not json at all")


# ---------------------------------------------------------------------------
# 9. Construction guards
# ---------------------------------------------------------------------------

def test_empty_members_raises():
    with pytest.raises(MLSGroupError):
        MLSGroup("g", [])


def test_my_did_not_in_members_raises():
    with pytest.raises(MLSGroupError, match="not in members"):
        MLSGroup("g", ["A", "B"], my_did="Z")


def test_duplicate_members_deduped():
    g = MLSGroup("g", ["A", "B", "A", "C"], my_did="A")
    assert g.members == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# 10. GossipSub topic integration
# ---------------------------------------------------------------------------

def test_mls_topic_deterministic():
    t1 = mls_topic_for("group-xyz")
    t2 = mls_topic_for("group-xyz")
    assert t1 == t2
    assert t1.startswith(MLS_TOPIC_PREFIX)
    assert mls_topic_for("group-xyz") != mls_topic_for("group-abc")


def test_mls_topic_classify():
    g = _make_group("g", ["A", "B"])
    app = g["A"].encrypt(b"x")
    welcome = g["A"].create_welcome("B")
    assert MLSTopic.classify(app) == WireFormat.PRIVATE_MESSAGE
    assert MLSTopic.classify(welcome) == WireFormat.WELCOME


async def test_topic_publish_subscribe_roundtrip():
    transport = MemoryTransport()
    topic = MLSTopic("g", transport)
    await topic.publish(b"opaque-mls-bytes")
    gen = topic.messages()
    payload = await gen.__anext__()
    assert payload == b"opaque-mls-bytes"
    await gen.aclose()
    await transport.close()


async def test_topic_carries_real_ciphertext():
    transport = MemoryTransport()
    g = _make_group("g", ["A", "B"])
    topic_a = MLSTopic("g", transport)
    topic_b = MLSTopic("g", transport)
    ct = g["A"].encrypt(b"over the wire")
    await topic_a.publish(ct)
    gen = topic_b.messages()
    received = await gen.__anext__()
    assert g["B"].decrypt(received, "A") == b"over the wire"
    await gen.aclose()
    await transport.close()
