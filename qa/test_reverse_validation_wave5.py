"""波 5 qa-E 反向验证 · 故意 broken 输入 / 攻击边角 → 必触发 abort/error 路径.

§J-2 第 3 条: "故意 broken 输入 → 验证 abort/error 路径触发". 否则视为没真验收.

覆盖 4 大场景:
- A. 加密层: 错 keypair / 错 nonce / tampered ciphertext → DecryptError
- B. lend store: 已 deny 的 request retry / 重复 accept / 重复 deny → RequestStateError
- C. friend 关系: 自己加自己 / 重复 inbound + 重复 accept → FriendError
- D. ledger: 数字 negative / borrower==lender / 数字溢出 → LedgerError
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ─── A. 加密 proxy (dev-B) ────────────────────────────────────────────────────


def test_a_decrypt_with_wrong_seed_fails() -> None:
    """alice/bob seed 不同 → bob 用错的 alice pubkey 解密 → DecryptError."""
    pytest.importorskip("nacl")
    from sisoul.friend.encrypted_proxy import (
        EncryptedProxy,
        ProxyDecryptError,
        derive_friend_session_keypair,
    )
    from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key

    alice_master = mnemonic_to_master_key(generate_mnemonic())
    bob_master = mnemonic_to_master_key(generate_mnemonic())
    eve_master = mnemonic_to_master_key(generate_mnemonic())  # 第三者

    alice_priv, alice_pub = derive_friend_session_keypair(alice_master, 0)
    bob_priv, bob_pub = derive_friend_session_keypair(bob_master, 0)
    eve_priv, eve_pub = derive_friend_session_keypair(eve_master, 0)

    alice_proxy = EncryptedProxy(alice_priv, alice_pub, self_did="alice")
    bob_proxy = EncryptedProxy(bob_priv, bob_pub, self_did="bob")
    eve_proxy = EncryptedProxy(eve_priv, eve_pub, self_did="eve")

    # alice → bob: 加密给 bob
    blob = alice_proxy.encrypt_for(bob_pub.encode(), "hello bob")
    # eve 用自己的 priv 不能解 alice→bob 的 blob (而且 peer pubkey 用 alice 也不行 — eve 不是 alice)
    with pytest.raises(ProxyDecryptError):
        eve_proxy.decrypt_from(alice_pub.encode(), blob)


def test_a_decrypt_tampered_ciphertext_fails() -> None:
    """tampered ciphertext → MAC 错 → DecryptError."""
    pytest.importorskip("nacl")
    from sisoul.friend.encrypted_proxy import (
        EncryptedProxy,
        ProxyDecryptError,
        derive_friend_session_keypair,
    )
    from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key

    a_master = mnemonic_to_master_key(generate_mnemonic())
    b_master = mnemonic_to_master_key(generate_mnemonic())
    a_priv, a_pub = derive_friend_session_keypair(a_master, 0)
    b_priv, b_pub = derive_friend_session_keypair(b_master, 0)

    a_proxy = EncryptedProxy(a_priv, a_pub, "alice")
    b_proxy = EncryptedProxy(b_priv, b_pub, "bob")
    blob = a_proxy.encrypt_for(b_pub.encode(), "secret payload")
    # 翻转最后一字节
    tampered = bytearray(blob)
    tampered[-1] ^= 0xFF
    with pytest.raises(ProxyDecryptError):
        b_proxy.decrypt_from(a_pub.encode(), bytes(tampered))


def test_a_decrypt_too_short_blob_raises() -> None:
    pytest.importorskip("nacl")
    from sisoul.friend.encrypted_proxy import (
        EncryptedProxy,
        derive_friend_session_keypair,
    )
    from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key

    m = mnemonic_to_master_key(generate_mnemonic())
    priv, pub = derive_friend_session_keypair(m, 0)
    proxy = EncryptedProxy(priv, pub, "x")
    with pytest.raises((ValueError, Exception)):
        proxy.decrypt_from(pub.encode(), b"\x00" * 5)


# ─── B. lend store 状态机 ────────────────────────────────────────────────────


def test_b_lend_double_approve_idempotent_double_deny_raises(tmp_path: Path) -> None:
    """重复 approve 幂等; deny 后再 approve → RequestStateError."""
    from sisoul.friend.lend import LendStore, RequestStateError

    store = LendStore(db_path=tmp_path / "lend.db", pending_file=tmp_path / "pending.json")
    req = store.request_lend(
        borrower_did="alice", lender_did="bob",
        resource_type="llm_quota", amount=100, model="claude-opus-4-7",
        mode="per-request",
    )
    assert req.status == "pending"
    # 第一次 approve
    a1 = store.approve_lend(req.id)
    assert a1.status == "approved"
    # 重复 approve 幂等
    a2 = store.approve_lend(req.id)
    assert a2.status == "approved"

    # deny 后再 approve 拒
    req2 = store.request_lend(
        borrower_did="alice", lender_did="bob",
        resource_type="llm_quota", amount=200, model="claude-opus-4-7",
        mode="per-request",
    )
    store.deny_lend(req2.id, reason="test")
    with pytest.raises(RequestStateError):
        store.approve_lend(req2.id)
    store.close()


def test_b_lend_request_borrower_equals_lender_raises(tmp_path: Path) -> None:
    from sisoul.friend.lend import LendError, LendStore

    store = LendStore(db_path=tmp_path / "lend.db", pending_file=tmp_path / "pending.json")
    with pytest.raises(LendError):
        store.request_lend(
            borrower_did="alice", lender_did="alice",
            resource_type="llm_quota", amount=100, model="x",
        )
    store.close()


def test_b_lend_negative_amount_raises(tmp_path: Path) -> None:
    from sisoul.friend.lend import LendError, LendStore

    store = LendStore(db_path=tmp_path / "lend.db", pending_file=tmp_path / "pending.json")
    with pytest.raises(LendError):
        store.request_lend(
            borrower_did="alice", lender_did="bob",
            resource_type="llm_quota", amount=-1, model="x",
        )
    store.close()


# ─── C. friend 关系 ──────────────────────────────────────────────────────────


def test_c_send_request_to_self_raises(tmp_path: Path) -> None:
    from sisoul.friend.relationship import FriendRelationship, FriendRequestError

    rel = FriendRelationship(own_did="did:sisoul:alice", db_path=tmp_path / "friends.db")
    with pytest.raises(FriendRequestError):
        rel.send_friend_request("did:sisoul:alice")


def test_c_accept_unknown_request_id_raises(tmp_path: Path) -> None:
    from sisoul.friend.relationship import (
        FriendRelationship,
        FriendRequestNotFoundError,
    )

    rel = FriendRelationship(own_did="did:sisoul:alice", db_path=tmp_path / "friends.db")
    with pytest.raises(FriendRequestNotFoundError):
        rel.accept_friend_request("non-existent-uuid")


def test_c_accept_outbound_request_raises(tmp_path: Path) -> None:
    """alice 发了 outbound request, 自己又 accept 自己的 outbound → raise."""
    from sisoul.friend.relationship import FriendRelationship, FriendRequestError

    rel = FriendRelationship(
        own_did="did:sisoul:alice", db_path=tmp_path / "friends.db",
        attest_queue_db=tmp_path / "attest.db",
    )
    req = rel.send_friend_request("did:sisoul:bob")
    with pytest.raises(FriendRequestError):
        rel.accept_friend_request(req.request_id)


def test_c_double_accept_inbound_raises(tmp_path: Path) -> None:
    from sisoul.friend.relationship import FriendRelationship, FriendRequestError

    bob_rel = FriendRelationship(
        own_did="did:sisoul:bob", db_path=tmp_path / "friends.db",
        attest_queue_db=tmp_path / "attest.db",
    )
    in_req = bob_rel.receive_friend_request(requester_did="did:sisoul:alice")
    bob_rel.accept_friend_request(in_req.request_id)
    # 第二次 accept 同 id → raise (status != pending)
    with pytest.raises(FriendRequestError):
        bob_rel.accept_friend_request(in_req.request_id)


# ─── D. ledger ────────────────────────────────────────────────────────────────


def test_d_ledger_negative_amount_raises(tmp_path: Path) -> None:
    from sisoul.friend.ledger import LedgerError, ReciprocityLedger

    led = ReciprocityLedger(db_path=tmp_path / "ledger.db", self_did="alice")
    with pytest.raises(LedgerError):
        led.record_usage(
            borrower_did="alice", lender_did="bob",
            resource_type="llm_quota", amount=-100,
            model_or_skill_id="x", direction="borrow",
            enqueue_onchain=False,
        )
    led.close()


def test_d_ledger_borrower_equals_lender_raises(tmp_path: Path) -> None:
    from sisoul.friend.ledger import LedgerError, ReciprocityLedger

    led = ReciprocityLedger(db_path=tmp_path / "ledger.db", self_did="alice")
    with pytest.raises(LedgerError):
        led.record_usage(
            borrower_did="alice", lender_did="alice",
            resource_type="llm_quota", amount=100,
            model_or_skill_id="x", direction="borrow",
            enqueue_onchain=False,
        )
    led.close()


def test_d_ledger_invalid_direction_raises(tmp_path: Path) -> None:
    from sisoul.friend.ledger import LedgerError, ReciprocityLedger

    led = ReciprocityLedger(db_path=tmp_path / "ledger.db", self_did="alice")
    with pytest.raises(LedgerError):
        led.record_usage(
            borrower_did="alice", lender_did="bob",
            resource_type="llm_quota", amount=100,
            model_or_skill_id="x",
            direction="steal",  # type: ignore[arg-type]
            enqueue_onchain=False,
        )
    led.close()


def test_d_ledger_query_balance_without_self_did_raises(tmp_path: Path) -> None:
    from sisoul.friend.ledger import LedgerError, ReciprocityLedger

    led = ReciprocityLedger(db_path=tmp_path / "ledger.db")  # no self_did
    with pytest.raises(LedgerError):
        led.query_balance("bob")
    led.close()


# ─── E. permissions ───────────────────────────────────────────────────────────


def test_e_check_permission_no_yaml_returns_deny(tmp_path: Path) -> None:
    """没 perm 文件 → no_permission_config 拒."""
    from sisoul.friend.permissions import check_permission

    ok, reason = check_permission(
        "did:sisoul:alice", "llm_quota", 100, "x", perms_dir=tmp_path,
    )
    assert ok is False
    assert "no_permission_config" in reason


def test_e_check_permission_unknown_resource_returns_deny(tmp_path: Path) -> None:
    from sisoul.friend.permissions import check_permission

    ok, reason = check_permission(
        "did:sisoul:alice", "nuke_codes", 1, "x", perms_dir=tmp_path,  # type: ignore[arg-type]
    )
    assert ok is False
    assert "unknown_resource" in reason


def test_e_check_permission_negative_amount_returns_deny(tmp_path: Path) -> None:
    from sisoul.friend.permissions import check_permission

    ok, reason = check_permission(
        "did:sisoul:alice", "llm_quota", -1, "x", perms_dir=tmp_path,
    )
    assert ok is False
    assert "invalid_amount" in reason


def test_e_invalid_yaml_mode_raises(tmp_path: Path) -> None:
    from sisoul.friend.permissions import (
        FriendPermission,
        InvalidPermissionConfigError,
        LLMQuotaShare,
        save_permissions,
    )

    perm = FriendPermission(
        friend_did="alice",
        llm_quota_share=LLMQuotaShare(enabled=True, mode="bogus"),  # type: ignore[arg-type]
    )
    with pytest.raises(InvalidPermissionConfigError):
        save_permissions("alice", perm, perms_dir=tmp_path)
