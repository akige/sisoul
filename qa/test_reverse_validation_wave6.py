"""波 6 qa-C · 反向验证 (Phase 4 下半 AI 技能 share).

§J-2 第 3 条 + §30 §3.4 R2 4 反模式. 测 broken input / 攻击 / 状态机错乱
能正确触发 abort / error / 拒绝, 不会默默通过.

5 类:
- A · skill package 错 recipient pubkey → DecryptionError
- B · IPFS pin 失败 → graceful + 通知用户 (不消耗 borrow lifecycle)
- C · 30min lifecycle 反向 (mock time advance) + 提前 end 后再用 → 拒
- D · skill chat 用 borrower 错 LLM key → 报错不消耗
- E · skill package tamper (modify encrypted bytes) → fail
- F · 额外 validate edge cases
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

import pytest
from nacl.public import PrivateKey

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _isolate():
    from sisoul.friend.skill_borrow import _ACTIVE_SESSIONS
    from sisoul.friend.skill_ipfs import clear_mock_blob_cache

    _ACTIVE_SESSIONS.clear()
    clear_mock_blob_cache()
    yield
    _ACTIVE_SESSIONS.clear()
    clear_mock_blob_cache()


def _make_pkg(name: str = "test-skill"):
    from sisoul.friend.skill_package import package_skill
    return package_skill(
        name=name,
        owner_did="did:sisoul:alice",
        system_prompt="You are a test skill.",
        description="reverse-val skill",
    )


# ─────────── A · skill package wrong recipient pubkey → DecryptError ───────────


def test_A1_decrypt_with_wrong_recipient_pubkey_raises() -> None:
    """alice encrypt(pkg, bob_pub) · 用 eve_priv 解密 → SkillPackageDecryptError."""
    from sisoul.friend.skill_package import (
        SkillPackageDecryptError,
        decrypt_skill_package,
        encrypt_skill_package,
    )

    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    eve_priv = PrivateKey.generate()

    pkg = _make_pkg()
    blob = encrypt_skill_package(pkg, bob_priv.public_key, alice_priv)

    with pytest.raises(SkillPackageDecryptError):
        decrypt_skill_package(blob, alice_priv.public_key, eve_priv)


def test_A2_decrypt_with_wrong_sender_pubkey_raises() -> None:
    """正 bob 端但用错 sender_pub (mallory_pub 替 alice_pub) → fail."""
    from sisoul.friend.skill_package import (
        SkillPackageDecryptError,
        decrypt_skill_package,
        encrypt_skill_package,
    )

    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    mallory_priv = PrivateKey.generate()
    pkg = _make_pkg()
    blob = encrypt_skill_package(pkg, bob_priv.public_key, alice_priv)

    with pytest.raises(SkillPackageDecryptError):
        decrypt_skill_package(blob, mallory_priv.public_key, bob_priv)


def test_A3_decrypt_too_short_blob_raises() -> None:
    """blob 长度 < nonce+mac 最小 → SkillPackageDecryptError."""
    from sisoul.friend.skill_package import (
        SkillPackageDecryptError,
        decrypt_skill_package,
    )

    bob_priv = PrivateKey.generate()
    alice_priv = PrivateKey.generate()
    with pytest.raises(SkillPackageDecryptError):
        decrypt_skill_package(b"too-short", alice_priv.public_key, bob_priv)


# ─────────── B · IPFS pin 失败 → graceful · 通知 (不无声吞) ───────────────────


def test_B1_pin_with_no_jwt_returns_mock_cid_with_warning(tmp_path, caplog) -> None:
    """无 PINATA_JWT · pin 走 mock fallback · 返 mockcid- · log warning."""
    import logging

    from sisoul.friend.skill_ipfs import SkillIPFSClient

    client = SkillIPFSClient(pinata_jwt=None, db_path=tmp_path / "pins.db")
    with caplog.at_level(logging.WARNING):
        rec = client.pin(
            b"some-encrypted-bytes",
            owner_did="did:sisoul:alice",
            skill_id="test-skill",
            expiry_hours=24,
        )
    assert rec.cid.startswith("mockcid-")
    assert rec.pinata_pinned is False
    assert any("PINATA_JWT" in r.getMessage() or "mock" in r.getMessage()
               for r in caplog.records), (
        f"应 log mock warning, 实际 log: {[r.getMessage() for r in caplog.records]}"
    )


def test_B2_pin_with_invalid_jwt_raises_skillpinerror(monkeypatch, tmp_path) -> None:
    """有 JWT 但 Pinata HTTP 失败 → SkillPinError (不悄悄返 mock)."""
    import httpx

    from sisoul.friend.skill_ipfs import SkillIPFSClient, SkillPinError

    class _BadResp:
        status_code = 401
        def raise_for_status(self):
            raise httpx.HTTPStatusError("401 Unauthorized", request=None, response=None)
        def json(self):
            return {"error": "invalid jwt"}

    class _BadClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, *a, **kw):
            return _BadResp()

    monkeypatch.setattr(httpx, "Client", _BadClient)
    client = SkillIPFSClient(pinata_jwt="invalid-jwt", db_path=tmp_path / "pins.db")
    with pytest.raises(SkillPinError) as exc:
        client.pin(
            b"some-bytes",
            owner_did="did:sisoul:alice", skill_id="test-skill",
        )
    assert "Pinata pin 失败" in str(exc.value)


def test_B3_borrow_when_provider_raises_then_borrow_fails(tmp_path) -> None:
    """encrypted_skill_provider 抛 → SkillBorrowError · 不留 session in DB."""
    from sisoul.friend.skill_borrow import (
        SkillBorrowDB,
        SkillBorrowError,
        request_borrow_skill,
    )

    def broken_provider(o, s):
        raise RuntimeError("network down")

    def decryptor(b):
        raise AssertionError("never reached")

    with pytest.raises(SkillBorrowError) as exc:
        request_borrow_skill(
            owner_did="did:sisoul:alice",
            skill_id="test-skill",
            borrower_did="did:sisoul:bob",
            duration_minutes=30,
            encrypted_skill_provider=broken_provider,
            decrypt_callback=decryptor,
            skip_permission_check=True,
            db_path=tmp_path / "borrow.db",
            tmp_root=tmp_path / "tmp",
        )
    assert "network down" in str(exc.value) or "provider" in str(exc.value).lower()

    with SkillBorrowDB(db_path=tmp_path / "borrow.db") as db:
        assert db.stats()["total"] == 0


# ─────────── C · 30min lifecycle 反向 (mock time advance) ────────────────────


def test_C1_proxy_chat_after_session_destroyed_raises(tmp_path) -> None:
    """end_skill_borrow_session 后 · proxy_skill_chat → SkillBorrowSessionNotFoundError."""
    from sisoul.friend.skill_borrow import (
        SkillBorrowSessionNotFoundError,
        end_skill_borrow_session,
        proxy_skill_chat,
        request_borrow_skill,
    )
    from sisoul.friend.skill_ipfs import register_mock_blob
    from sisoul.friend.skill_package import (
        decrypt_skill_package,
        encrypt_skill_package,
    )

    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    pkg = _make_pkg()

    def provider(o, s):
        blob = encrypt_skill_package(pkg, bob_priv.public_key, alice_priv)
        cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
        register_mock_blob(cid, blob)
        return blob, cid

    def decryptor(b):
        return decrypt_skill_package(b, alice_priv.public_key, bob_priv)

    res = request_borrow_skill(
        owner_did="did:sisoul:alice", skill_id="test-skill",
        borrower_did="did:sisoul:bob",
        duration_minutes=30,
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=tmp_path / "b.db", tmp_root=tmp_path / "tmp",
    )
    sid = res.session.session_id

    end_skill_borrow_session(sid, reason="test", db_path=tmp_path / "b.db",
                             enqueue_onchain=False)

    with pytest.raises(SkillBorrowSessionNotFoundError):
        proxy_skill_chat(
            session_id=sid, prompt="x",
            forwarder=lambda **kw: ("ok", 1, 1),
            db_path=tmp_path / "b.db",
        )


def test_C2_proxy_chat_after_expire_raises_expired(tmp_path) -> None:
    """session 过期但还没 destroy (mock time advance via memory mutate) → SkillBorrowExpiredError."""
    from sisoul.friend.skill_borrow import (
        _ACTIVE_SESSIONS,
        SkillBorrowExpiredError,
        proxy_skill_chat,
        request_borrow_skill,
    )
    from sisoul.friend.skill_ipfs import register_mock_blob
    from sisoul.friend.skill_package import (
        decrypt_skill_package,
        encrypt_skill_package,
    )

    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    pkg = _make_pkg()

    def provider(o, s):
        blob = encrypt_skill_package(pkg, bob_priv.public_key, alice_priv)
        cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
        register_mock_blob(cid, blob)
        return blob, cid

    def decryptor(b):
        return decrypt_skill_package(b, alice_priv.public_key, bob_priv)

    res = request_borrow_skill(
        owner_did="did:sisoul:alice", skill_id="test-skill",
        borrower_did="did:sisoul:bob",
        duration_minutes=30,
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=tmp_path / "b.db", tmp_root=tmp_path / "tmp",
    )
    sid = res.session.session_id

    # mock time advance: 把 _ACTIVE_SESSIONS 中 session_obj 的 expires_at 调到过去
    session_obj, _ = _ACTIVE_SESSIONS[sid]
    session_obj.expires_at = int(time.time()) - 60

    with pytest.raises(SkillBorrowExpiredError):
        proxy_skill_chat(
            session_id=sid, prompt="x",
            forwarder=lambda **kw: ("ok", 1, 1),
            db_path=tmp_path / "b.db",
        )


def test_C3_30min_expire_via_auto_destroy_scheduler_runs_clean(tmp_path) -> None:
    """多 session 同时过期 → scheduler 一把全清, 无 errors."""
    from sisoul.friend.skill_borrow import (
        auto_destroy_expired_sessions,
        get_borrow_session,
        request_borrow_skill,
    )
    from sisoul.friend.skill_ipfs import register_mock_blob
    from sisoul.friend.skill_package import (
        decrypt_skill_package,
        encrypt_skill_package,
    )

    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    pkg = _make_pkg()

    def provider(o, s):
        blob = encrypt_skill_package(pkg, bob_priv.public_key, alice_priv)
        cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
        register_mock_blob(cid, blob)
        return blob, cid

    def decryptor(b):
        return decrypt_skill_package(b, alice_priv.public_key, bob_priv)

    sids = []
    for i in range(3):
        res = request_borrow_skill(
            owner_did="did:sisoul:alice", skill_id="test-skill",
            borrower_did=f"did:sisoul:bob-{i}",
            duration_minutes=30, duration_seconds_override=1,
            encrypted_skill_provider=provider, decrypt_callback=decryptor,
            skip_permission_check=True,
            db_path=tmp_path / "b.db", tmp_root=tmp_path / f"tmp-{i}",
        )
        sids.append(res.session.session_id)

    time.sleep(1.2)
    out = auto_destroy_expired_sessions(
        db_path=tmp_path / "b.db", enqueue_onchain=False,
    )
    assert out["scanned"] == 3
    assert out["destroyed"] == 3
    assert out["errors"] == []
    for sid in sids:
        s = get_borrow_session(sid, db_path=tmp_path / "b.db")
        assert s.status == "destroyed"
        assert s.destroy_reason == "auto-expired"


# ─────────── D · skill chat 用 borrower 错 LLM key → 报错不消耗 ──────────────


def test_D1_chat_with_failing_forwarder_raises_no_partial_charge(tmp_path) -> None:
    """forwarder 抛 (e.g. 401 invalid key) → SkillBorrowError · session 仍 active 可重试."""
    from sisoul.friend.skill_borrow import (
        SkillBorrowError,
        get_borrow_session,
        proxy_skill_chat,
        request_borrow_skill,
    )
    from sisoul.friend.skill_ipfs import register_mock_blob
    from sisoul.friend.skill_package import (
        decrypt_skill_package,
        encrypt_skill_package,
    )

    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    pkg = _make_pkg()

    def provider(o, s):
        blob = encrypt_skill_package(pkg, bob_priv.public_key, alice_priv)
        cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
        register_mock_blob(cid, blob)
        return blob, cid

    def decryptor(b):
        return decrypt_skill_package(b, alice_priv.public_key, bob_priv)

    res = request_borrow_skill(
        owner_did="did:sisoul:alice", skill_id="test-skill",
        borrower_did="did:sisoul:bob",
        duration_minutes=30,
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=tmp_path / "b.db", tmp_root=tmp_path / "tmp",
    )
    sid = res.session.session_id

    def bad_fwd(prompt, model, provider, api_key=None, **kw):
        raise RuntimeError("401 invalid API key")

    with pytest.raises(SkillBorrowError) as exc:
        proxy_skill_chat(
            session_id=sid, prompt="hi",
            forwarder=bad_fwd, llm_api_key="invalid-key",
            db_path=tmp_path / "b.db",
        )
    assert "401" in str(exc.value) or "forwarder" in str(exc.value).lower()

    s = get_borrow_session(sid, db_path=tmp_path / "b.db")
    assert s.status == "active"


# ─────────── E · skill package tamper → fail ────────────────────────────────


def test_E1_tampered_encrypted_blob_fails_mac() -> None:
    """改 encrypted blob 最后字节 → MAC fail → SkillPackageDecryptError."""
    from sisoul.friend.skill_package import (
        SkillPackageDecryptError,
        decrypt_skill_package,
        encrypt_skill_package,
    )

    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    pkg = _make_pkg()
    blob = encrypt_skill_package(pkg, bob_priv.public_key, alice_priv)

    tampered = bytearray(blob)
    tampered[-1] = tampered[-1] ^ 0xFF
    with pytest.raises(SkillPackageDecryptError):
        decrypt_skill_package(bytes(tampered), alice_priv.public_key, bob_priv)


def test_E2_tampered_blob_middle_byte_fails() -> None:
    """改 ciphertext 中间字节 → 同样 MAC fail."""
    from sisoul.friend.skill_package import (
        SkillPackageDecryptError,
        decrypt_skill_package,
        encrypt_skill_package,
    )

    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    pkg = _make_pkg()
    blob = encrypt_skill_package(pkg, bob_priv.public_key, alice_priv)
    tampered = bytearray(blob)
    mid = len(tampered) // 2
    tampered[mid] = (tampered[mid] + 1) & 0xFF
    with pytest.raises(SkillPackageDecryptError):
        decrypt_skill_package(bytes(tampered), alice_priv.public_key, bob_priv)


def test_E3_borrow_with_tampered_provider_raises(tmp_path) -> None:
    """provider 返回篡改 blob → request_borrow_skill 抛 SkillBorrowError (内裹 decrypt fail)."""
    from sisoul.friend.skill_borrow import (
        SkillBorrowError,
        request_borrow_skill,
    )
    from sisoul.friend.skill_ipfs import register_mock_blob
    from sisoul.friend.skill_package import (
        decrypt_skill_package,
        encrypt_skill_package,
    )

    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    pkg = _make_pkg()

    def tampered_provider(o, s):
        blob = bytearray(encrypt_skill_package(pkg, bob_priv.public_key, alice_priv))
        blob[-1] ^= 0xFF
        cid = "mockcid-" + hashlib.sha256(bytes(blob)).hexdigest()[:46]
        register_mock_blob(cid, bytes(blob))
        return bytes(blob), cid

    def decryptor(b):
        return decrypt_skill_package(b, alice_priv.public_key, bob_priv)

    with pytest.raises(SkillBorrowError) as exc:
        request_borrow_skill(
            owner_did="did:sisoul:alice", skill_id="test-skill",
            borrower_did="did:sisoul:bob",
            duration_minutes=30,
            encrypted_skill_provider=tampered_provider,
            decrypt_callback=decryptor,
            skip_permission_check=True,
            db_path=tmp_path / "b.db", tmp_root=tmp_path / "tmp",
        )
    assert "解密失败" in str(exc.value) or "Decrypt" in str(exc.value)


# ─────────── F · 额外: validate edge cases ──────────────────────────────────


def test_F1_skill_invalid_semver_raises() -> None:
    """非 SemVer version → InvalidSkillPackageError."""
    from sisoul.friend.skill_package import (
        InvalidSkillPackageError,
        package_skill,
    )

    with pytest.raises(InvalidSkillPackageError):
        package_skill(
            name="bad-ver", owner_did="x", system_prompt="p",
            version="not-a-semver",
        )


def test_F2_borrow_duration_out_of_range_raises() -> None:
    """duration_minutes 越界 [1, 1440] → SkillBorrowError."""
    from sisoul.friend.skill_borrow import (
        SkillBorrowError,
        request_borrow_skill,
    )

    with pytest.raises(SkillBorrowError):
        request_borrow_skill(
            owner_did="x", skill_id="s", borrower_did="b",
            duration_minutes=0,
            encrypted_skill_provider=lambda o, s: (b"", ""),
            decrypt_callback=lambda b: None,
            skip_permission_check=True,
        )
    with pytest.raises(SkillBorrowError):
        request_borrow_skill(
            owner_did="x", skill_id="s", borrower_did="b",
            duration_minutes=9999,
            encrypted_skill_provider=lambda o, s: (b"", ""),
            decrypt_callback=lambda b: None,
            skip_permission_check=True,
        )


def test_F3_parse_qualified_name_malformed_raises() -> None:
    """qualified_name 无 ":" → InvalidSkillPackageError."""
    from sisoul.friend.skill_package import (
        InvalidSkillPackageError,
        parse_qualified_name,
    )
    with pytest.raises(InvalidSkillPackageError):
        parse_qualified_name("nocolon")
    with pytest.raises(InvalidSkillPackageError):
        parse_qualified_name(":empty-owner")
    with pytest.raises(InvalidSkillPackageError):
        parse_qualified_name("empty-skill:")


def test_F4_end_session_unknown_id_raises() -> None:
    """end_skill_borrow_session 未知 sid → SkillBorrowSessionNotFoundError."""
    from sisoul.friend.skill_borrow import (
        SkillBorrowSessionNotFoundError,
        end_skill_borrow_session,
    )
    with pytest.raises(SkillBorrowSessionNotFoundError):
        end_skill_borrow_session("nonexistent_sid_12345")


def test_F5_end_session_idempotent_returns_destroyed(tmp_path) -> None:
    """end 已 destroyed session 幂等 (不重复 wipe / unpin / ledger)."""
    from sisoul.friend.skill_borrow import (
        end_skill_borrow_session,
        request_borrow_skill,
    )
    from sisoul.friend.skill_ipfs import register_mock_blob
    from sisoul.friend.skill_package import (
        decrypt_skill_package,
        encrypt_skill_package,
    )

    alice_priv = PrivateKey.generate()
    bob_priv = PrivateKey.generate()
    pkg = _make_pkg()

    def provider(o, s):
        blob = encrypt_skill_package(pkg, bob_priv.public_key, alice_priv)
        cid = "mockcid-" + hashlib.sha256(blob).hexdigest()[:46]
        register_mock_blob(cid, blob)
        return blob, cid

    def decryptor(b):
        return decrypt_skill_package(b, alice_priv.public_key, bob_priv)

    res = request_borrow_skill(
        owner_did="did:sisoul:alice", skill_id="test-skill",
        borrower_did="did:sisoul:bob",
        duration_minutes=30,
        encrypted_skill_provider=provider, decrypt_callback=decryptor,
        skip_permission_check=True,
        db_path=tmp_path / "b.db", tmp_root=tmp_path / "tmp",
    )
    sid = res.session.session_id
    s1 = end_skill_borrow_session(sid, reason="first", db_path=tmp_path / "b.db",
                                  enqueue_onchain=False)
    s2 = end_skill_borrow_session(sid, reason="second", db_path=tmp_path / "b.db",
                                  enqueue_onchain=False)
    assert s1.status == "destroyed"
    assert s2.status == "destroyed"
    assert s2.destroy_reason == "first"
