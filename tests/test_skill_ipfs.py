"""tests for sisoul.friend.skill_ipfs (波 6 dev-A).

覆盖:
- SkillIPFSClient.pin (mock fallback no jwt + real Pinata HTTP mock via monkeypatch)
- SkillPinRecord 数据
- SkillPinDB CRUD + list_active + list_expired_active + stats
- unpin (mock cid + real Pinata DELETE mock)
- unpin_expired_skills scheduler 入口
- 24h expiry default
- register_mock_blob + fetch round-trip
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from sisoul.friend.skill_ipfs import (
    DEFAULT_UNPIN_SCAN_INTERVAL_SEC,
    SkillFetchError,
    SkillIPFSClient,
    SkillPinDB,
    SkillPinError,
    SkillPinRecord,
    SkillUnpinError,
    clear_mock_blob_cache,
    fetch_skill_from_ipfs,
    pin_skill_to_ipfs,
    register_mock_blob,
    unpin_expired_skills,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "skill_pins.db"


@pytest.fixture(autouse=True)
def _clear_mock_cache():
    clear_mock_blob_cache()
    yield
    clear_mock_blob_cache()


# ── pin: mock fallback (no jwt) ────────────────────────────────────────────


def test_pin_mock_no_jwt(db_path, monkeypatch):
    monkeypatch.delenv("PINATA_JWT", raising=False)
    client = SkillIPFSClient(pinata_jwt=None, db_path=db_path)
    blob = b"test-encrypted-skill-blob"
    rec = client.pin(
        blob,
        owner_did="bob",
        skill_id="solidity-expert",
        expiry_hours=24,
    )
    assert rec.cid.startswith("mockcid-")
    assert rec.owner_did == "bob"
    assert rec.skill_id == "solidity-expert"
    assert rec.size_bytes == len(blob)
    assert rec.pinata_pinned is False
    assert rec.unpinned is False
    assert (rec.expires_at - rec.pinned_at) == 24 * 3600


def test_pin_writes_db(db_path, monkeypatch):
    monkeypatch.delenv("PINATA_JWT", raising=False)
    client = SkillIPFSClient(db_path=db_path)
    rec = client.pin(b"x", owner_did="bob", skill_id="t")
    with SkillPinDB(db_path=db_path) as db:
        got = db.get(rec.cid)
    assert got is not None
    assert got.skill_id == "t"


def test_pin_missing_fields(db_path):
    client = SkillIPFSClient(db_path=db_path)
    with pytest.raises(SkillPinError):
        client.pin(b"x", owner_did="", skill_id="t")
    with pytest.raises(SkillPinError):
        client.pin(b"x", owner_did="bob", skill_id="")
    with pytest.raises(SkillPinError):
        client.pin("not-bytes", owner_did="bob", skill_id="t")  # type: ignore[arg-type]


# ── pin: real Pinata via httpx mock ────────────────────────────────────────


def test_pin_real_pinata_mock(db_path, monkeypatch):
    """模拟 Pinata API 返 IpfsHash, 走 'real' 分支."""
    import httpx

    captured = {}

    class _MockResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"IpfsHash": "QmRealCID12345"}

    class _MockClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, url, headers=None, files=None, data=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["filename"] = files["file"][0] if files else None
            captured["metadata"] = data["pinataMetadata"] if data else None
            return _MockResp()

    monkeypatch.setattr(httpx, "Client", _MockClient)

    client = SkillIPFSClient(pinata_jwt="fake-jwt", db_path=db_path)
    rec = client.pin(b"blob", owner_did="bob", skill_id="t", expiry_hours=12)
    assert rec.cid == "QmRealCID12345"
    assert rec.pinata_pinned is True
    assert (rec.expires_at - rec.pinned_at) == 12 * 3600
    assert "Bearer fake-jwt" in captured["headers"]["Authorization"]
    import json
    md = json.loads(captured["metadata"])
    assert md["keyvalues"]["owner_did"] == "bob"
    assert md["keyvalues"]["skill_id"] == "t"


def test_pin_real_pinata_http_error(db_path, monkeypatch):
    import httpx

    class _MockClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, *a, **kw):
            raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx, "Client", _MockClient)
    client = SkillIPFSClient(pinata_jwt="fake", db_path=db_path)
    with pytest.raises(SkillPinError, match="Pinata pin 失败"):
        client.pin(b"x", owner_did="bob", skill_id="t")


# ── fetch ──────────────────────────────────────────────────────────────────


def test_fetch_mockcid(db_path):
    blob = b"hello-world"
    register_mock_blob("mockcid-deadbeef", blob)
    client = SkillIPFSClient(db_path=db_path)
    got = client.fetch("mockcid-deadbeef")
    assert got == blob


def test_fetch_mockcid_not_registered(db_path):
    client = SkillIPFSClient(db_path=db_path)
    with pytest.raises(SkillFetchError, match="不在"):
        client.fetch("mockcid-nothere")


def test_fetch_real_gateway_mock(db_path, monkeypatch):
    import httpx

    class _MockResp:
        status_code = 200
        content = b"fetched-content"
        def raise_for_status(self): pass

    class _MockClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url):
            return _MockResp()

    monkeypatch.setattr(httpx, "Client", _MockClient)
    client = SkillIPFSClient(db_path=db_path)
    got = client.fetch("QmRealCID")
    assert got == b"fetched-content"


def test_fetch_all_gateways_fail(db_path, monkeypatch):
    import httpx

    class _MockClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "Client", _MockClient)
    client = SkillIPFSClient(db_path=db_path)
    with pytest.raises(SkillFetchError, match="所有 gateway"):
        client.fetch("QmRealCID")


# ── unpin ──────────────────────────────────────────────────────────────────


def test_unpin_mock(db_path, monkeypatch):
    monkeypatch.delenv("PINATA_JWT", raising=False)
    client = SkillIPFSClient(db_path=db_path)
    rec = client.pin(b"x", owner_did="bob", skill_id="t")
    ok = client.unpin(rec.cid)
    assert ok is True
    with SkillPinDB(db_path=db_path) as db:
        got = db.get(rec.cid)
    assert got.unpinned is True
    assert got.unpinned_at is not None


def test_unpin_idempotent(db_path, monkeypatch):
    monkeypatch.delenv("PINATA_JWT", raising=False)
    client = SkillIPFSClient(db_path=db_path)
    rec = client.pin(b"x", owner_did="bob", skill_id="t")
    assert client.unpin(rec.cid) is True
    assert client.unpin(rec.cid) is True


def test_unpin_real_pinata_mock(db_path, monkeypatch):
    import httpx

    captured = {}

    class _MockResp:
        status_code = 200
        def raise_for_status(self): pass

    class _MockClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, url, headers=None, files=None, data=None):
            class P:
                status_code = 200
                def raise_for_status(self): pass
                def json(self): return {"IpfsHash": "QmCidX"}
            return P()
        def delete(self, url, headers=None):
            captured["delete_url"] = url
            return _MockResp()

    monkeypatch.setattr(httpx, "Client", _MockClient)
    client = SkillIPFSClient(pinata_jwt="fake", db_path=db_path)
    rec = client.pin(b"x", owner_did="bob", skill_id="t")
    assert client.unpin(rec.cid) is True
    assert "QmCidX" in captured["delete_url"]


def test_unpin_404_ignored(db_path, monkeypatch):
    import httpx

    class _MockResp:
        status_code = 404
        def raise_for_status(self): pass

    class _MockClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, *a, **kw):
            class P:
                status_code = 200
                def raise_for_status(self): pass
                def json(self): return {"IpfsHash": "QmCidY"}
            return P()
        def delete(self, *a, **kw):
            return _MockResp()

    monkeypatch.setattr(httpx, "Client", _MockClient)
    client = SkillIPFSClient(pinata_jwt="fake", db_path=db_path)
    rec = client.pin(b"x", owner_did="bob", skill_id="t")
    assert client.unpin(rec.cid, ignore_404=True) is True


# ── DB CRUD ────────────────────────────────────────────────────────────────


def test_db_list_active_filter(db_path):
    with SkillPinDB(db_path=db_path) as db:
        now = int(time.time())
        for i in range(5):
            db.upsert(SkillPinRecord(
                cid=f"mockcid-{i:046d}",
                owner_did="bob" if i < 3 else "alice",
                skill_id=f"s{i}",
                pinned_at=now,
                expires_at=now + 3600,
            ))
        all_active = db.list_active()
        assert len(all_active) == 5
        bob_only = db.list_active(owner_did="bob")
        assert len(bob_only) == 3


def test_db_list_expired_active(db_path):
    with SkillPinDB(db_path=db_path) as db:
        now = int(time.time())
        db.upsert(SkillPinRecord(
            cid="mockcid-a", owner_did="b", skill_id="s",
            pinned_at=now - 7200, expires_at=now - 3600,
        ))
        db.upsert(SkillPinRecord(
            cid="mockcid-b", owner_did="b", skill_id="s",
            pinned_at=now - 7200, expires_at=now - 100,
        ))
        db.upsert(SkillPinRecord(
            cid="mockcid-c", owner_did="b", skill_id="s",
            pinned_at=now, expires_at=now + 3600,
        ))
        expired = db.list_expired_active(now=now)
        cids = sorted(r.cid for r in expired)
        assert cids == ["mockcid-a", "mockcid-b"]


def test_db_stats(db_path):
    with SkillPinDB(db_path=db_path) as db:
        now = int(time.time())
        for i in range(3):
            db.upsert(SkillPinRecord(
                cid=f"mockcid-{i}", owner_did="b", skill_id="s",
                pinned_at=now, expires_at=now + 3600,
            ))
        db.mark_unpinned("mockcid-0")
        st = db.stats()
        assert st["total"] == 3
        assert st["active"] == 2
        assert st["unpinned"] == 1


def test_record_is_expired():
    now = int(time.time())
    r1 = SkillPinRecord(cid="a", owner_did="b", skill_id="s",
                        pinned_at=now - 7200, expires_at=now - 100)
    r2 = SkillPinRecord(cid="b", owner_did="b", skill_id="s",
                        pinned_at=now, expires_at=now + 3600)
    assert r1.is_expired() is True
    assert r2.is_expired() is False


# ── unpin_expired_skills scheduler ─────────────────────────────────────────


def test_unpin_expired_skills_scheduler(db_path, monkeypatch):
    monkeypatch.delenv("PINATA_JWT", raising=False)
    with SkillPinDB(db_path=db_path) as db:
        now = int(time.time())
        for i in range(2):
            db.upsert(SkillPinRecord(
                cid=f"mockcid-expired-{i:040d}", owner_did="bob", skill_id=f"s{i}",
                pinned_at=now - 7200, expires_at=now - 100,
            ))
        db.upsert(SkillPinRecord(
            cid="mockcid-active000", owner_did="bob", skill_id="s2",
            pinned_at=now, expires_at=now + 3600,
        ))

    res = unpin_expired_skills(db_path=db_path)
    assert res["scanned"] == 2
    assert res["unpinned"] == 2
    assert res["errors"] == []


def test_unpin_expired_skills_empty(db_path):
    res = unpin_expired_skills(db_path=db_path)
    assert res["scanned"] == 0
    assert res["unpinned"] == 0


# ── module helpers ─────────────────────────────────────────────────────────


def test_pin_skill_to_ipfs_helper(db_path, monkeypatch):
    monkeypatch.delenv("PINATA_JWT", raising=False)
    rec = pin_skill_to_ipfs(
        b"blob",
        owner_did="bob",
        skill_id="t",
        expiry_hours=1,
        db_path=db_path,
    )
    assert rec.cid.startswith("mockcid-")
    assert (rec.expires_at - rec.pinned_at) == 3600


def test_fetch_skill_from_ipfs_helper(db_path):
    register_mock_blob("mockcid-helper", b"hello")
    got = fetch_skill_from_ipfs("mockcid-helper", db_path=db_path)
    assert got == b"hello"


# ── 24h default ───────────────────────────────────────────────────────────


def test_default_24h_expiry(db_path, monkeypatch):
    monkeypatch.delenv("PINATA_JWT", raising=False)
    client = SkillIPFSClient(db_path=db_path)
    rec = client.pin(b"x", owner_did="bob", skill_id="t")
    assert (rec.expires_at - rec.pinned_at) == 24 * 3600


def test_scan_interval_constant():
    assert DEFAULT_UNPIN_SCAN_INTERVAL_SEC == 5 * 60
