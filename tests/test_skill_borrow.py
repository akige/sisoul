"""tests for sisoul.friend.skill_borrow (波 6 dev-A) — lifecycle 主线.

覆盖:
- request_borrow_skill 完整 (provider + decryptor + skip_permission_check)
- duration_seconds_override 缩短 lifecycle 验证 auto destroy
- end_skill_borrow_session: wipe tmp + IPFS unpin + ledger entry
- auto_destroy_expired_sessions scheduler
- list_borrow_sessions / get_borrow_session
- proxy_skill_chat 走 mock forwarder
- session 内存 cache 释放 (end 后 get_active_skill_package 返 None)
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
from nacl.public import PrivateKey

from sisoul.friend.skill_borrow import (
    DEFAULT_BORROW_DURATION_MINUTES,
    SkillBorrowDB,
    SkillBorrowError,
    SkillBorrowExpiredError,
    SkillBorrowSession,
    SkillBorrowSessionNotFoundError,
    _ACTIVE_SESSIONS,
    auto_destroy_expired_sessions,
    end_skill_borrow_session,
    get_active_skill_package,
    get_borrow_session,
    list_borrow_sessions,
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


@pytest.fixture
def borrow_db(tmp_path: Path) -> Path:
    return tmp_path / "skill_borrow.db"


@pytest.fixture
def pin_db(tmp_path: Path) -> Path:
    return tmp_path / "skill_pins.db"


@pytest.fixture
def ledger_db(tmp_path: Path) -> Path:
    return tmp_path / "ledger.db"


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    return tmp_path / "skill-tmp"


@pytest.fixture(autouse=True)
def _clear_state(monkeypatch, tmp_path: Path):
    """每 test 隔离: 清 _ACTIVE_SESSIONS + mock blob cache + monkeypatch HOME."""
    _ACTIVE_SESSIONS.clear()
    clear_mock_blob_cache()
    monkeypatch.setenv("HOME", str(tmp_path))
    yield
    _ACTIVE_SESSIONS.clear()
    clear_mock_blob_cache()


@pytest.fixture
def self_keypair():
    """alice/bob 同 instance, self-loop 加密. priv index=0."""
    return PrivateKey.generate()


@pytest.fixture
def sample_pkg():
    return package_skill(
        name="solidity-expert",
        owner_did="did:sisoul:bob",
        system_prompt="You are a security-paranoid Solidity expert.",
        description="DeFi specialist",
        version="0.1.0",
        examples=[{"q": "what is reentrancy?", "a": "..."}],
        personality_traits=["pedantic"],
        recommended_models=["claude-opus-4-7"],
    )


def _make_provider_and_decryptor(pkg: SkillPackage, keypair: PrivateKey):
    """self-loop: 同 keypair 双向 (mock 一台机器跑 owner + borrower 模拟)."""
    pub = keypair.public_key

    def provider(_o: str, _s: str) -> tuple[bytes, str]:
        blob = encrypt_skill_package(pkg, pub, keypair)
        cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
        register_mock_blob(cid, blob)
        return blob, cid

    def decryptor(blob: bytes) -> SkillPackage:
        return decrypt_skill_package(blob, pub, keypair)

    return provider, decryptor


# ── request_borrow_skill ────────────────────────────────────────────────────


def test_request_borrow_basic(sample_pkg, self_keypair, borrow_db, tmp_root, ledger_db):
    provider, decryptor = _make_provider_and_decryptor(sample_pkg, self_keypair)
    res = request_borrow_skill(
        owner_did="did:sisoul:bob",
        skill_id="solidity-expert",
        borrower_did="did:sisoul:alice",
        duration_minutes=30,
        encrypted_skill_provider=provider,
        decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=borrow_db,
        tmp_root=tmp_root,
        ledger_db=ledger_db,
        enqueue_onchain=False,
    )
    assert res.session.status == "active"
    assert res.session.skill_id == "solidity-expert"
    assert res.session.owner_did == "did:sisoul:bob"
    assert res.session.borrower_did == "did:sisoul:alice"
    assert res.session.duration_minutes == 30
    assert (res.session.expires_at - res.session.started_at) == 30 * 60
    assert res.session.ipfs_cid is not None
    assert res.session.local_decrypted_path is not None
    # tmp dir 实际写出了 system_prompt.md / package.json / examples.json
    tmp_dir = Path(res.session.local_decrypted_path)
    assert tmp_dir.exists()
    assert (tmp_dir / "system_prompt.md").exists()
    assert (tmp_dir / "package.json").exists()
    assert (tmp_dir / "examples.json").exists()
    # fingerprint 验证
    assert res.skill_package_fingerprint == sample_pkg.fingerprint


def test_request_borrow_duration_override_for_test(
    sample_pkg, self_keypair, borrow_db, tmp_root
):
    provider, decryptor = _make_provider_and_decryptor(sample_pkg, self_keypair)
    res = request_borrow_skill(
        owner_did="bob", skill_id="solidity-expert", borrower_did="alice",
        duration_minutes=30,  # 但 override 2 秒
        duration_seconds_override=2,
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=borrow_db, tmp_root=tmp_root,
    )
    assert (res.session.expires_at - res.session.started_at) == 2


def test_request_borrow_no_provider_raises(sample_pkg, self_keypair):
    _, decryptor = _make_provider_and_decryptor(sample_pkg, self_keypair)
    with pytest.raises(SkillBorrowError, match="encrypted_skill_provider"):
        request_borrow_skill(
            owner_did="bob", skill_id="s", borrower_did="alice",
            encrypted_skill_provider=None,
            decrypt_callback=decryptor,
            skip_permission_check=True,
        )


def test_request_borrow_no_decrypt_raises(sample_pkg, self_keypair):
    provider, _ = _make_provider_and_decryptor(sample_pkg, self_keypair)
    with pytest.raises(SkillBorrowError, match="decrypt_callback"):
        request_borrow_skill(
            owner_did="bob", skill_id="s", borrower_did="alice",
            encrypted_skill_provider=provider,
            decrypt_callback=None,
            skip_permission_check=True,
        )


def test_request_borrow_duration_bounds(sample_pkg, self_keypair):
    provider, decryptor = _make_provider_and_decryptor(sample_pkg, self_keypair)
    with pytest.raises(SkillBorrowError, match="duration_minutes"):
        request_borrow_skill(
            owner_did="bob", skill_id="s", borrower_did="alice",
            duration_minutes=0,
            encrypted_skill_provider=provider, decrypt_callback=decryptor,
            skip_permission_check=True,
        )
    with pytest.raises(SkillBorrowError, match="duration_minutes"):
        request_borrow_skill(
            owner_did="bob", skill_id="s", borrower_did="alice",
            duration_minutes=99999,
            encrypted_skill_provider=provider, decrypt_callback=decryptor,
            skip_permission_check=True,
        )


def test_request_borrow_stashes_active(sample_pkg, self_keypair, borrow_db, tmp_root):
    provider, decryptor = _make_provider_and_decryptor(sample_pkg, self_keypair)
    res = request_borrow_skill(
        owner_did="bob", skill_id="s", borrower_did="alice",
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=borrow_db, tmp_root=tmp_root,
    )
    cached = get_active_skill_package(res.session.session_id)
    assert cached is not None
    assert cached.skill_id == sample_pkg.skill_id


# ── end_skill_borrow_session ───────────────────────────────────────────────


def test_end_session_wipes_tmp_and_destroys(
    sample_pkg, self_keypair, borrow_db, tmp_root, ledger_db, pin_db
):
    provider, decryptor = _make_provider_and_decryptor(sample_pkg, self_keypair)
    res = request_borrow_skill(
        owner_did="bob", skill_id="s", borrower_did="alice",
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=borrow_db, tmp_root=tmp_root,
        ledger_db=ledger_db,
    )
    tmp_dir = Path(res.session.local_decrypted_path)
    assert tmp_dir.exists()

    s = end_skill_borrow_session(
        res.session.session_id,
        reason="manual-test",
        db_path=borrow_db,
        pin_db_path=pin_db,
        ledger_db=ledger_db,
        enqueue_onchain=False,
    )
    assert s.status == "destroyed"
    assert s.destroy_reason == "manual-test"
    assert s.destroyed_at is not None
    # tmp dir 已 wipe
    assert not tmp_dir.exists()
    # 内存 cache 清
    assert get_active_skill_package(res.session.session_id) is None
    # ledger 写入 (dev-D 可达时, 这里写入)
    # entry_id 可 None (dev-D 不可达 fail-open) 或 str
    assert s.ledger_entry_id is None or isinstance(s.ledger_entry_id, str)


def test_end_session_idempotent(
    sample_pkg, self_keypair, borrow_db, tmp_root
):
    provider, decryptor = _make_provider_and_decryptor(sample_pkg, self_keypair)
    res = request_borrow_skill(
        owner_did="bob", skill_id="s", borrower_did="alice",
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=borrow_db, tmp_root=tmp_root,
    )
    s1 = end_skill_borrow_session(res.session.session_id, db_path=borrow_db)
    s2 = end_skill_borrow_session(res.session.session_id, db_path=borrow_db)
    assert s1.status == "destroyed"
    assert s2.status == "destroyed"


def test_end_session_not_found(borrow_db):
    with pytest.raises(SkillBorrowSessionNotFoundError):
        end_skill_borrow_session("nonexistent", db_path=borrow_db)


# ── auto_destroy_expired_sessions ──────────────────────────────────────────


def test_auto_destroy_expired_sessions(
    sample_pkg, self_keypair, borrow_db, tmp_root, ledger_db, pin_db
):
    provider, decryptor = _make_provider_and_decryptor(sample_pkg, self_keypair)
    # 起 1 个 1 秒后过期, 1 个 长期 active
    short = request_borrow_skill(
        owner_did="bob", skill_id="s1", borrower_did="alice",
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        duration_seconds_override=1,
        db_path=borrow_db, tmp_root=tmp_root, ledger_db=ledger_db,
    )
    long_ = request_borrow_skill(
        owner_did="bob", skill_id="s2", borrower_did="alice",
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        duration_minutes=30,
        db_path=borrow_db, tmp_root=tmp_root, ledger_db=ledger_db,
    )
    # 等过期
    time.sleep(1.2)
    res = auto_destroy_expired_sessions(
        db_path=borrow_db, pin_db_path=pin_db, ledger_db=ledger_db, enqueue_onchain=False,
    )
    assert res["scanned"] == 1
    assert res["destroyed"] == 1
    assert res["errors"] == []
    # short 已 destroyed
    s = get_borrow_session(short.session.session_id, db_path=borrow_db)
    assert s.status == "destroyed"
    assert s.destroy_reason == "auto-expired"
    # long 还 active
    s2 = get_borrow_session(long_.session.session_id, db_path=borrow_db)
    assert s2.status == "active"


def test_auto_destroy_empty(borrow_db, pin_db, ledger_db):
    res = auto_destroy_expired_sessions(
        db_path=borrow_db, pin_db_path=pin_db, ledger_db=ledger_db, enqueue_onchain=False,
    )
    assert res["scanned"] == 0
    assert res["destroyed"] == 0


# ── list / get ─────────────────────────────────────────────────────────────


def test_list_borrow_sessions_active_only(
    sample_pkg, self_keypair, borrow_db, tmp_root
):
    provider, decryptor = _make_provider_and_decryptor(sample_pkg, self_keypair)
    res = request_borrow_skill(
        owner_did="bob", skill_id="s", borrower_did="alice",
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=borrow_db, tmp_root=tmp_root,
    )
    items = list_borrow_sessions(borrower_did="alice", db_path=borrow_db)
    assert len(items) == 1
    assert items[0].session_id == res.session.session_id

    # 结束后默认 only_active=True 看不到
    end_skill_borrow_session(res.session.session_id, db_path=borrow_db)
    items = list_borrow_sessions(borrower_did="alice", db_path=borrow_db)
    assert items == []
    # show_all=True 能看到
    items = list_borrow_sessions(borrower_did="alice", db_path=borrow_db, only_active=False)
    assert len(items) == 1
    assert items[0].status == "destroyed"


# ── proxy_skill_chat ────────────────────────────────────────────────────────


def test_proxy_skill_chat_mock_forwarder(
    sample_pkg, self_keypair, borrow_db, tmp_root
):
    provider, decryptor = _make_provider_and_decryptor(sample_pkg, self_keypair)
    res = request_borrow_skill(
        owner_did="bob", skill_id="s", borrower_did="alice",
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=borrow_db, tmp_root=tmp_root,
    )

    def mock_fwd(prompt, model, provider, api_key=None, **kw):
        return f"ECHO[{model}]: {prompt[:30]}", 10, 20

    out = proxy_skill_chat(
        session_id=res.session.session_id,
        prompt="how to avoid reentrancy?",
        forwarder=mock_fwd,
        db_path=borrow_db,
    )
    assert "ECHO[claude-opus-4-7]" in out["text"]  # 取 recommended_models[0]
    assert out["tokens_used"] == 30
    assert out["model_used"] == "claude-opus-4-7"
    assert out["skill_id"] == "s"
    assert out["session_id"] == res.session.session_id
    assert out["session_remaining_sec"] > 0


def test_proxy_chat_session_not_found(borrow_db):
    with pytest.raises(SkillBorrowSessionNotFoundError):
        proxy_skill_chat(session_id="bs_nope", prompt="x", db_path=borrow_db)


def test_proxy_chat_expired(sample_pkg, self_keypair, borrow_db, tmp_root):
    provider, decryptor = _make_provider_and_decryptor(sample_pkg, self_keypair)
    res = request_borrow_skill(
        owner_did="bob", skill_id="s", borrower_did="alice",
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        duration_seconds_override=1,
        db_path=borrow_db, tmp_root=tmp_root,
    )
    time.sleep(1.2)

    def mock_fwd(prompt, model, provider, api_key=None, **kw):
        return "x", 1, 1

    with pytest.raises(SkillBorrowExpiredError):
        proxy_skill_chat(
            session_id=res.session.session_id, prompt="x",
            forwarder=mock_fwd, db_path=borrow_db,
        )


# ── SkillBorrowSession 数据 ────────────────────────────────────────────────


def test_session_remaining_sec():
    now = int(time.time())
    s = SkillBorrowSession(
        session_id="bs_x", skill_id="s", borrower_did="a", owner_did="b",
        started_at=now, expires_at=now + 100,
    )
    assert s.remaining_seconds() > 0
    assert s.remaining_seconds() <= 100


def test_session_is_expired():
    now = int(time.time())
    s_expired = SkillBorrowSession(
        session_id="bs_x", skill_id="s", borrower_did="a", owner_did="b",
        started_at=now - 100, expires_at=now - 10,
    )
    s_active = SkillBorrowSession(
        session_id="bs_y", skill_id="s", borrower_did="a", owner_did="b",
        started_at=now, expires_at=now + 100,
    )
    assert s_expired.is_expired() is True
    assert s_active.is_expired() is False
