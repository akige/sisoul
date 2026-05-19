"""波 6 qa-C · M4b dummy `python-helper` skill 完整 lifecycle (Phase 4 下半).

§30 §2 波 6 通过标准 + §28 §3.6 AI 技能 share. 复用波 5 qa-E 同机双 instance 模式
+ canary 0 leak 范式. 模拟:

    alice = owner (训 python-helper skill, lend)
    bob   = borrower (借 30s test 缩短, mock chat, 等过期 auto destroy, 验 0 leak)

测试组 (按任务 spec):
- 2.1 双 sisoul instance 起 (HOME 隔离 + BIP-39 seed + DID)
- 2.2 alice 训 dummy python-helper skill (含 CANARY)
- 2.3 bob friend list 看到 alice + 借 alice 的 python-helper (duration_seconds_override=3)
- 2.4 bob 用 skill 做 mock chat (走 skill_router proxy-chat) + wall < 5s
- 2.5 等过期 → auto_destroy_expired_sessions → sessions 空 + IPFS unpin
- 2.6 0 leak: bob_vault 全文件 grep CANARY → 0; tmp dir 不在; ledger 写 1 条 metadata
- 2.7 反向: alice 撤回 lend → bob 借不到

严格约束: 不动 src/. 只 ship qa/. 不在本机起 launchd.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

# 让 qa/ 测试也能 import sisoul (uv-installed editable)
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# 关键 CANARY: skill 训练 prompt 内嵌. destroy 后 bob 端任意文件 0 命中.
SKILL_CANARY = "MEGA_SKILL_CANARY_PROMPT_8877"


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
    (v / "identity").mkdir(parents=True)
    (v / "friends").mkdir(parents=True)
    (v / "skills" / "owned").mkdir(parents=True)
    return v


@pytest.fixture
def bob_vault(bob_home: Path) -> Path:
    v = bob_home / ".sisoul"
    (v / "identity").mkdir(parents=True)
    (v / "friends").mkdir(parents=True)
    (v / "skills" / "owned").mkdir(parents=True)
    return v


@pytest.fixture(autouse=True)
def _isolate_skill_state():
    """skill_borrow 内存 _ACTIVE_SESSIONS + skill_ipfs _MOCK_BLOB_CACHE 跨 test 清."""
    from sisoul.friend.skill_borrow import _ACTIVE_SESSIONS
    from sisoul.friend.skill_ipfs import clear_mock_blob_cache

    _ACTIVE_SESSIONS.clear()
    clear_mock_blob_cache()
    yield
    _ACTIVE_SESSIONS.clear()
    clear_mock_blob_cache()


def _init_instance(home: Path, handle: str) -> dict[str, Any]:
    """alice / bob 各自 BIP-39 + DID + master key (跟波 5 qa-E _init_instance 同模式)."""
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
    return {
        "handle": handle,
        "did": did_obj,
        "mnemonic": mnemonic,
        "master": master,
        "home": home,
    }


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


def _make_mutual_friends(both: dict[str, dict[str, Any]], alice_vault: Path, bob_vault: Path) -> None:
    """复用波 5 qa-E 模式: alice ↔ bob 双向 friend + mutual."""
    from sisoul.friend.relationship import FriendRelationship

    alice_did = _alice_did_str(both)
    bob_did = _bob_did_str(both)

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

    out_a = alice_rel.send_friend_request(bob_did, message="hi")
    in_b = bob_rel.receive_friend_request(
        requester_did=alice_did, message="hi",
        attestation_uid=out_a.attestation_uid,
    )
    fb = bob_rel.accept_friend_request(in_b.request_id)
    alice_rel.confirm_mutual_attestation(friend_did=bob_did, mutual_attestation_uid=fb.accept_attestation_uid)
    out_b = bob_rel.send_friend_request(alice_did)
    in_a = alice_rel.receive_friend_request(requester_did=bob_did, attestation_uid=out_b.attestation_uid)
    fa = alice_rel.accept_friend_request(in_a.request_id)
    bob_rel.confirm_mutual_attestation(friend_did=alice_did, mutual_attestation_uid=fa.accept_attestation_uid)


# ─────────────────────── 2.1 双 instance 初始化 ───────────────────────────────


def test_2_1_init_alice_bob_distinct_seeds_and_dids(
    alice_vault: Path, bob_vault: Path, both_instances: dict[str, dict[str, Any]]
) -> None:
    """alice + bob 各自 BIP-39 + DID 落地, mnemonic / master 各异."""
    alice = both_instances["alice"]
    bob = both_instances["bob"]
    assert alice["mnemonic"] != bob["mnemonic"], "alice/bob mnemonic 必不同"
    assert alice["master"] != bob["master"], "alice/bob master 必不同"
    assert (alice_vault / "seed.txt").exists()
    assert (bob_vault / "seed.txt").exists()
    assert (alice_vault / "identity" / "dids.json").exists()
    assert (bob_vault / "identity" / "dids.json").exists()


# ─────────────────────── 2.2 alice 训 dummy python-helper skill ────────────────


@pytest.fixture
def python_helper_pkg(both_instances: dict[str, dict[str, Any]]):
    """alice 训一个 dummy `python-helper` skill, 含 CANARY in few_shot."""
    from sisoul.friend.skill_package import package_skill

    alice_did = _alice_did_str(both_instances)
    pkg = package_skill(
        name="python-helper",
        owner_did=alice_did,
        system_prompt=(
            "You are a Python expert. Always suggest Pythonic solutions, "
            "use type hints, prefer pathlib over os.path."
        ),
        description="Pythonic helper · type-safe · prefers pathlib",
        version="0.1.0",
        examples=[
            {"q": "read file?", "a": "Path(p).read_text(encoding='utf-8')"},
            {"q": "list dir?", "a": "list(Path(p).iterdir())"},
            # 第 3 个含 CANARY (验 destroy 后 0 leak)
            {"q": f"signature trick? {SKILL_CANARY}", "a": "use typing.Protocol"},
        ],
        personality_traits=["pedantic", "type-safe", "pythonic"],
        recommended_models=["claude-opus-4-7", "gpt-5"],
    )
    return pkg


def test_2_2_alice_creates_python_helper_skill(
    alice_vault: Path, both_instances: dict[str, dict[str, Any]],
    python_helper_pkg, monkeypatch,
) -> None:
    """alice 用 package_skill 训 python-helper · CANARY 在 example 里 · 落 owned/."""
    from sisoul.friend.skill_package import validate_skill_package

    monkeypatch.setenv("HOME", str(alice_vault.parent))
    pkg = python_helper_pkg

    # alice 端 owned/<skill_id>.json 落地 (模拟 sisoul skill create)
    owned_path = alice_vault / "skills" / "owned" / "python-helper.json"
    owned_path.write_text(pkg.to_json(), encoding="utf-8")
    assert owned_path.exists()

    # validate schema
    validate_skill_package(pkg)
    assert pkg.skill_id == "python-helper"
    assert pkg.owner_did == _alice_did_str(both_instances)
    assert pkg.contents.few_shot_examples_count == 3
    assert "pedantic" in pkg.contents.personality_traits
    assert "claude-opus-4-7" in pkg.contents.recommended_models
    # CANARY 真在 owned plaintext (alice 自己 plaintext 是 OK 的, 只有 bob 端不能漏)
    blob = owned_path.read_bytes()
    assert SKILL_CANARY.encode() in blob, "alice 自己 owned plaintext 应含 CANARY (sanity)"


# ─────────────────────── 2.3 bob 借 alice 的 python-helper (30s 缩短) ──────────


def test_2_3_bob_borrows_alice_python_helper_30s_test(
    alice_vault: Path, bob_vault: Path,
    both_instances: dict[str, dict[str, Any]], python_helper_pkg,
) -> None:
    """bob 端: friend list 看到 alice · 真用 alice_pub × bob_priv 跨实例加密通道 · borrow OK."""
    from nacl.public import PrivateKey

    from sisoul.friend.skill_borrow import (
        get_active_skill_package,
        request_borrow_skill,
    )
    from sisoul.friend.skill_ipfs import (
        SkillPinDB,
        SkillPinRecord,
        register_mock_blob,
    )
    from sisoul.friend.skill_package import (
        decrypt_skill_package,
        encrypt_skill_package,
    )

    _make_mutual_friends(both_instances, alice_vault, bob_vault)

    # bob: friend list 应见 alice (sanity)
    from sisoul.friend.relationship import FriendRelationship
    bob_rel = FriendRelationship(
        own_did=_bob_did_str(both_instances),
        db_path=bob_vault / "friends.db",
        attest_queue_db=bob_vault / "attest_queue.db",
    )
    friends = bob_rel.list_friends(status="active")
    assert any(f.did == _alice_did_str(both_instances) for f in friends), (
        "bob friend list 应见 alice"
    )

    # 两套独立 keypair (alice owner / bob borrower)
    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    alice_pub = alice_priv.public_key
    bob_pub = bob_priv.public_key

    # bob 端独立 DB / tmp dir (HOME 隔离)
    bob_skill_db = bob_vault / "skill_borrow.db"
    bob_pin_db = bob_vault / "skill_pins.db"
    bob_ledger_db = bob_vault / "ledger.db"
    bob_tmp = bob_vault / "skill-tmp"

    pkg = python_helper_pkg

    # alice 端 provider: encrypt(skill, bob_pub) → IPFS pin (mock CID) → 返
    def alice_provider(_owner: str, _skill: str) -> tuple[bytes, str]:
        blob = encrypt_skill_package(pkg, bob_pub, alice_priv)
        cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
        register_mock_blob(cid, blob)
        # bob 端本地 pin DB 也记 (跨实例 IPFS 假设双方都能查 pin metadata)
        with SkillPinDB(db_path=bob_pin_db) as db:
            now = int(time.time())
            db.upsert(SkillPinRecord(
                cid=cid, owner_did=pkg.owner_did, skill_id=pkg.skill_id,
                pinned_at=now, expires_at=now + 24 * 3600,
                size_bytes=len(blob), pinata_pinned=False,
            ))
        return blob, cid

    # bob 端 decryptor: alice_pub × bob_priv
    def bob_decryptor(blob: bytes):
        return decrypt_skill_package(blob, alice_pub, bob_priv)

    # bob: 借 30 分钟 · override 3 秒 (test 缩短 lifecycle)
    t_start = time.time()
    res = request_borrow_skill(
        owner_did=pkg.owner_did,
        skill_id=pkg.skill_id,
        borrower_did=_bob_did_str(both_instances),
        duration_minutes=30,
        duration_seconds_override=3,
        encrypted_skill_provider=alice_provider,
        decrypt_callback=bob_decryptor,
        skip_permission_check=True,  # mutual friend OK 但 permission yaml 没配, 跳
        db_path=bob_skill_db,
        tmp_root=bob_tmp,
        ledger_db=bob_ledger_db,
        enqueue_onchain=False,
    )
    wall = time.time() - t_start

    # 验证: session 活, tmp 存在, fingerprint 对
    assert res.session.status == "active"
    assert res.session.owner_did == pkg.owner_did
    assert res.session.borrower_did == _bob_did_str(both_instances)
    assert res.skill_package_fingerprint == pkg.fingerprint
    tmp_dir = Path(res.session.local_decrypted_path)
    assert tmp_dir.exists()
    # decrypted tmp 含 system_prompt.md
    sp_file = (tmp_dir / "system_prompt.md").read_text(encoding="utf-8")
    assert "Python expert" in sp_file
    # CANARY 应在 examples.json (本 example 第 3 条含)
    examples_file = tmp_dir / "examples.json"
    assert examples_file.exists()
    assert SKILL_CANARY in examples_file.read_text(encoding="utf-8")

    # 性能: borrow wall < 5s (实际 ms 级)
    assert wall < 5.0, f"borrow wall 超 5s: {wall:.3f}s"

    # cache 拿到解密 SkillPackage
    cached = get_active_skill_package(res.session.session_id)
    assert cached is not None
    assert cached.skill_id == "python-helper"


# ─────────────────────── 2.4 bob 用 skill 做 mock chat (走 proxy_skill_chat) ───


def test_2_4_bob_uses_skill_mock_chat_wall_under_500ms(
    alice_vault: Path, bob_vault: Path,
    both_instances: dict[str, dict[str, Any]], python_helper_pkg,
) -> None:
    """bob borrow 后 · 用 dev-A proxy_skill_chat (mock forwarder) 跑一轮 · wall < 500ms."""
    from nacl.public import PrivateKey

    from sisoul.friend.skill_borrow import (
        proxy_skill_chat,
        request_borrow_skill,
    )
    from sisoul.friend.skill_ipfs import register_mock_blob
    from sisoul.friend.skill_package import decrypt_skill_package, encrypt_skill_package

    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    alice_pub = alice_priv.public_key
    bob_pub = bob_priv.public_key

    bob_skill_db = bob_vault / "skill_borrow.db"
    bob_tmp = bob_vault / "skill-tmp"
    pkg = python_helper_pkg

    def provider(_o, _s):
        blob = encrypt_skill_package(pkg, bob_pub, alice_priv)
        cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
        register_mock_blob(cid, blob)
        return blob, cid

    def decryptor(blob):
        return decrypt_skill_package(blob, alice_pub, bob_priv)

    res = request_borrow_skill(
        owner_did=pkg.owner_did, skill_id=pkg.skill_id,
        borrower_did=_bob_did_str(both_instances),
        duration_minutes=30,
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=bob_skill_db, tmp_root=bob_tmp,
    )

    # mock forwarder: 不真打 LLM, 模拟签名 (text, prompt_tok, resp_tok)
    def mock_fwd(prompt, model, provider, api_key=None, **kw):
        return f"[mock] result for {model}", 60, 40

    t = time.time()
    chat_result = proxy_skill_chat(
        session_id=res.session.session_id,
        prompt="how to read a file?",
        forwarder=mock_fwd,
        db_path=bob_skill_db,
    )
    wall = time.time() - t

    assert chat_result["tokens_used"] == 100
    assert chat_result["skill_id"] == "python-helper"
    assert chat_result["model_used"] == "claude-opus-4-7"  # pkg.recommended_models 第 1
    assert chat_result["session_remaining_sec"] > 0
    # 性能: single chat turn < 500ms (mock LLM 实测 ms 级)
    assert wall < 0.5, f"mock chat 单 turn 超 500ms: {wall:.3f}s"


# ─────────────────────── 2.5 等过期 → auto destroy → unpin → sessions 空 ──────


def test_2_5_auto_destroy_after_expire_unpins_and_clears_sessions(
    alice_vault: Path, bob_vault: Path,
    both_instances: dict[str, dict[str, Any]], python_helper_pkg,
) -> None:
    """bob borrow 3s · sleep 3.2s · auto_destroy_expired_sessions → scanned=1 destroyed=1 · IPFS unpin · sessions 空."""
    from nacl.public import PrivateKey

    from sisoul.friend.skill_borrow import (
        auto_destroy_expired_sessions,
        get_active_skill_package,
        get_borrow_session,
        list_borrow_sessions,
        request_borrow_skill,
    )
    from sisoul.friend.skill_ipfs import (
        SkillPinDB,
        SkillPinRecord,
        register_mock_blob,
    )
    from sisoul.friend.skill_package import decrypt_skill_package, encrypt_skill_package

    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    alice_pub = alice_priv.public_key
    bob_pub = bob_priv.public_key

    bob_skill_db = bob_vault / "skill_borrow.db"
    bob_pin_db = bob_vault / "skill_pins.db"
    bob_ledger_db = bob_vault / "ledger.db"
    bob_tmp = bob_vault / "skill-tmp"
    pkg = python_helper_pkg
    bob_did = _bob_did_str(both_instances)

    def provider(_o, _s):
        blob = encrypt_skill_package(pkg, bob_pub, alice_priv)
        cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
        register_mock_blob(cid, blob)
        with SkillPinDB(db_path=bob_pin_db) as db:
            now = int(time.time())
            db.upsert(SkillPinRecord(
                cid=cid, owner_did=pkg.owner_did, skill_id=pkg.skill_id,
                pinned_at=now, expires_at=now + 24 * 3600,
                size_bytes=len(blob), pinata_pinned=False,
            ))
        return blob, cid

    def decryptor(blob):
        return decrypt_skill_package(blob, alice_pub, bob_priv)

    res = request_borrow_skill(
        owner_did=pkg.owner_did, skill_id=pkg.skill_id,
        borrower_did=bob_did,
        duration_minutes=30, duration_seconds_override=3,
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=bob_skill_db, tmp_root=bob_tmp,
        pin_db_path=bob_pin_db,
        ledger_db=bob_ledger_db,
        enqueue_onchain=False,
    )

    sid = res.session.session_id
    tmp_dir = Path(res.session.local_decrypted_path)
    cid = res.session.ipfs_cid
    assert tmp_dir.exists()

    # 真等过期
    time.sleep(3.2)

    sched_out = auto_destroy_expired_sessions(
        db_path=bob_skill_db,
        pin_db_path=bob_pin_db,
        ledger_db=bob_ledger_db,
        enqueue_onchain=False,
    )
    assert sched_out["scanned"] == 1
    assert sched_out["destroyed"] == 1
    assert sched_out["errors"] == []

    # session 现 destroyed
    final = get_borrow_session(sid, db_path=bob_skill_db)
    assert final.status == "destroyed"
    assert final.destroy_reason == "auto-expired"
    assert final.destroyed_at is not None

    # tmp dir 物理消失
    assert not tmp_dir.exists()

    # 内存 cache 清
    assert get_active_skill_package(sid) is None

    # sisoul skill sessions --mine-as-borrower (only_active=True) → 空
    active_sessions = list_borrow_sessions(
        borrower_did=bob_did, db_path=bob_skill_db, only_active=True,
    )
    assert active_sessions == [], f"应无活跃 session: {active_sessions}"

    # IPFS unpin: 本地 DB mark unpinned (mock cid 走本地标记路径)
    with SkillPinDB(db_path=bob_pin_db) as db:
        rec = db.get(cid)
        assert rec is not None
        assert rec.unpinned is True, "IPFS pin 应被 unpin (本地标 unpinned=1)"


# ─────────────────────── 2.6 CANARY 0 leak (隐私关键) ─────────────────────────


def _grep_canary_in_bytes(canary: str, path: Path) -> bool:
    try:
        b = path.read_bytes()
    except Exception:
        return False
    return canary.encode() in b


def _scan_dir(root: Path, canary: str, skip_db: bool = False) -> list[str]:
    leaks: list[str] = []
    if not root.exists():
        return leaks
    for r, _dirs, files in os.walk(root):
        for fn in files:
            p = Path(r) / fn
            if skip_db and p.suffix == ".db":
                continue
            if _grep_canary_in_bytes(canary, p):
                leaks.append(str(p))
    return leaks


def test_2_6_canary_zero_leak_after_destroy(
    alice_vault: Path, bob_vault: Path,
    both_instances: dict[str, dict[str, Any]], python_helper_pkg,
) -> None:
    """bob borrow → use → end (manual) → 扫 bob_vault 全文件 → 0 CANARY 命中 · tmp 不存在 · ledger 1 条 metadata."""
    from nacl.public import PrivateKey

    from sisoul.friend.skill_borrow import (
        end_skill_borrow_session,
        proxy_skill_chat,
        request_borrow_skill,
    )
    from sisoul.friend.skill_ipfs import register_mock_blob, SkillPinDB, SkillPinRecord
    from sisoul.friend.skill_package import decrypt_skill_package, encrypt_skill_package

    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    alice_pub = alice_priv.public_key
    bob_pub = bob_priv.public_key

    bob_skill_db = bob_vault / "skill_borrow.db"
    bob_pin_db = bob_vault / "skill_pins.db"
    bob_ledger_db = bob_vault / "ledger.db"
    bob_tmp = bob_vault / "skill-tmp"
    pkg = python_helper_pkg
    bob_did = _bob_did_str(both_instances)

    def provider(_o, _s):
        blob = encrypt_skill_package(pkg, bob_pub, alice_priv)
        cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
        register_mock_blob(cid, blob)
        with SkillPinDB(db_path=bob_pin_db) as db:
            now = int(time.time())
            db.upsert(SkillPinRecord(
                cid=cid, owner_did=pkg.owner_did, skill_id=pkg.skill_id,
                pinned_at=now, expires_at=now + 24 * 3600,
                size_bytes=len(blob), pinata_pinned=False,
            ))
        return blob, cid

    def decryptor(blob):
        return decrypt_skill_package(blob, alice_pub, bob_priv)

    res = request_borrow_skill(
        owner_did=pkg.owner_did, skill_id=pkg.skill_id,
        borrower_did=bob_did,
        duration_minutes=30,
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=bob_skill_db, tmp_root=bob_tmp,
        pin_db_path=bob_pin_db,
        ledger_db=bob_ledger_db,
        enqueue_onchain=False,
    )
    sid = res.session.session_id
    tmp_dir = Path(res.session.local_decrypted_path)

    # 前置 sanity: CANARY 在 tmp examples.json 中 (这是 borrow 期内的临时状态, 正常)
    pre_leaks = _scan_dir(bob_vault, SKILL_CANARY, skip_db=True)
    assert any("examples.json" in p for p in pre_leaks), (
        f"borrow 期内 examples.json 应含 CANARY (sanity): {pre_leaks}"
    )

    # bob 用一轮 chat
    def mock_fwd(prompt, model, provider, api_key=None, **kw):
        return "[mock]", 30, 20
    proxy_skill_chat(
        session_id=sid, prompt="something", forwarder=mock_fwd,
        db_path=bob_skill_db,
    )

    # 主动 end session
    end_skill_borrow_session(
        sid, reason="manual",
        db_path=bob_skill_db,
        pin_db_path=bob_pin_db,
        ledger_db=bob_ledger_db,
        enqueue_onchain=False,
    )

    # === 隐私关键 4 条 ===

    # 1. bob_vault 全文件 (含 SQLite DB) 0 CANARY 命中
    leaks = _scan_dir(bob_vault, SKILL_CANARY, skip_db=False)
    assert leaks == [], f"DESTROY 后 bob_vault 漏 CANARY: {leaks}"

    # 2. tmp dir 物理消失
    assert not tmp_dir.exists(), f"tmp dir 应物理消失: {tmp_dir}"

    # 3. bob 进程 (本测试进程) /proc 不存于 mac; 替代验证: _ACTIVE_SESSIONS 内存清
    from sisoul.friend.skill_borrow import _ACTIVE_SESSIONS, get_active_skill_package
    assert sid not in _ACTIVE_SESSIONS
    assert get_active_skill_package(sid) is None

    # 4. ledger 写了 1 条 borrow attestation (metadata, 不含 prompt/CANARY)
    from sisoul.friend.ledger import ReciprocityLedger
    led = ReciprocityLedger(db_path=bob_ledger_db, self_did=bob_did)
    try:
        # ledger 端 query (注意 dev-D 签名: query_balance(friend_did, *, self_did=...))
        bal = led.query_balance(pkg.owner_did, self_did=bob_did)
        # 应至少 1 次 borrow (ai_skill amount=1; resource_type 不影响 borrowed_total 聚合)
        assert bal.borrowed_total >= 1, f"ledger 应记 bob borrow alice 至少 1 次: {bal}"
        assert bob_ledger_db.exists()
    finally:
        led.close()

    # ledger.db (SQLite 二进制) 也不能含 CANARY (sanity, SQLite 不会, 但确保)
    if bob_ledger_db.exists():
        assert SKILL_CANARY.encode() not in bob_ledger_db.read_bytes(), (
            "ledger.db 不应含 CANARY (它只记 metadata)"
        )

    # 5. alice_vault 全文件: alice 自己 owned/<id>.json 是 plaintext 含 CANARY 正常
    #    但 alice 不应 leak 到 bob_vault 任何文件 (跨 vault 隔离)
    a_to_b_leaks = _scan_dir(bob_vault, SKILL_CANARY, skip_db=False)
    assert a_to_b_leaks == [], (
        f"alice owned plaintext 不应 leak 到 bob_vault: {a_to_b_leaks}"
    )


# ─────────────────────── 2.7 反向: alice 撤回 lend → bob 借不到 ──────────────


def test_2_7_alice_revoke_lend_then_bob_borrow_denied(
    alice_vault: Path, bob_vault: Path,
    both_instances: dict[str, dict[str, Any]], python_helper_pkg,
) -> None:
    """alice revoke alice friend perm (整 friend revoked) · check_permission 拒 bob borrow 请求."""
    from sisoul.friend.anti_abuse import revoke_friend_permission
    from sisoul.friend.permissions import (
        AISkillShare,
        FriendPermission,
        check_permission,
        save_permissions,
    )

    bob_did = _bob_did_str(both_instances)
    perms_dir = alice_vault / "friends"

    # alice 给 bob 配 ai_skill perm: enabled (dev-C AISkillShare 实际 schema)
    perm = FriendPermission(
        friend_did=bob_did,
        ai_skill_share=AISkillShare(
            enabled=True,
            mode="strong-tie-auto",
            skills=["python-helper"],
            per_session_max_minutes=60,
        ),
    )
    save_permissions(bob_did, perm, perms_dir=perms_dir)

    # 撤前: ai_skill check_permission 应 allow
    ok, reason = check_permission(
        bob_did, "ai_skill", 30,
        model="python-helper",
        perms_dir=perms_dir,
        current_usage=0,
    )
    assert ok, f"revoke 前应允: {reason}"

    # alice revoke bob friend permission
    result = revoke_friend_permission(
        bob_did, reason="qa_c_test_revoke",
        perms_dir=perms_dir,
        onchain_publisher=lambda did, r: None,
    )
    assert result["revoked"] is True

    # 撤后: 立即拒
    ok2, reason2 = check_permission(
        bob_did, "ai_skill", 30,
        model="python-helper",
        perms_dir=perms_dir,
        current_usage=0,
    )
    assert ok2 is False, f"revoke 后应拒: {reason2}"
    assert reason2.startswith("revoked:") or "revoked" in reason2.lower(), (
        f"reason 应含 revoked: 实际 {reason2}"
    )
