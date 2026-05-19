"""tests for skill borrow wipe (dev-A 隐私核心).

模仿 dev-B test_proxy_no_leak.py 的方式: 给 SkillPackage 注入独特字串,
端 end session 后扫 tmp dir + (本机 0 leak / 简化 grep). 核心 invariant:

1. end 后 tmp dir 物理消失
2. _ACTIVE_SESSIONS 不再持 SkillPackage 引用
3. SkillPackage 的独特字串不出现在 ~/.sisoul/ 子目录 (除了 borrow_db / ledger_db 元数据)
4. tmp dir 任何文件读不出 (因已 rmtree)
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from pathlib import Path

import pytest
from nacl.public import PrivateKey

from sisoul.friend.skill_borrow import (
    _ACTIVE_SESSIONS,
    end_skill_borrow_session,
    get_active_skill_package,
    request_borrow_skill,
)
from sisoul.friend.skill_ipfs import (
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


def _build_skill_with_unique_marker():
    """造 skill 含独特字串便于 leak 扫描."""
    marker = "ZZZ-SECRET-MARKER-" + uuid.uuid4().hex
    pkg = package_skill(
        name="secret-skill",
        owner_did="did:sisoul:bob",
        system_prompt=f"This is a secret system prompt containing {marker}",
        description="don't leak me",
        examples=[{"q": "secret question", "a": f"secret answer {marker}"}],
        personality_traits=["secret-paranoid"],
    )
    return pkg, marker


def _round_trip_provider(pkg: SkillPackage, keypair: PrivateKey):
    pub = keypair.public_key

    def provider(_o, _s):
        blob = encrypt_skill_package(pkg, pub, keypair)
        cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
        register_mock_blob(cid, blob)
        return blob, cid

    def decryptor(blob):
        return decrypt_skill_package(blob, pub, keypair)

    return provider, decryptor


# ── core: tmp dir 物理消失 ────────────────────────────────────────────────


def test_end_session_tmp_dir_physically_gone(tmp_path):
    pkg, marker = _build_skill_with_unique_marker()
    kp = PrivateKey.generate()
    provider, decryptor = _round_trip_provider(pkg, kp)

    tmp_root = tmp_path / "skill-tmp"
    borrow_db = tmp_path / "borrow.db"

    res = request_borrow_skill(
        owner_did="bob", skill_id="secret-skill", borrower_did="alice",
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=borrow_db, tmp_root=tmp_root,
    )

    tmp_dir = Path(res.session.local_decrypted_path)
    assert tmp_dir.exists()

    # 确认 marker 落地到了 system_prompt.md
    sp_content = (tmp_dir / "system_prompt.md").read_text(encoding="utf-8")
    assert marker in sp_content

    # end
    end_skill_borrow_session(res.session.session_id, db_path=borrow_db)

    # 物理消失
    assert not tmp_dir.exists()


def test_end_session_clears_memory_cache(tmp_path):
    pkg, _ = _build_skill_with_unique_marker()
    kp = PrivateKey.generate()
    provider, decryptor = _round_trip_provider(pkg, kp)

    tmp_root = tmp_path / "skill-tmp"
    borrow_db = tmp_path / "borrow.db"

    res = request_borrow_skill(
        owner_did="bob", skill_id="secret-skill", borrower_did="alice",
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=borrow_db, tmp_root=tmp_root,
    )
    assert get_active_skill_package(res.session.session_id) is not None

    end_skill_borrow_session(res.session.session_id, db_path=borrow_db)

    assert get_active_skill_package(res.session.session_id) is None


# ── leak scan: marker 不能出现在 tmp_root 或 home tmp 子树 ──────────────────


def _scan_tree_for_marker(root: Path, marker: str) -> list[Path]:
    """递归扫文件 (跳过 .db 元数据)"""
    if not root.exists():
        return []
    hits = []
    bm = marker.encode("utf-8")
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix == ".db":  # SQLite 二进制元数据, 不扫
            continue
        try:
            if bm in f.read_bytes():
                hits.append(f)
        except (OSError, PermissionError):
            continue
    return hits


def test_no_leak_after_end_session(tmp_path):
    pkg, marker = _build_skill_with_unique_marker()
    kp = PrivateKey.generate()
    provider, decryptor = _round_trip_provider(pkg, kp)

    tmp_root = tmp_path / "skill-tmp"
    borrow_db = tmp_path / "borrow.db"
    pin_db = tmp_path / "pin.db"
    ledger_db = tmp_path / "ledger.db"

    res = request_borrow_skill(
        owner_did="bob", skill_id="secret-skill", borrower_did="alice",
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=borrow_db, tmp_root=tmp_root,
        ledger_db=ledger_db,
    )

    # before end: marker 在 tmp dir
    hits_before = _scan_tree_for_marker(tmp_root, marker)
    assert len(hits_before) >= 1  # 至少 system_prompt.md 含

    end_skill_borrow_session(
        res.session.session_id,
        db_path=borrow_db,
        pin_db_path=pin_db,
        ledger_db=ledger_db,
        enqueue_onchain=False,
    )

    # after end: tmp_root 不应再含 marker (整个 session dir 已 rmtree)
    hits_after = _scan_tree_for_marker(tmp_root, marker)
    assert hits_after == [], f"LEAK: marker 在: {hits_after}"

    # 也扫 tmp_path 整树 (含 ledger/borrow.db 都是 SQLite, 被跳过; .json/.txt 不应含)
    hits_home = _scan_tree_for_marker(tmp_path, marker)
    # ledger note 字段不存 marker (只存 session_id), borrow.db 同;
    # 但 borrow.db 跳过 (.db 后缀). 所以应 0 leak.
    assert hits_home == [], f"LEAK in home tree: {hits_home}"


def test_auto_destroy_also_wipes(tmp_path):
    """auto_destroy_expired_sessions 走同 end_skill_borrow_session, 应同样 wipe."""
    from sisoul.friend.skill_borrow import auto_destroy_expired_sessions

    pkg, marker = _build_skill_with_unique_marker()
    kp = PrivateKey.generate()
    provider, decryptor = _round_trip_provider(pkg, kp)

    tmp_root = tmp_path / "skill-tmp"
    borrow_db = tmp_path / "borrow.db"
    pin_db = tmp_path / "pin.db"
    ledger_db = tmp_path / "ledger.db"

    res = request_borrow_skill(
        owner_did="bob", skill_id="secret-skill", borrower_did="alice",
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        duration_seconds_override=1,
        db_path=borrow_db, tmp_root=tmp_root, ledger_db=ledger_db,
    )
    tmp_dir = Path(res.session.local_decrypted_path)
    assert tmp_dir.exists()

    time.sleep(1.2)
    auto_destroy_expired_sessions(
        db_path=borrow_db, pin_db_path=pin_db, ledger_db=ledger_db,
        enqueue_onchain=False,
    )

    assert not tmp_dir.exists()
    hits = _scan_tree_for_marker(tmp_root, marker)
    assert hits == []


# ── end 出错时仍尽力 wipe (best-effort) ──────────────────────────────────


def test_end_session_continues_when_wipe_partial_fails(tmp_path, monkeypatch):
    """模拟 wipe 部分失败 (e.g. tmp file 在使用中). end 仍应标 destroyed + 清内存."""
    pkg, _ = _build_skill_with_unique_marker()
    kp = PrivateKey.generate()
    provider, decryptor = _round_trip_provider(pkg, kp)

    borrow_db = tmp_path / "borrow.db"
    tmp_root = tmp_path / "skill-tmp"

    res = request_borrow_skill(
        owner_did="bob", skill_id="secret-skill", borrower_did="alice",
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=borrow_db, tmp_root=tmp_root,
    )

    # 替换 _wipe_tmp_dir 模拟失败
    from sisoul.friend import skill_borrow as sb_mod
    monkeypatch.setattr(sb_mod, "_wipe_tmp_dir", lambda _p: False)

    s = end_skill_borrow_session(res.session.session_id, db_path=borrow_db)
    assert s.status == "destroyed"  # 仍标 destroyed
    assert get_active_skill_package(res.session.session_id) is None
