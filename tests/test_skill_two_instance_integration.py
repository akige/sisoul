"""tests for sisoul skill 同机 alice/bob 双 instance 完整集成 (波 6 dev-A).

模拟两个 sisoul 实例: alice 跟 bob 各自有独立 DB + seed + keypair. bob 训 dummy
python-helper skill → alice 借 30s (缩短) → 模拟使用 → 30s 后 auto destroy →
验证 tmp dir 清 + 0 leak.

跟主线 test_skill_borrow.py 区别: 这里用**两套** keypair (alice priv != bob priv),
真模拟跨实例加密通道 (不是 self-loop).
"""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path

import pytest
from nacl.public import PrivateKey

from sisoul.friend.skill_borrow import (
    _ACTIVE_SESSIONS,
    auto_destroy_expired_sessions,
    end_skill_borrow_session,
    get_active_skill_package,
    get_borrow_session,
    proxy_skill_chat,
    request_borrow_skill,
)
from sisoul.friend.skill_ipfs import (
    SkillPinDB,
    clear_mock_blob_cache,
    register_mock_blob,
)
from sisoul.friend.skill_package import (
    SkillPackage,
    decrypt_skill_package,
    encrypt_skill_package,
    package_skill,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch):
    _ACTIVE_SESSIONS.clear()
    clear_mock_blob_cache()
    monkeypatch.setenv("HOME", str(tmp_path))
    yield
    _ACTIVE_SESSIONS.clear()
    clear_mock_blob_cache()


def test_two_instance_alice_borrow_bob_python_helper(tmp_path):
    """端到端: bob 训 → alice 借 30s → use → auto destroy → 0 leak."""
    # ── 1. bob 训 dummy python-helper ────────────────────────────────────
    bob_did = "did:sisoul:bob"
    alice_did = "did:sisoul:alice"
    marker = "BOB-PYHELPER-SECRET-" + uuid.uuid4().hex
    bob_skill = package_skill(
        name="python-helper",
        owner_did=bob_did,
        system_prompt=(
            f"You are a Python helper. Internal note: {marker}. "
            "Be concise, give type hints, prefer stdlib."
        ),
        description="Python coding helper",
        version="0.1.0",
        examples=[
            {"q": "how to read a file?", "a": "Use Path.read_text()"},
            {"q": "list comprehension?", "a": "[x*2 for x in lst]"},
        ],
        personality_traits=["concise", "stdlib-first"],
        recommended_models=["claude-opus-4-7"],
    )

    # ── 2. 两套独立 keypair (alice / bob 各自独立) ───────────────────────
    bob_priv = PrivateKey.generate()
    alice_priv = PrivateKey.generate()
    bob_pub = bob_priv.public_key
    alice_pub = alice_priv.public_key

    # ── 3. 各自独立 DB / tmp_root ────────────────────────────────────────
    alice_db = tmp_path / "alice" / "skill_borrow.db"
    alice_pin_db = tmp_path / "alice" / "skill_pins.db"
    alice_ledger_db = tmp_path / "alice" / "ledger.db"
    alice_tmp = tmp_path / "alice" / "skill-tmp"

    # ── 4. bob 端 provider: 加密 + IPFS pin (mock) ───────────────────────
    def bob_owner_provider(_owner: str, _skill: str) -> tuple[bytes, str]:
        """模拟 bob daemon 收到 alice 借请求后: encrypt(skill, alice_pub) + IPFS pin."""
        blob = encrypt_skill_package(bob_skill, alice_pub, bob_priv)
        cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
        register_mock_blob(cid, blob)
        # bob 本地 pin DB 记一条
        from sisoul.friend.skill_ipfs import SkillPinRecord
        with SkillPinDB(db_path=alice_pin_db) as db:
            now = int(time.time())
            db.upsert(SkillPinRecord(
                cid=cid, owner_did=bob_did, skill_id="python-helper",
                pinned_at=now, expires_at=now + 24 * 3600,
                size_bytes=len(blob), pinata_pinned=False,
            ))
        return blob, cid

    # ── 5. alice 端 decryptor: bob pub × alice priv ──────────────────────
    def alice_decryptor(blob: bytes) -> SkillPackage:
        return decrypt_skill_package(blob, bob_pub, alice_priv)

    # ── 6. alice 借 30 秒 (test 缩短 lifecycle) ──────────────────────────
    res = request_borrow_skill(
        owner_did=bob_did,
        skill_id="python-helper",
        borrower_did=alice_did,
        duration_minutes=30,  # 但 override 3 秒
        duration_seconds_override=3,
        encrypted_skill_provider=bob_owner_provider,
        decrypt_callback=alice_decryptor,
        skip_permission_check=True,
        db_path=alice_db,
        tmp_root=alice_tmp,
        ledger_db=alice_ledger_db,
        enqueue_onchain=False,
    )

    sid = res.session.session_id
    tmp_dir = Path(res.session.local_decrypted_path)

    # ── 7. 验证: skill 装载 OK ───────────────────────────────────────────
    assert res.session.status == "active"
    assert res.session.owner_did == bob_did
    assert res.session.borrower_did == alice_did
    assert res.skill_package_fingerprint == bob_skill.fingerprint
    assert tmp_dir.exists()

    # alice 能拿到解密的 skill
    cached_pkg = get_active_skill_package(sid)
    assert cached_pkg is not None
    assert marker in cached_pkg.contents.system_prompt

    # marker 也写到 tmp_dir
    sp_file_content = (tmp_dir / "system_prompt.md").read_text(encoding="utf-8")
    assert marker in sp_file_content

    # ── 8. alice 用 skill 跑 mock chat ───────────────────────────────────
    def mock_fwd(prompt, model, provider, api_key=None, **kw):
        # 不真打 LLM, 但 prompt 应含 bob 的 system prompt
        return f"[mock] echo of {model}", 50, 30

    chat_result = proxy_skill_chat(
        session_id=sid,
        prompt="how to write a Python list comprehension?",
        forwarder=mock_fwd,
        db_path=alice_db,
    )
    assert chat_result["tokens_used"] == 80
    assert chat_result["model_used"] == "claude-opus-4-7"
    assert chat_result["skill_id"] == "python-helper"

    # ── 9. 等过期 + auto destroy scheduler ───────────────────────────────
    time.sleep(3.2)
    scheduler_res = auto_destroy_expired_sessions(
        db_path=alice_db,
        pin_db_path=alice_pin_db,
        ledger_db=alice_ledger_db,
        enqueue_onchain=False,
    )
    assert scheduler_res["scanned"] == 1
    assert scheduler_res["destroyed"] == 1
    assert scheduler_res["errors"] == []

    # ── 10. 验证 destroy 后果 ────────────────────────────────────────────
    final = get_borrow_session(sid, db_path=alice_db)
    assert final.status == "destroyed"
    assert final.destroy_reason == "auto-expired"

    # tmp 物理消失
    assert not tmp_dir.exists()

    # 内存 cache 清
    assert get_active_skill_package(sid) is None

    # ── 11. 0 leak 扫: marker 不在 alice_tmp 树 ──────────────────────────
    def _scan(root: Path, m: str) -> list[Path]:
        if not root.exists():
            return []
        hits = []
        bm = m.encode("utf-8")
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix == ".db":  # SQLite 二进制, 跳
                continue
            try:
                if bm in f.read_bytes():
                    hits.append(f)
            except (OSError, PermissionError):
                continue
        return hits

    alice_root = tmp_path / "alice"
    leaks = _scan(alice_root, marker)
    assert leaks == [], f"LEAK after auto destroy: {leaks}"

    # ── 12. ledger 也 IPFS unpin (本地标 unpinned) ──────────────────────
    with SkillPinDB(db_path=alice_pin_db) as db:
        rec = db.get(res.session.ipfs_cid)
        assert rec is not None
        assert rec.unpinned is True


def test_two_instance_alice_can_end_early(tmp_path):
    """alice 不等 30 分钟到, 主动 end. 同样 wipe."""
    bob_did = "did:sisoul:bob"
    alice_did = "did:sisoul:alice"
    marker = "EARLY-END-MARKER-" + uuid.uuid4().hex

    bob_skill = package_skill(
        name="quick-skill",
        owner_did=bob_did,
        system_prompt=f"Test: {marker}",
    )
    bob_priv = PrivateKey.generate()
    alice_priv = PrivateKey.generate()
    bob_pub = bob_priv.public_key
    alice_pub = alice_priv.public_key

    alice_db = tmp_path / "alice_db.sqlite"
    alice_tmp = tmp_path / "alice_tmp"
    alice_pin_db = tmp_path / "alice_pin.db"

    def provider(_o, _s):
        blob = encrypt_skill_package(bob_skill, alice_pub, bob_priv)
        cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
        register_mock_blob(cid, blob)
        return blob, cid

    def decryptor(blob):
        return decrypt_skill_package(blob, bob_pub, alice_priv)

    res = request_borrow_skill(
        owner_did=bob_did, skill_id="quick-skill", borrower_did=alice_did,
        duration_minutes=30,
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=alice_db, tmp_root=alice_tmp,
    )

    sid = res.session.session_id
    tmp_dir = Path(res.session.local_decrypted_path)
    assert tmp_dir.exists()

    # alice 提前 end (不等 30 分钟)
    final = end_skill_borrow_session(
        sid, reason="user-changed-mind",
        db_path=alice_db, pin_db_path=alice_pin_db,
        enqueue_onchain=False,
    )
    assert final.status == "destroyed"
    assert final.destroy_reason == "user-changed-mind"
    assert not tmp_dir.exists()
