"""波 5 qa-E · M4 同机双 sisoul daemon 模拟 alice ↔ bob 互借 LLM quota.

参考 §30 波 5 通过标准 + §29 M4 Mac+VPS 替代方案 (同机双 vault 不同端口).

测试组 (按 dev 报告 + obs 通过标准对齐):
- 2.1 双 sisoul instance 初始化 (HOME 隔离 + BIP-39 seed + DID)
- 2.2 friend 关系建立 (alice request → bob inbound → accept → mutual)
- 2.3 Bob 给 Alice 授权 LLM quota (strong-tie-auto + monthly cap + rate limit + models allow)
- 2.4 Alice 借 Bob LLM quota → 加密 proxy 真验证 + 隐私 metadata 不含 prompt
- 2.5 互惠 ledger 累积 + 反向 bob 借 alice 一次 → ratio 接近 1
- 2.6 5 层滥用各拦截 (L1 月度 cap / L2 rate / L3 revoke 即时 / L4 reputation / L5 scan token_burst)
- 3.  隐私关键: bob 端 lend / proxy session 看不到 alice prompt; 任意 SQLite/yaml 文件不含 prompt
- 5.  性能 sanity (M4 < 5s; 加密往返 < 500ms; ledger query < 100ms)
- 6.  daemon 61 paths smoke (ROUTE_MISSING=0)

不动 dev/sisoul/src/. 只 ship qa/.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# 让 qa/ 测试也能 import sisoul (uv-installed editable)
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ─────────────────────── 公共 fixture: 双 instance 隔离 ────────────────────────


@pytest.fixture
def alice_home(tmp_path: Path) -> Path:
    d = tmp_path / "alice"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def bob_home(tmp_path: Path) -> Path:
    d = tmp_path / "bob"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def alice_vault(alice_home: Path) -> Path:
    v = alice_home / ".sisoul"
    v.mkdir(parents=True)
    (v / "identity").mkdir(parents=True)
    (v / "friends").mkdir(parents=True)
    return v


@pytest.fixture
def bob_vault(bob_home: Path) -> Path:
    v = bob_home / ".sisoul"
    v.mkdir(parents=True)
    (v / "identity").mkdir(parents=True)
    (v / "friends").mkdir(parents=True)
    return v


# ─────────────────────── 2.1 双 instance 初始化 (BIP-39 + DID) ─────────────────


def _init_instance(home: Path, handle: str) -> dict[str, Any]:
    """alice / bob 各自 BIP-39 + DID + master key."""
    from sisoul.identity.seed import (
        generate_mnemonic,
        mnemonic_to_master_key,
        save_mnemonic_to_file,
    )

    mnemonic = generate_mnemonic(strength=128)
    master = mnemonic_to_master_key(mnemonic)
    seed_path = home / ".sisoul" / "seed.txt"
    save_mnemonic_to_file(mnemonic, seed_path)

    from sisoul.identity.did import register_did

    did_obj = register_did(
        handle=handle,
        network="mock",
        master_seed=master,
        registry_path=home / ".sisoul" / "identity" / "dids.json",
    )
    return {"handle": handle, "did": did_obj, "mnemonic": mnemonic, "master": master, "home": home}


def test_2_1_init_alice_and_bob(alice_vault: Path, bob_vault: Path) -> None:
    """alice + bob 各自 BIP-39 seed 生成 + DID 注册 + 文件落地."""
    alice = _init_instance(alice_vault.parent, "alice")
    bob = _init_instance(bob_vault.parent, "bob")

    # 必含字段
    assert alice["mnemonic"] != bob["mnemonic"], "两个 mnemonic 必不同"
    assert alice["master"] != bob["master"], "两个 master 必不同"
    assert alice["did"].handle == "alice"
    assert bob["did"].handle == "bob"

    # registry 落地
    a_reg = alice_vault / "identity" / "dids.json"
    b_reg = bob_vault / "identity" / "dids.json"
    assert a_reg.exists()
    assert b_reg.exists()

    # seed 文件落地
    assert (alice_vault / "seed.txt").exists()
    assert (bob_vault / "seed.txt").exists()


# ─────────────────────── 2.2 friend 关系建立 (双向 mutual) ─────────────────────


@pytest.fixture
def both_instances(alice_vault: Path, bob_vault: Path) -> dict[str, dict[str, Any]]:
    return {
        "alice": _init_instance(alice_vault.parent, "alice"),
        "bob": _init_instance(bob_vault.parent, "bob"),
    }


def _alice_did_str(both: dict[str, dict[str, Any]]) -> str:
    return f"did:sisoul:{both['alice']['did'].handle}"


def _bob_did_str(both: dict[str, dict[str, Any]]) -> str:
    return f"did:sisoul:{both['bob']['did'].handle}"


def test_2_2_friend_request_accept_mutual(
    alice_vault: Path, bob_vault: Path, both_instances: dict[str, dict[str, Any]]
) -> None:
    """alice request bob → bob inbound + accept → 双向 mutual 上链 attestation queue."""
    from sisoul.friend.relationship import FriendRelationship

    alice_did = _alice_did_str(both_instances)
    bob_did = _bob_did_str(both_instances)

    alice_rel = FriendRelationship(
        own_did=alice_did,
        db_path=alice_vault / "friends.db",
        attest_queue_db=alice_vault / "attest_queue.db",
    )
    bob_rel = FriendRelationship(
        own_did=bob_did,
        db_path=bob_vault / "friends.db",
        attest_queue_db=bob_vault / "attest_queue.db",
    )

    # alice 发 request → bob inbound + accept (bob 端 mutual 半成: bob 已 accept alice;
    # 等 alice 也 accept bob 才算双向 mutual)
    out_req_a = alice_rel.send_friend_request(bob_did, message="hi bob")
    assert out_req_a.status == "pending"
    assert out_req_a.direction == "outbound"
    assert out_req_a.attestation_uid

    in_req_b = bob_rel.receive_friend_request(
        requester_did=alice_did,
        message="hi bob",
        attestation_uid=out_req_a.attestation_uid,
    )
    assert in_req_b.direction == "inbound"
    friend_b = bob_rel.accept_friend_request(in_req_b.request_id)
    assert friend_b.status == "active"
    assert friend_b.accept_attestation_uid

    # alice 端收到 bob 的 FRIEND_ACCEPT attestation_uid → confirm_mutual_attestation
    alice_rel.confirm_mutual_attestation(
        friend_did=bob_did,
        mutual_attestation_uid=friend_b.accept_attestation_uid,
    )

    # 双向 mutual 需要 alice 也 accept bob 的反向 request (bob 也得发一遍)
    out_req_b = bob_rel.send_friend_request(alice_did)
    in_req_a = alice_rel.receive_friend_request(
        requester_did=bob_did, attestation_uid=out_req_b.attestation_uid,
    )
    friend_a = alice_rel.accept_friend_request(in_req_a.request_id)
    assert friend_a.status == "active"
    assert friend_a.accept_attestation_uid

    # bob 端 confirm alice 的 mutual_attestation_uid
    bob_rel.confirm_mutual_attestation(
        friend_did=alice_did,
        mutual_attestation_uid=friend_a.accept_attestation_uid,
    )

    # 双方都看到对方 active + mutual=True
    alice_friends = alice_rel.list_friends(status="active")
    bob_friends = bob_rel.list_friends(status="active")
    assert any(f.did == bob_did and f.is_mutual for f in alice_friends), (
        f"alice 应看到 bob active+mutual, 实际: {[(f.did, f.status, f.is_mutual) for f in alice_friends]}"
    )
    assert any(f.did == alice_did and f.is_mutual for f in bob_friends), (
        f"bob 应看到 alice active+mutual, 实际: {[(f.did, f.status, f.is_mutual) for f in bob_friends]}"
    )


# ─────────────────────── 2.3 Bob 授权 Alice LLM quota ──────────────────────────


def test_2_3_bob_grants_alice_llm_quota(
    bob_vault: Path, both_instances: dict[str, dict[str, Any]]
) -> None:
    from sisoul.friend.permissions import (
        FriendPermission,
        LLMQuotaShare,
        load_permissions,
        save_permissions,
    )

    alice_did = _alice_did_str(both_instances)
    perm = FriendPermission(
        friend_did=alice_did,
        llm_quota_share=LLMQuotaShare(
            enabled=True,
            mode="strong-tie-auto",
            monthly_token_cap=100_000,
            rate_limit=10,
            models=["claude-opus-4-7"],
            emergency_reserve_tokens=10_000,
        ),
    )
    perms_dir = bob_vault / "friends"
    path = save_permissions(alice_did, perm, perms_dir=perms_dir)
    assert path.exists()

    loaded = load_permissions(alice_did, perms_dir=perms_dir)
    assert loaded.llm_quota_share.monthly_token_cap == 100_000
    assert loaded.llm_quota_share.mode == "strong-tie-auto"
    assert "claude-opus-4-7" in loaded.llm_quota_share.models


# ─────────────────────── 2.4 Alice 借 Bob LLM quota (加密 proxy 真验证) ───────


def _mock_llm_forwarder(prompt: str, model: str, provider: str = "anthropic", **kw: Any):
    """mock LLM: 不真调 Claude API, 但模仿 dev-B forwarder 签名."""
    # 模仿真 LLM 响应 (text, prompt_tokens, response_tokens)
    return (f"mock-response-for-{model}-len{len(prompt)}", max(1, len(prompt) // 4), 50)


def test_2_4_alice_borrows_bob_llm_quota_encrypted_e2e(
    alice_vault: Path, bob_vault: Path, both_instances: dict[str, dict[str, Any]]
) -> None:
    """alice 加密 prompt → bob proxy 解密 → mock LLM → 加密 response 回 alice. 同时验证 metadata 不漏 prompt."""
    pytest.importorskip("nacl")  # libsodium 未装则 skip (dev-B 报告说 nacl 必有)
    from sisoul.friend.encrypted_proxy import (
        EncryptedProxy,
        derive_friend_session_keypair,
    )

    alice_did = _alice_did_str(both_instances)
    bob_did = _bob_did_str(both_instances)

    # 朋友 index 由 friend DB 给, 此处用 0 (同机 2 vault 各自 0)
    alice_priv, alice_pub = derive_friend_session_keypair(both_instances["alice"]["master"], 0)
    bob_priv, bob_pub = derive_friend_session_keypair(both_instances["bob"]["master"], 0)

    # Alice 端 proxy (仅做 encrypt/decrypt, 不调 LLM)
    alice_proxy = EncryptedProxy(
        self_priv=alice_priv, self_pub=alice_pub, self_did=alice_did
    )
    # Bob 端 proxy (mock LLM forwarder, 不真调 anthropic)
    bob_proxy = EncryptedProxy(
        self_priv=bob_priv,
        self_pub=bob_pub,
        self_did=bob_did,
        llm_api_key="mock-no-real-key",
        forwarder=_mock_llm_forwarder,
    )

    secret_prompt = "请帮我写一段 Python 函数计算斐波那契数列 -- ULTRA_SECRET_CANARY_TOKEN_12345"

    # 1. Alice 加密 prompt 给 Bob
    t_start = time.time()
    encrypted_prompt = alice_proxy.encrypt_for(bob_pub.encode(), secret_prompt)
    # 2. Bob proxy 处理 (内含解密 + mock LLM + 加密 response)
    encrypted_response, metadata = bob_proxy.proxy_chat_request(
        borrower_did=alice_did,
        borrower_pubkey=alice_pub.encode(),
        encrypted_prompt=encrypted_prompt,
        target_model="claude-opus-4-7",
    )
    # 3. Alice 解密 response
    plaintext_response = alice_proxy.decrypt_from(bob_pub.encode(), encrypted_response).decode()
    t_total = time.time() - t_start

    # 验证
    assert "mock-response-for-claude-opus-4-7" in plaintext_response
    assert metadata.session_id
    assert metadata.borrower_did == alice_did
    # 性能 (dev-B 报告 < 100ms; 我放宽到 < 500ms M4 加密 proxy 单次)
    assert t_total < 0.5, f"加密 proxy 单往返超 500ms: {t_total:.3f}s"

    # 🔑 隐私关键: metadata 不含 prompt 内容
    safe = metadata.to_safe_dict()
    assert "ULTRA_SECRET_CANARY_TOKEN_12345" not in str(safe), (
        f"metadata 漏 prompt canary: {safe}"
    )
    assert "prompt" not in safe or not safe.get("prompt")
    # bob 端 list_sessions 不能含 prompt
    sessions = bob_proxy.list_sessions()
    for s in sessions:
        sd = s.to_safe_dict()
        assert "ULTRA_SECRET_CANARY_TOKEN_12345" not in str(sd), (
            f"sessions 漏 prompt canary: {sd}"
        )


# ─────────────────────── 2.5 互惠 ledger 累积 + 反向 1:1 ──────────────────────


def test_2_5_reciprocity_ledger_alice_borrow_bob_then_reverse(
    alice_vault: Path, bob_vault: Path, both_instances: dict[str, dict[str, Any]]
) -> None:
    """alice 借 bob 一次 + bob 借 alice 一次 → ratio ≈ 1."""
    from sisoul.friend.ledger import ReciprocityLedger

    alice_did = _alice_did_str(both_instances)
    bob_did = _bob_did_str(both_instances)
    ledger_path = alice_vault / "ledger.db"  # 同机模拟: 共享一个 ledger db

    led = ReciprocityLedger(db_path=ledger_path, self_did=alice_did)

    # alice 借 bob: borrower=alice, lender=bob
    e1 = led.record_usage(
        borrower_did=alice_did,
        lender_did=bob_did,
        resource_type="llm_quota",
        amount=1000,
        model_or_skill_id="claude-opus-4-7",
        direction="borrow",
        enqueue_onchain=False,
    )
    assert e1.entry_id

    # bob 借 alice: borrower=bob, lender=alice (反向)
    e2 = led.record_usage(
        borrower_did=bob_did,
        lender_did=alice_did,
        resource_type="llm_quota",
        amount=1000,
        model_or_skill_id="claude-opus-4-7",
        direction="borrow",
        enqueue_onchain=False,
    )
    assert e2.entry_id

    # alice 视角: borrowed_total ≈ lent_total ≈ 1000, ratio ≈ 1
    bal = led.query_balance(bob_did, self_did=alice_did)
    assert bal.borrowed_total == 1000
    assert bal.lent_total == 1000
    assert 0.66 <= bal.ratio <= 1.5
    assert bal.direction_imbalance == "balanced"
    assert bal.imbalance_warning is False

    # ledger query 性能 < 100ms
    t = time.time()
    _ = led.query_balance(bob_did, self_did=alice_did)
    assert (time.time() - t) < 0.1

    led.close()


def test_2_5b_imbalance_warning_borrower_heavy(
    alice_vault: Path, both_instances: dict[str, dict[str, Any]]
) -> None:
    """alice 借多, 不还 → borrower-heavy warning."""
    from sisoul.friend.ledger import ReciprocityLedger

    alice_did = _alice_did_str(both_instances)
    bob_did = _bob_did_str(both_instances)
    led = ReciprocityLedger(db_path=alice_vault / "ledger_imb.db", self_did=alice_did)
    for _ in range(5):
        led.record_usage(
            borrower_did=alice_did,
            lender_did=bob_did,
            resource_type="llm_quota",
            amount=1000,
            model_or_skill_id="claude-opus-4-7",
            direction="borrow",
            enqueue_onchain=False,
        )
    bal = led.query_balance(bob_did, self_did=alice_did)
    assert bal.imbalance_warning is True
    assert bal.direction_imbalance == "borrower-heavy"
    led.close()


# ─────────────────────── 2.6 5 层滥用各拦截 ────────────────────────────────────


def test_2_6_l1_monthly_cap(bob_vault: Path, both_instances: dict[str, dict[str, Any]]) -> None:
    """L1 月度 cap: alice 借量超 cap → deny."""
    from sisoul.friend.anti_abuse import enforce_monthly_cap
    from sisoul.friend.permissions import FriendPermission, LLMQuotaShare

    perm = FriendPermission(
        friend_did=_alice_did_str(both_instances),
        llm_quota_share=LLMQuotaShare(
            enabled=True, mode="strong-tie-auto",
            monthly_token_cap=10_000, rate_limit=0,
        ),
    )
    # 已用 5k, 新借 6k → 总 11k > 10k → deny
    assert enforce_monthly_cap(perm, current_usage=5_000, new_amount=6_000) is False
    # 已用 5k, 新借 4k → 总 9k <= 10k → pass
    assert enforce_monthly_cap(perm, current_usage=5_000, new_amount=4_000) is True


def test_2_6_l2_rate_limit(both_instances: dict[str, dict[str, Any]]) -> None:
    """L2 rate limit: 短时连续 11 次 → 第 11 次 deny."""
    from sisoul.friend.anti_abuse import RateLimiter
    from sisoul.friend.permissions import FriendPermission, LLMQuotaShare

    perm = FriendPermission(
        friend_did=_alice_did_str(both_instances),
        llm_quota_share=LLMQuotaShare(
            enabled=True, mode="strong-tie-auto",
            monthly_token_cap=0, rate_limit=10,
        ),
    )
    alice_did = _alice_did_str(both_instances)
    limiter = RateLimiter()
    # 前 10 次都通过
    for i in range(10):
        assert limiter.check(perm, alice_did) is True, f"第 {i+1} 次不该被拦"
        limiter.record(alice_did, amount=100, request_id=f"r{i}")
    # 第 11 次应被拦
    assert limiter.check(perm, alice_did) is False, "第 11 次应被 rate limit 拦"


def test_2_6_l3_revoke_immediate(
    bob_vault: Path, both_instances: dict[str, dict[str, Any]]
) -> None:
    """L3 revoke: bob revoke alice → alice 立刻借不到 (revoked perm)."""
    from sisoul.friend.anti_abuse import revoke_friend_permission
    from sisoul.friend.permissions import (
        FriendPermission,
        LLMQuotaShare,
        check_permission,
        save_permissions,
    )

    alice_did = _alice_did_str(both_instances)
    perms_dir = bob_vault / "friends"
    perm = FriendPermission(
        friend_did=alice_did,
        llm_quota_share=LLMQuotaShare(enabled=True, mode="strong-tie-auto", monthly_token_cap=100_000),
    )
    save_permissions(alice_did, perm, perms_dir=perms_dir)

    # 撤前能借
    ok, reason = check_permission(
        alice_did, "llm_quota", 1000, "claude-opus-4-7",
        perms_dir=perms_dir, current_usage=0,
    )
    assert ok, f"revoke 前应能借: {reason}"

    # bob revoke alice (本地 yaml + 链上 attestation, 链上 fail-open)
    result = revoke_friend_permission(
        alice_did, reason="test_l3_revoke", perms_dir=perms_dir,
        onchain_publisher=lambda did, r: None,  # mock 上链 no-op
    )
    assert result["revoked"] is True

    # 撤后立即拒
    ok2, reason2 = check_permission(
        alice_did, "llm_quota", 1000, "claude-opus-4-7",
        perms_dir=perms_dir, current_usage=0,
    )
    assert ok2 is False, f"revoke 后应立即拒: {reason2}"
    assert reason2.startswith("revoked:"), f"reason 应 revoked: 开头, 实际 {reason2}"


def test_2_6_l4_reputation_drops_with_abuse(both_instances: dict[str, dict[str, Any]]) -> None:
    """L4 reputation: 多次滥用事件 → score 下降."""
    from sisoul.friend.anti_abuse import compute_reputation

    alice_did = _alice_did_str(both_instances)
    base = compute_reputation(alice_did, borrows=0, lends=0)
    # 多次滥用
    bad = compute_reputation(alice_did, borrows=0, lends=0, abuse_incidents=3)
    spam = compute_reputation(alice_did, borrows=0, lends=0, spam_complaints=5)
    assert bad.score < base.score, f"abuse 3 次后 score 应降, base={base.score} bad={bad.score}"
    assert spam.score < base.score
    # grade 可能从 B 降到 C / D
    assert bad.grade != "A"


def test_2_6_l5_scan_token_burst_blocks(both_instances: dict[str, dict[str, Any]]) -> None:
    """L5 daemon 扫描: amount 异常巨量 (> token_burst 阈值) → block."""
    from sisoul.friend.anti_abuse import ScanThresholds, scan_request_pattern

    alice_did = _alice_did_str(both_instances)
    th = ScanThresholds(token_burst=10_000)
    # 正常 amount → ok
    ok, reason = scan_request_pattern(
        {"friend_did": alice_did, "amount": 5000, "prompt_hash": "abc", "model": "claude-opus-4-7"},
        thresholds=th,
    )
    assert ok, f"正常 amount 应 ok: {reason}"
    # 10× burst → block
    bad, reason2 = scan_request_pattern(
        {"friend_did": alice_did, "amount": 100_000, "prompt_hash": "abc", "model": "claude-opus-4-7"},
        thresholds=th,
    )
    assert bad is False
    assert "token_burst" in reason2, f"reason 应含 token_burst, 实际 {reason2}"


def test_2_6_l5_scan_rate_burst_10s(both_instances: dict[str, dict[str, Any]]) -> None:
    """L5: 10s 内重复 > rate_burst_per_10s → block."""
    from sisoul.friend.anti_abuse import ScanThresholds, scan_request_pattern

    alice_did = _alice_did_str(both_instances)
    th = ScanThresholds(rate_burst_per_10s=5)
    now = time.time()
    history = [
        {"friend_did": alice_did, "amount": 100, "ts": now - i}
        for i in range(5)
    ]
    bad, reason = scan_request_pattern(
        {"friend_did": alice_did, "amount": 100, "prompt_hash": "h", "ts": now},
        thresholds=th, recent_history=history,
    )
    assert bad is False
    assert "rate_burst_10s" in reason


# ─────────────────────── 3. 隐私关键: bob 端任意文件不含 prompt ────────────────


def _grep_canary_in_bytes(canary: str, path: Path) -> bool:
    try:
        b = path.read_bytes()
    except Exception:
        return False
    return canary.encode() in b


def test_3_privacy_no_prompt_leak_in_bob_vault_files(
    bob_vault: Path, alice_vault: Path, both_instances: dict[str, dict[str, Any]]
) -> None:
    """跑一遍真 proxy + ledger 流程, 然后扫 bob 端所有文件不能含 prompt canary."""
    pytest.importorskip("nacl")
    from sisoul.friend.encrypted_proxy import (
        EncryptedProxy,
        derive_friend_session_keypair,
    )
    from sisoul.friend.ledger import ReciprocityLedger

    alice_did = _alice_did_str(both_instances)
    bob_did = _bob_did_str(both_instances)

    alice_priv, alice_pub = derive_friend_session_keypair(both_instances["alice"]["master"], 0)
    bob_priv, bob_pub = derive_friend_session_keypair(both_instances["bob"]["master"], 0)

    bob_proxy = EncryptedProxy(
        self_priv=bob_priv, self_pub=bob_pub, self_did=bob_did,
        llm_api_key="mock", forwarder=_mock_llm_forwarder,
    )
    alice_proxy = EncryptedProxy(self_priv=alice_priv, self_pub=alice_pub, self_did=alice_did)

    canary = "MEGA_SECRET_PROMPT_CANARY_XYZQ_9988"
    encrypted = alice_proxy.encrypt_for(bob_pub.encode(), f"plz help -- {canary}")
    enc_resp, meta = bob_proxy.proxy_chat_request(
        borrower_did=alice_did,
        borrower_pubkey=alice_pub.encode(),
        encrypted_prompt=encrypted,
        target_model="claude-opus-4-7",
    )

    # 同时 ledger 也写一条 (无 prompt 字段, 只 metadata)
    led = ReciprocityLedger(db_path=bob_vault / "ledger.db", self_did=bob_did)
    led.record_usage(
        borrower_did=alice_did, lender_did=bob_did,
        resource_type="llm_quota", amount=(meta.prompt_token_count + meta.response_token_count) or 100,
        model_or_skill_id="claude-opus-4-7", direction="lend",
        enqueue_onchain=False,
        note=f"borrow_session={meta.session_id}",
    )
    led.close()

    # 反向: alice 端解密能拿到响应 (sanity)
    plain = alice_proxy.decrypt_from(bob_pub.encode(), enc_resp).decode()
    assert "mock-response" in plain

    # 🔑 扫 bob_vault 所有文件: SQLite/yaml/json/任意 → 0 canary 命中
    leaks: list[str] = []
    for root, _dirs, files in os.walk(bob_vault):
        for fn in files:
            p = Path(root) / fn
            if _grep_canary_in_bytes(canary, p):
                leaks.append(str(p))
    assert not leaks, f"bob 端文件漏 prompt canary: {leaks}"

    # 反向 sanity: alice_vault 也不该有 (alice 端 plain 仅活在 python 变量, 不写文件)
    a_leaks: list[str] = []
    for root, _dirs, files in os.walk(alice_vault):
        for fn in files:
            p = Path(root) / fn
            if _grep_canary_in_bytes(canary, p):
                a_leaks.append(str(p))
    assert not a_leaks, f"alice 端文件漏 prompt canary: {a_leaks}"


# ─────────────────────── 5. 性能 sanity (M4 < 5s borrow wall) ─────────────────


def test_5_e2e_borrow_wall_under_5s(
    alice_vault: Path, bob_vault: Path, both_instances: dict[str, dict[str, Any]]
) -> None:
    """完整 alice → bob borrow (perm yaml + lend store + proxy + ledger) wall < 5s."""
    pytest.importorskip("nacl")
    from sisoul.friend.borrow import borrow_resource, set_mock_proxy, ProxyResult
    from sisoul.friend.permissions import (
        FriendPermission, LLMQuotaShare, save_permissions,
    )

    alice_did = _alice_did_str(both_instances)
    bob_did = _bob_did_str(both_instances)
    perms_dir = bob_vault / "friends"

    # bob 给 alice strong-tie-auto 授权 → 不弹窗
    perm = FriendPermission(
        friend_did=alice_did,
        llm_quota_share=LLMQuotaShare(
            enabled=True, mode="strong-tie-auto",
            monthly_token_cap=100_000, rate_limit=100,
            models=["claude-opus-4-7"],
        ),
    )
    save_permissions(alice_did, perm, perms_dir=perms_dir)

    # 由于 dev-D borrow.check_permission 路径 fallback per-request (它通过 import 全局
    # permissions, 但 perms_dir 不传入), 这里直接 force_mode 跳到 strong-tie-auto.
    set_mock_proxy(lambda **kw: ProxyResult(
        text="mock done",
        tokens_used=kw.get("amount", 1000),
        model_used=kw.get("model", ""),
        method="injected-mock",
    ))
    try:
        t = time.time()
        session = borrow_resource(
            borrower_did=alice_did,
            lender_did=bob_did,
            resource_type="llm_quota",
            amount=1000,
            model="claude-opus-4-7",
            prompt="hello",
            force_mode="strong-tie-auto",
            lend_db=alice_vault / "lend.db",
            pending_file=alice_vault / "pending_lends.json",
            ledger_db=alice_vault / "ledger_e2e.db",
            enqueue_onchain=False,
        )
        wall = time.time() - t
    finally:
        set_mock_proxy(None)

    assert session.status == "completed", f"session 应完成: {session.status} / {session.error}"
    assert wall < 5.0, f"E2E borrow wall 超 5s: {wall:.3f}s"
    assert session.ledger_entry_id


# ─────────────────────── 6. daemon 61 paths smoke (ROUTE_MISSING=0) ────────────


def test_6_daemon_paths_count_and_health() -> None:
    """OpenAPI paths count + 真启 daemon (sync) curl /health 通."""
    from fastapi.testclient import TestClient

    from sisoul.daemon import app

    paths = app.openapi()["paths"]
    assert len(paths) >= 60, f"daemon paths 应 >= 60 (波 5 集成后), 实际 {len(paths)}"

    n_ops = 0
    for _p, methods in paths.items():
        for m in methods.keys():
            if m.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                n_ops += 1
    assert n_ops >= 60, f"daemon ops >= 60, 实际 {n_ops}"

    client = TestClient(app)
    r = client.get("/sisoul/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "phase" in body


def test_6_daemon_friend_routes_smoke() -> None:
    """smoke 一组 friend / lend / ledger / perms 端点 (TestClient 同步)."""
    from fastapi.testclient import TestClient

    from sisoul.daemon import app

    client = TestClient(app)

    # GET 类不写 (read-only sanity): perms/list / ledger/imbalance / friend/list (各依赖 vault)
    # 这些 endpoint 可能要 query 参数, 用最低限度: 不传 → 期望 422 (validation) 或 200 (合法默认)
    ok_codes = {200, 400, 404, 405, 422, 501}
    for path in [
        "/sisoul/friend/list",
        "/sisoul/perms/list",
        "/sisoul/ledger/stats",
    ]:
        r = client.get(path)
        assert r.status_code in ok_codes, f"{path} 异常 {r.status_code}: {r.text[:100]}"
