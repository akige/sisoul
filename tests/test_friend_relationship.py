"""Tests for sisoul.friend.relationship (Phase 4 W51-W53 · 波 5 dev-A).

覆盖:
- Friend / FriendRequest dataclass 序列化 / handle 派生
- FriendDB SQLite upsert / get / list / delete / stats
- 强连接评分 (各 case 边界)
- FriendRelationship: send_request / receive_request / accept / confirm-mutual / revoke / list
- enqueue_friend_attestation (写到 dev-B AttestQueue)
- record_interaction 边界
- manual_score_override 覆盖路径
- 同机 2 实例 (Alice / Bob) 双向 mutual 关键路径
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sisoul.friend.relationship import (
    DEFAULT_FRIEND_DB,
    FRIEND_RELATIONSHIP_SCHEMA,
    FRIEND_RELATIONSHIP_SCHEMA_UID,
    STRONG_TIE_MANUAL_OVERRIDE_MAX,
    STRONG_TIE_MAX,
    STRONG_TIE_THRESHOLD,
    Friend,
    FriendDB,
    FriendError,
    FriendNotFoundError,
    FriendRelationship,
    FriendRequest,
    FriendRequestError,
    FriendRequestNotFoundError,
    compute_strong_tie_score,
    encode_friend_attestation_data,
    enqueue_friend_attestation,
    record_interaction,
    verify_mutual_attestation,
)


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def friend_db(tmp_path: Path) -> Path:
    return tmp_path / "friends.db"


@pytest.fixture
def attest_db(tmp_path: Path) -> Path:
    return tmp_path / "attest_queue.db"


@pytest.fixture
def alice_rel(friend_db: Path, attest_db: Path) -> FriendRelationship:
    return FriendRelationship(
        own_did="did:sisoul:alice",
        db_path=friend_db,
        attest_queue_db=attest_db,
    )


@pytest.fixture
def bob_rel(tmp_path: Path) -> FriendRelationship:
    """Bob 用独立 SQLite (模拟另一台机)."""
    return FriendRelationship(
        own_did="did:sisoul:bob",
        db_path=tmp_path / "bob_friends.db",
        attest_queue_db=tmp_path / "bob_attest.db",
    )


# ── Friend dataclass ────────────────────────────────────────────────────────


class TestFriend:
    def test_handle_derived_from_did(self) -> None:
        f = Friend(did="did:sisoul:alice")
        assert f.handle == "alice"
        assert f.created_at  # auto-filled
        assert f.status == "pending"

    def test_handle_from_ens(self) -> None:
        f = Friend(did="bob.sisoul.eth")
        assert f.handle == "bob"

    def test_handle_from_raw_handle(self) -> None:
        f = Friend(did="charlie")
        assert f.handle == "charlie"

    def test_is_mutual_false_without_both_uids(self) -> None:
        f = Friend(did="did:sisoul:alice")
        assert f.is_mutual is False
        f.accept_attestation_uid = "0xaa"
        assert f.is_mutual is False
        f.mutual_attestation_uid = "0xbb"
        assert f.is_mutual is True

    def test_roundtrip(self) -> None:
        f = Friend(did="did:sisoul:alice", status="active", interaction_count=42)
        d = f.to_dict()
        f2 = Friend.from_dict(d)
        assert f2.did == f.did
        assert f2.interaction_count == 42
        assert f2.status == "active"

    def test_from_dict_ignores_unknown_keys(self) -> None:
        f = Friend.from_dict(
            {"did": "did:sisoul:eve", "garbage": "xxx", "status": "active"}
        )
        assert f.did == "did:sisoul:eve"
        assert f.status == "active"


class TestFriendRequest:
    def test_auto_request_id(self) -> None:
        r = FriendRequest(
            request_id="",
            requester_did="did:sisoul:alice",
            target_did="did:sisoul:bob",
            direction="outbound",
        )
        assert r.request_id
        assert r.created_at

    def test_roundtrip(self) -> None:
        r = FriendRequest(
            request_id="r1",
            requester_did="did:sisoul:alice",
            target_did="did:sisoul:bob",
            direction="inbound",
            message="hi",
        )
        r2 = FriendRequest.from_dict(r.to_dict())
        assert r2.message == "hi"
        assert r2.direction == "inbound"


# ── FriendDB ────────────────────────────────────────────────────────────────


class TestFriendDB:
    def test_upsert_get_list(self, friend_db: Path) -> None:
        with FriendDB(db_path=friend_db) as db:
            f = Friend(did="did:sisoul:bob", status="active")
            db.upsert_friend(f)
            got = db.get_friend("did:sisoul:bob")
            assert got is not None
            assert got.did == "did:sisoul:bob"
            assert got.status == "active"
            # list
            items = db.list_friends()
            assert len(items) == 1

    def test_get_normalizes_did(self, friend_db: Path) -> None:
        with FriendDB(db_path=friend_db) as db:
            db.upsert_friend(Friend(did="did:sisoul:bob"))
            assert db.get_friend("bob") is not None
            assert db.get_friend("bob.sisoul.eth") is not None
            assert db.get_friend("did:sisoul:bob") is not None

    def test_delete(self, friend_db: Path) -> None:
        with FriendDB(db_path=friend_db) as db:
            db.upsert_friend(Friend(did="did:sisoul:bob"))
            assert db.delete_friend("did:sisoul:bob") is True
            assert db.get_friend("did:sisoul:bob") is None
            assert db.delete_friend("did:sisoul:nonexistent") is False

    def test_list_filter_by_status(self, friend_db: Path) -> None:
        with FriendDB(db_path=friend_db) as db:
            db.upsert_friend(Friend(did="did:sisoul:a", status="pending"))
            db.upsert_friend(Friend(did="did:sisoul:b", status="active"))
            db.upsert_friend(Friend(did="did:sisoul:c", status="active"))
            db.upsert_friend(Friend(did="did:sisoul:d", status="revoked"))
            assert len(db.list_friends(status="active")) == 2
            assert len(db.list_friends(status="pending")) == 1
            assert len(db.list_friends(status="revoked")) == 1

    def test_stats(self, friend_db: Path) -> None:
        with FriendDB(db_path=friend_db) as db:
            db.upsert_friend(Friend(did="did:sisoul:a", status="active"))
            db.upsert_friend(Friend(did="did:sisoul:b", status="pending"))
            db.upsert_request(
                FriendRequest(
                    request_id="r1",
                    requester_did="did:sisoul:a",
                    target_did="did:sisoul:b",
                    direction="outbound",
                )
            )
            stats = db.stats()
            assert stats["friends_active"] == 1
            assert stats["friends_pending"] == 1
            assert stats["requests_pending"] == 1

    def test_request_upsert_list(self, friend_db: Path) -> None:
        with FriendDB(db_path=friend_db) as db:
            r1 = FriendRequest(
                request_id="r1",
                requester_did="did:sisoul:a",
                target_did="did:sisoul:b",
                direction="outbound",
            )
            r2 = FriendRequest(
                request_id="r2",
                requester_did="did:sisoul:c",
                target_did="did:sisoul:a",
                direction="inbound",
                status="pending",
            )
            db.upsert_request(r1)
            db.upsert_request(r2)
            assert len(db.list_requests(direction="outbound")) == 1
            assert len(db.list_requests(direction="inbound")) == 1
            assert len(db.list_requests()) == 2
            assert len(db.list_requests(status="pending")) == 2


# ── 强连接评分 ──────────────────────────────────────────────────────────────


class TestStrongTieScore:
    def test_non_mutual_score_zero(self) -> None:
        f = Friend(did="did:sisoul:alice", status="active")
        sc = compute_strong_tie_score(f)
        assert sc.total == 0.0
        assert sc.is_strong is False

    def test_mutual_base_score(self) -> None:
        f = Friend(
            did="did:sisoul:alice",
            status="active",
            accept_attestation_uid="0xaa",
            mutual_attestation_uid="0xbb",
            became_active_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        sc = compute_strong_tie_score(f)
        assert sc.base == 1.0
        assert sc.months_score == 0.0
        assert sc.interactions_score == 0.0
        assert sc.total == pytest.approx(1.0, abs=0.05)
        assert sc.is_strong is False

    def test_months_score_growth(self) -> None:
        """6 月后给 3 分 (0.5/月), 12 月后 cap 6 分."""
        active_6mo_ago = (
            datetime.now(timezone.utc) - timedelta(days=180)
        ).isoformat(timespec="seconds")
        f = Friend(
            did="did:sisoul:alice",
            status="active",
            accept_attestation_uid="0xaa",
            mutual_attestation_uid="0xbb",
            became_active_at=active_6mo_ago,
        )
        sc = compute_strong_tie_score(f)
        # 6 月 * 0.5 = 3 分
        assert 2.9 <= sc.months_score <= 3.1
        # 不到 5 分阈值
        assert sc.is_strong is False

    def test_months_score_capped(self) -> None:
        active_24mo_ago = (
            datetime.now(timezone.utc) - timedelta(days=720)
        ).isoformat(timespec="seconds")
        f = Friend(
            did="did:sisoul:alice",
            status="active",
            accept_attestation_uid="0xaa",
            mutual_attestation_uid="0xbb",
            became_active_at=active_24mo_ago,
        )
        sc = compute_strong_tie_score(f)
        assert sc.months_score == 6.0  # 上限
        # base 1 + months 6 = 7 → strong
        assert sc.total == pytest.approx(7.0, abs=0.05)
        assert sc.is_strong is True

    def test_interactions_score(self) -> None:
        f = Friend(
            did="did:sisoul:alice",
            status="active",
            accept_attestation_uid="0xaa",
            mutual_attestation_uid="0xbb",
            became_active_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            interaction_count=25,
        )
        sc = compute_strong_tie_score(f)
        # 25 // 10 = 2 → 2 * 0.5 = 1 分
        assert sc.interactions_score == 1.0

    def test_interactions_score_capped(self) -> None:
        f = Friend(
            did="did:sisoul:alice",
            status="active",
            accept_attestation_uid="0xaa",
            mutual_attestation_uid="0xbb",
            became_active_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            interaction_count=1000,
        )
        sc = compute_strong_tie_score(f)
        assert sc.interactions_score == 5.0  # 上限

    def test_strong_at_threshold(self) -> None:
        """base 1 + months 4 (8 月) + interactions 0 = 5 → strong (exactly threshold)."""
        active_8mo_ago = (
            datetime.now(timezone.utc) - timedelta(days=240)
        ).isoformat(timespec="seconds")
        f = Friend(
            did="did:sisoul:alice",
            status="active",
            accept_attestation_uid="0xaa",
            mutual_attestation_uid="0xbb",
            became_active_at=active_8mo_ago,
        )
        sc = compute_strong_tie_score(f)
        assert sc.total >= STRONG_TIE_THRESHOLD
        assert sc.is_strong is True

    def test_manual_override(self) -> None:
        f = Friend(
            did="did:sisoul:alice",
            status="active",
            accept_attestation_uid="0xaa",
            mutual_attestation_uid="0xbb",
            manual_score_override=10.0,
        )
        sc = compute_strong_tie_score(f)
        assert sc.total == 10.0
        assert sc.is_strong is True
        assert sc.manual_override == 10.0

    def test_manual_override_cap(self) -> None:
        f = Friend(
            did="did:sisoul:alice",
            status="active",
            accept_attestation_uid="0xaa",
            mutual_attestation_uid="0xbb",
            manual_score_override=999.0,  # 超 15
        )
        sc = compute_strong_tie_score(f)
        assert sc.total == STRONG_TIE_MANUAL_OVERRIDE_MAX

    def test_manual_override_negative_clamped(self) -> None:
        f = Friend(
            did="did:sisoul:alice",
            status="active",
            accept_attestation_uid="0xaa",
            mutual_attestation_uid="0xbb",
            manual_score_override=-3.0,
        )
        sc = compute_strong_tie_score(f)
        assert sc.total == 0.0
        assert sc.is_strong is False

    def test_score_total_below_max(self) -> None:
        """理论最大 (无 manual): base 1 + months 6 + interactions 5 = 12."""
        f = Friend(
            did="did:sisoul:alice",
            status="active",
            accept_attestation_uid="0xaa",
            mutual_attestation_uid="0xbb",
            became_active_at=(
                datetime.now(timezone.utc) - timedelta(days=720)
            ).isoformat(timespec="seconds"),
            interaction_count=10000,
        )
        sc = compute_strong_tie_score(f)
        assert sc.total == STRONG_TIE_MAX  # 12


# ── enqueue_friend_attestation ─────────────────────────────────────────────


class TestFriendAttestation:
    def test_schema_uid_stable(self) -> None:
        # FRIEND_RELATIONSHIP_SCHEMA_UID 必须确定性 (mock 同 schema 算同 UID)
        assert FRIEND_RELATIONSHIP_SCHEMA_UID.startswith("0x")
        assert len(FRIEND_RELATIONSHIP_SCHEMA_UID) == 66  # 0x + 64 hex

    def test_schema_string_contains_fields(self) -> None:
        for field in [
            "requester_did",
            "target_did",
            "relationship_type",
            "timestamp",
            "message",
        ]:
            assert field in FRIEND_RELATIONSHIP_SCHEMA

    def test_encode_deterministic(self) -> None:
        a = encode_friend_attestation_data(
            "did:sisoul:alice", "did:sisoul:bob", "request", 12345, "hi"
        )
        b = encode_friend_attestation_data(
            "did:sisoul:alice", "did:sisoul:bob", "request", 12345, "hi"
        )
        assert a == b
        # 不同 inputs → 不同 output
        c = encode_friend_attestation_data(
            "did:sisoul:alice", "did:sisoul:bob", "accept", 12345, "hi"
        )
        assert a != c

    def test_enqueue_writes_to_attest_queue(self, attest_db: Path) -> None:
        result = enqueue_friend_attestation(
            requester_did="did:sisoul:alice",
            target_did="did:sisoul:bob",
            relationship_type="request",
            message="加个好友",
            attest_queue_db=attest_db,
        )
        assert result["queue_id"]
        assert result["attestation_uid"].startswith("0x")
        assert result["schema_uid"] == FRIEND_RELATIONSHIP_SCHEMA_UID
        assert result["attestation_payload"]["requester_did"] == "did:sisoul:alice"
        assert result["attestation_payload"]["relationship_type"] == "request"
        assert result["attestation_payload"]["message"] == "加个好友"

        # 真去 dev-B queue 验证有这一条 (action_type=friend-request)
        from sisoul.onchain.eas import AttestQueue

        with AttestQueue(db_path=attest_db) as q:
            items = q.all_items(status="pending", limit=10)
            assert len(items) == 1
            assert items[0].action_type == "friend-request"
            assert items[0].target == "did:sisoul:bob"
            assert items[0].tool_name == "sisoul-friend"
            assert items[0].actor_did == "did:sisoul:alice"

    def test_enqueue_normalizes_did(self, attest_db: Path) -> None:
        result = enqueue_friend_attestation(
            requester_did="alice",  # 光 handle
            target_did="bob.sisoul.eth",  # ENS
            relationship_type="accept",
            attest_queue_db=attest_db,
        )
        assert (
            result["attestation_payload"]["requester_did"] == "did:sisoul:alice"
        )
        assert result["attestation_payload"]["target_did"] == "did:sisoul:bob"


# ── FriendRelationship 高层 API ─────────────────────────────────────────────


class TestSendFriendRequest:
    def test_send_creates_request_and_friend(
        self, alice_rel: FriendRelationship
    ) -> None:
        req = alice_rel.send_friend_request("did:sisoul:bob", message="hi")
        assert req.requester_did == "did:sisoul:alice"
        assert req.target_did == "did:sisoul:bob"
        assert req.direction == "outbound"
        assert req.attestation_uid
        assert req.status == "pending"

        # 本端 friend cache 有 bob (pending)
        bob = alice_rel.get_friend("did:sisoul:bob")
        assert bob.status == "pending"
        assert bob.request_attestation_uid == req.attestation_uid

    def test_cannot_send_to_self(self, alice_rel: FriendRelationship) -> None:
        with pytest.raises(FriendRequestError):
            alice_rel.send_friend_request("did:sisoul:alice")

    def test_send_normalizes_target_did(self, alice_rel: FriendRelationship) -> None:
        req = alice_rel.send_friend_request("bob")
        assert req.target_did == "did:sisoul:bob"


class TestAcceptFriendRequest:
    def test_accept_inbound_request(
        self, alice_rel: FriendRelationship
    ) -> None:
        # 模拟 Bob 给 Alice 发了 inbound
        inbound = alice_rel.receive_friend_request(
            "did:sisoul:bob",
            message="加",
            attestation_uid="0x_bob_request_uid",
        )
        assert inbound.direction == "inbound"
        friend = alice_rel.accept_friend_request(inbound.request_id)
        assert friend.status == "active"
        assert friend.accept_attestation_uid is not None
        assert friend.became_active_at is not None
        # 此时 mutual=False, 因为对方 (bob) 那边 FRIEND_ACCEPT 还没传过来 confirm
        assert friend.is_mutual is False
        # score 也是 0 (非 mutual)
        assert friend.strong_tie_score == 0.0

    def test_accept_unknown_request_id(
        self, alice_rel: FriendRelationship
    ) -> None:
        with pytest.raises(FriendRequestNotFoundError):
            alice_rel.accept_friend_request("nonexistent-uuid")

    def test_cannot_accept_outbound(self, alice_rel: FriendRelationship) -> None:
        req = alice_rel.send_friend_request("did:sisoul:bob")
        with pytest.raises(FriendRequestError):
            alice_rel.accept_friend_request(req.request_id)

    def test_cannot_double_accept(
        self, alice_rel: FriendRelationship
    ) -> None:
        inbound = alice_rel.receive_friend_request("did:sisoul:bob")
        alice_rel.accept_friend_request(inbound.request_id)
        with pytest.raises(FriendRequestError):
            alice_rel.accept_friend_request(inbound.request_id)


class TestConfirmMutual:
    def test_confirm_makes_mutual(self, alice_rel: FriendRelationship) -> None:
        inbound = alice_rel.receive_friend_request("did:sisoul:bob")
        friend = alice_rel.accept_friend_request(inbound.request_id)
        assert friend.is_mutual is False

        # 模拟 Bob 那边 accept 完 + Alice daemon 收到 Bob 的 FRIEND_ACCEPT
        friend = alice_rel.confirm_mutual_attestation(
            "did:sisoul:bob", "0x_bob_accept_uid"
        )
        assert friend.is_mutual is True
        assert friend.status == "active"
        # mutual 后基础 score = 1
        assert friend.strong_tie_score == pytest.approx(1.0, abs=0.05)

    def test_confirm_unknown_friend(self, alice_rel: FriendRelationship) -> None:
        with pytest.raises(FriendNotFoundError):
            alice_rel.confirm_mutual_attestation("did:sisoul:nobody", "0x_x")


class TestRevoke:
    def test_revoke_sets_revoked(self, alice_rel: FriendRelationship) -> None:
        alice_rel.send_friend_request("did:sisoul:bob")
        friend = alice_rel.revoke_friend("did:sisoul:bob")
        assert friend.status == "revoked"
        assert friend.revoke_attestation_uid is not None
        assert friend.strong_tie_score == 0.0

    def test_revoke_unknown(self, alice_rel: FriendRelationship) -> None:
        with pytest.raises(FriendNotFoundError):
            alice_rel.revoke_friend("did:sisoul:nobody")


class TestListFriends:
    def test_list_empty(self, alice_rel: FriendRelationship) -> None:
        assert alice_rel.list_friends() == []

    def test_list_filter_status(self, alice_rel: FriendRelationship) -> None:
        alice_rel.send_friend_request("did:sisoul:bob")
        inbound = alice_rel.receive_friend_request("did:sisoul:charlie")
        alice_rel.accept_friend_request(inbound.request_id)
        active = alice_rel.list_friends(status="active")
        pending = alice_rel.list_friends(status="pending")
        assert len(active) == 1
        assert active[0].did == "did:sisoul:charlie"
        assert len(pending) == 1
        assert pending[0].did == "did:sisoul:bob"

    def test_list_recompute_score(self, alice_rel: FriendRelationship) -> None:
        inbound = alice_rel.receive_friend_request("did:sisoul:bob")
        alice_rel.accept_friend_request(inbound.request_id)
        alice_rel.confirm_mutual_attestation("did:sisoul:bob", "0x_bob_uid")
        items = alice_rel.list_friends(recompute_score=True)
        assert items[0].strong_tie_score >= 1.0


class TestSetManualScore:
    def test_set_manual_score(self, alice_rel: FriendRelationship) -> None:
        alice_rel.receive_friend_request("did:sisoul:bob")
        # 没 accept 先 — 这里直接拿 friend 对象
        # 我们要先有 friend 才能 set_manual_score
        inbound = alice_rel.list_requests(direction="inbound")[0]
        alice_rel.accept_friend_request(inbound.request_id)
        f = alice_rel.set_manual_score("did:sisoul:bob", 8.5)
        assert f.manual_score_override == 8.5
        # 即使非 mutual 也用 manual
        assert f.strong_tie_score == 8.5

        # 取消
        f = alice_rel.set_manual_score("did:sisoul:bob", None)
        assert f.manual_score_override is None
        assert f.strong_tie_score == 0.0  # 非 mutual → 0

    def test_set_manual_score_unknown(
        self, alice_rel: FriendRelationship
    ) -> None:
        with pytest.raises(FriendNotFoundError):
            alice_rel.set_manual_score("did:sisoul:nobody", 5.0)


# ── record_interaction ─────────────────────────────────────────────────────


class TestRecordInteraction:
    def test_record_interaction_increments(
        self, alice_rel: FriendRelationship, friend_db: Path
    ) -> None:
        inbound = alice_rel.receive_friend_request("did:sisoul:bob")
        alice_rel.accept_friend_request(inbound.request_id)
        alice_rel.confirm_mutual_attestation("did:sisoul:bob", "0x_bob_uid")

        f = record_interaction("did:sisoul:bob", increment=5, db_path=friend_db)
        assert f.interaction_count == 5
        assert f.last_interaction is not None

        f = record_interaction(
            "did:sisoul:bob", increment=10, db_path=friend_db
        )
        assert f.interaction_count == 15
        # 15 // 10 = 1 → +0.5 分
        assert f.strong_tie_score >= 1.5

    def test_record_unknown_friend(self, friend_db: Path) -> None:
        with pytest.raises(FriendNotFoundError):
            record_interaction("did:sisoul:nobody", db_path=friend_db)

    def test_record_negative_rejected(
        self, alice_rel: FriendRelationship, friend_db: Path
    ) -> None:
        alice_rel.send_friend_request("did:sisoul:bob")
        with pytest.raises(FriendError):
            record_interaction("did:sisoul:bob", increment=-1, db_path=friend_db)


# ── verify_mutual_attestation ───────────────────────────────────────────────


class TestVerifyMutual:
    def test_verify_returns_local_cache(self) -> None:
        f = Friend(
            did="did:sisoul:bob",
            status="active",
            accept_attestation_uid="0xaa",
            mutual_attestation_uid="0xbb",
        )
        r = verify_mutual_attestation(f)
        assert r["is_mutual"] is True
        assert r["accept_attestation_uid"] == "0xaa"
        assert r["mutual_attestation_uid"] == "0xbb"
        assert r["method"] == "local-cache"


# ── 两边 Alice / Bob 互相 mutual (轻量 in-test 双实例) ─────────────────────


class TestAliceBobMutual:
    def test_simulated_mutual_handshake(
        self, alice_rel: FriendRelationship, bob_rel: FriendRelationship
    ) -> None:
        # 1. Alice → Bob FRIEND_REQUEST
        req = alice_rel.send_friend_request(
            "did:sisoul:bob", message="加个朋友"
        )

        # 2. Bob daemon 收到 inbound (本 wave 模拟: 手工调 receive)
        bob_inbound = bob_rel.receive_friend_request(
            "did:sisoul:alice",
            message="加个朋友",
            attestation_uid=req.attestation_uid,
        )

        # 3. Bob accept → 产生 Bob FRIEND_ACCEPT attestation
        bob_friend = bob_rel.accept_friend_request(bob_inbound.request_id)
        assert bob_friend.status == "active"
        bob_accept_uid = bob_friend.accept_attestation_uid
        assert bob_accept_uid

        # 4. Alice 收到 Bob FRIEND_ACCEPT, 也 accept (互相 accept 完成双向)
        #    模拟: Alice 这边对应一条 inbound 是 Bob 发的, 实际 Phase 5 P2P 接.
        #    本 wave: Alice 端不走 inbound 流程, 直接生成自己的 FRIEND_ACCEPT 上链 →
        #    再 confirm Bob 那边的 mutual_attestation_uid.
        alice_inbound = alice_rel.receive_friend_request(
            "did:sisoul:bob", attestation_uid=bob_accept_uid
        )
        alice_friend = alice_rel.accept_friend_request(alice_inbound.request_id)
        alice_accept_uid = alice_friend.accept_attestation_uid

        # 5. 双向 confirm mutual: 对方的 accept_uid 作为本端的 mutual_uid
        alice_friend = alice_rel.confirm_mutual_attestation(
            "did:sisoul:bob", bob_accept_uid
        )
        bob_friend = bob_rel.confirm_mutual_attestation(
            "did:sisoul:alice", alice_accept_uid
        )

        assert alice_friend.is_mutual is True
        assert bob_friend.is_mutual is True
        assert alice_friend.strong_tie_score >= 1.0
        assert bob_friend.strong_tie_score >= 1.0

        # 6. list 各自能看到对方 active
        alice_list = alice_rel.list_friends(status="active")
        bob_list = bob_rel.list_friends(status="active")
        assert len(alice_list) == 1 and alice_list[0].did == "did:sisoul:bob"
        assert len(bob_list) == 1 and bob_list[0].did == "did:sisoul:alice"


# ── resolve_own_did (DID registry 集成) ────────────────────────────────────


class TestResolveOwnDID:
    def test_resolve_uses_did_registry(self, tmp_path: Path) -> None:
        from sisoul.identity.did import register_did

        registry = tmp_path / "dids.json"
        register_did(
            "alice",
            network="mock",
            registry_path=registry,
        )
        from sisoul.friend.relationship import resolve_own_did

        did_str = resolve_own_did(registry_path=registry)
        assert did_str == "did:sisoul:alice"

    def test_resolve_empty_registry_fallback(self, tmp_path: Path) -> None:
        from sisoul.friend.relationship import resolve_own_did

        registry = tmp_path / "empty.json"
        did_str = resolve_own_did(registry_path=registry, fallback="alice")
        assert did_str == "did:sisoul:alice"

    def test_resolve_empty_registry_no_fallback(self, tmp_path: Path) -> None:
        from sisoul.friend.relationship import resolve_own_did

        registry = tmp_path / "empty.json"
        with pytest.raises(FriendError):
            resolve_own_did(registry_path=registry)
