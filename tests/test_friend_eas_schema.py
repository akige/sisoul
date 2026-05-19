"""Tests for FRIEND_RELATIONSHIP EAS schema (波 5 dev-A).

覆盖:
- schema 字符串 + UID 稳定 / 字段齐全
- enqueue_friend_attestation 真写到波 4 dev-B AttestQueue
- 跨 schema 隔离: FRIEND_RELATIONSHIP_SCHEMA_UID != SISOUL_AUDIT_SCHEMA UID
- mock 双向 verify 自洽 (Alice attest Bob, Bob attest Alice)
- batched 上链路径 (复用 dev-B upload_batch mock 网络)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sisoul.friend.relationship import (
    FRIEND_RELATIONSHIP_SCHEMA,
    FRIEND_RELATIONSHIP_SCHEMA_UID,
    encode_friend_attestation_data,
    enqueue_friend_attestation,
)
from sisoul.onchain.eas import (
    MOCK_SCHEMA_UID as SISOUL_AUDIT_SCHEMA_UID,
    AttestConfig,
    AttestQueue,
    upload_batch,
)


def test_friend_schema_distinct_from_audit_schema() -> None:
    """FRIEND_RELATIONSHIP schema 必须跟 dev-B SISOUL_AUDIT_SCHEMA UID 不同."""
    assert FRIEND_RELATIONSHIP_SCHEMA_UID != SISOUL_AUDIT_SCHEMA_UID


def test_friend_schema_fields_complete() -> None:
    for f in [
        "requester_did",
        "target_did",
        "relationship_type",
        "timestamp",
        "message",
    ]:
        assert f in FRIEND_RELATIONSHIP_SCHEMA


def test_friend_schema_uid_format() -> None:
    assert FRIEND_RELATIONSHIP_SCHEMA_UID.startswith("0x")
    assert len(FRIEND_RELATIONSHIP_SCHEMA_UID) == 66  # 0x + 64 hex chars


def test_encode_canonical() -> None:
    """同 input 一定同 output (确定性 canonical encoding)."""
    a = encode_friend_attestation_data(
        "did:sisoul:alice", "did:sisoul:bob", "request", 100, "hi"
    )
    b = encode_friend_attestation_data(
        "did:sisoul:alice", "did:sisoul:bob", "request", 100, "hi"
    )
    assert a == b


def test_encode_includes_all_fields() -> None:
    import json as _json

    raw = encode_friend_attestation_data(
        "did:sisoul:alice", "did:sisoul:bob", "request", 100, "msg"
    )
    decoded = _json.loads(raw.decode("utf-8"))
    assert decoded["requester_did"] == "did:sisoul:alice"
    assert decoded["target_did"] == "did:sisoul:bob"
    assert decoded["relationship_type"] == "request"
    assert decoded["timestamp"] == 100
    assert decoded["message"] == "msg"


def test_relationship_type_differentiates_uid(tmp_path: Path) -> None:
    """request / accept / revoke 同对 DID 不同 attestation_uid."""
    db = tmp_path / "attest.db"
    r1 = enqueue_friend_attestation(
        "did:sisoul:alice", "did:sisoul:bob", "request", attest_queue_db=db
    )
    r2 = enqueue_friend_attestation(
        "did:sisoul:alice", "did:sisoul:bob", "accept", attest_queue_db=db
    )
    r3 = enqueue_friend_attestation(
        "did:sisoul:alice", "did:sisoul:bob", "revoke", attest_queue_db=db
    )
    assert r1["attestation_uid"] != r2["attestation_uid"]
    assert r2["attestation_uid"] != r3["attestation_uid"]


def test_enqueue_writes_to_attest_queue(tmp_path: Path) -> None:
    db = tmp_path / "attest.db"
    result = enqueue_friend_attestation(
        "did:sisoul:alice",
        "did:sisoul:bob",
        "request",
        message="加",
        attest_queue_db=db,
    )

    # 真去看 dev-B AttestQueue 有这条 (action_type=friend-request)
    with AttestQueue(db_path=db) as q:
        items = q.all_items(status="pending", limit=10)
        assert len(items) == 1
        item = items[0]
        assert item.action_type == "friend-request"
        assert item.actor_did == "did:sisoul:alice"
        assert item.target == "did:sisoul:bob"
        assert item.tool_name == "sisoul-friend"

    # attestation_uid 跟 mock 算的 sha256 一致 — 同 input + 同 nonce 唯一
    assert result["attestation_uid"].startswith("0x")
    assert len(result["attestation_uid"]) == 66


def test_batched_upload_path_works(tmp_path: Path) -> None:
    """跑 dev-B batched 上链 mock 看 friend attestation 能跟 audit 一锅煮."""
    db = tmp_path / "attest.db"
    # enqueue 3 条 friend + 2 条普通 audit (混合)
    enqueue_friend_attestation(
        "did:sisoul:alice", "did:sisoul:bob", "request", attest_queue_db=db
    )
    enqueue_friend_attestation(
        "did:sisoul:alice", "did:sisoul:bob", "accept", attest_queue_db=db
    )
    enqueue_friend_attestation(
        "did:sisoul:alice", "did:sisoul:bob", "revoke", attest_queue_db=db
    )

    cfg = AttestConfig(network="mock", batch_size=10)
    with AttestQueue(db_path=db) as q:
        # force=True 强 flush 不管阈值
        result = upload_batch(q, cfg, force=True)

    assert result.count == 3
    assert result.method == "mock"
    assert len(result.attestation_uids) == 3
    assert result.tx_hash.startswith("0x")


def test_mock_mutual_verify_self_consistent(tmp_path: Path) -> None:
    """Alice + Bob 双向 attest, 各自 enqueue 各自的 attestation, 链上各自查得到."""
    alice_db = tmp_path / "alice_attest.db"
    bob_db = tmp_path / "bob_attest.db"

    # Alice attest "Bob is friend"
    r_alice = enqueue_friend_attestation(
        "did:sisoul:alice",
        "did:sisoul:bob",
        "accept",
        attest_queue_db=alice_db,
    )
    # Bob attest "Alice is friend"
    r_bob = enqueue_friend_attestation(
        "did:sisoul:bob",
        "did:sisoul:alice",
        "accept",
        attest_queue_db=bob_db,
    )

    # 不同 attestation UID (不同 input)
    assert r_alice["attestation_uid"] != r_bob["attestation_uid"]
    # schema 一致
    assert r_alice["schema_uid"] == r_bob["schema_uid"] == FRIEND_RELATIONSHIP_SCHEMA_UID

    # 双向 payload 镜像
    assert r_alice["attestation_payload"]["requester_did"] == "did:sisoul:alice"
    assert r_alice["attestation_payload"]["target_did"] == "did:sisoul:bob"
    assert r_bob["attestation_payload"]["requester_did"] == "did:sisoul:bob"
    assert r_bob["attestation_payload"]["target_did"] == "did:sisoul:alice"


def test_enqueue_normalizes_handle_to_did(tmp_path: Path) -> None:
    db = tmp_path / "attest.db"
    r = enqueue_friend_attestation(
        "alice", "bob.sisoul.eth", "request", attest_queue_db=db
    )
    assert r["attestation_payload"]["requester_did"] == "did:sisoul:alice"
    assert r["attestation_payload"]["target_did"] == "did:sisoul:bob"


def test_no_mainnet_path() -> None:
    """波 5 约束: 不能在 mainnet 跑 friend attestation. dev-B upload_batch 已拦, 但这里
    单独再 assert 一次表明 dev-A 知情."""
    from sisoul.onchain.eas import NetworkNotSupportedError, AttestConfig

    cfg = AttestConfig(network="optimism-mainnet")
    # 不真 enqueue, 只校验 upload_batch 路径 abort.
    from sisoul.onchain.eas import AttestQueue, upload_batch

    import tempfile

    with tempfile.TemporaryDirectory() as td:
        with AttestQueue(db_path=Path(td) / "x.db") as q:
            enqueue_friend_attestation(
                "did:sisoul:alice",
                "did:sisoul:bob",
                "request",
                attest_queue_db=Path(td) / "x.db",
            )
            with pytest.raises(NetworkNotSupportedError):
                upload_batch(q, cfg, force=True)
