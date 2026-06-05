"""tests for /v1/push/* daemon routes (mobile push device registration)."""
from __future__ import annotations
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SISOUL_VAULT", str(tmp_path))
    from sisoul.daemon_routes.push import push_router

    app = FastAPI()
    app.include_router(push_router)
    return TestClient(app), tmp_path


def test_register_new_ios_device(client):
    tc, vault = client
    r = tc.post("/v1/push/register", json={
        "token": "abcd1234efgh5678ijkl9012mnop3456",
        "platform": "ios",
        "did_key": "did:key:z6MkAlice",
    })
    assert r.status_code == 200
    j = r.json()
    assert j["success"] is True
    assert j["is_new"] is True
    assert j["device"]["platform"] == "ios"
    assert j["device"]["token"] == "abcd1234efgh5678ijkl9012mnop3456"
    # persisted
    devices_file = vault / "push_devices.json"
    assert devices_file.exists()
    rows = json.loads(devices_file.read_text())
    assert len(rows) == 1


def test_register_android_device(client):
    tc, _ = client
    r = tc.post("/v1/push/register", json={
        "token": "fcm-token-xyzzy123456789",
        "platform": "android",
        "did_key": "did:key:z6MkBob",
    })
    assert r.status_code == 200
    assert r.json()["device"]["platform"] == "android"


def test_register_idempotent_same_token(client):
    tc, _ = client
    payload = {
        "token": "duplicate-token-test",
        "platform": "ios",
        "did_key": "did:key:z6MkA",
    }
    r1 = tc.post("/v1/push/register", json=payload)
    r2 = tc.post("/v1/push/register", json=payload)
    assert r1.json()["is_new"] is True
    assert r2.json()["is_new"] is False  # same token → update, not duplicate


def test_register_rejects_invalid_platform(client):
    tc, _ = client
    r = tc.post("/v1/push/register", json={
        "token": "valid-token-12345",
        "platform": "blackberry",  # not in allowed Literal
    })
    assert r.status_code == 422


def test_register_rejects_short_token(client):
    tc, _ = client
    r = tc.post("/v1/push/register", json={
        "token": "x",  # too short (min_length=8)
        "platform": "ios",
    })
    assert r.status_code == 422


def test_list_devices_empty(client):
    tc, _ = client
    r = tc.get("/v1/push/devices")
    assert r.status_code == 200
    assert r.json() == {"devices": [], "count": 0}


def test_list_devices_returns_registered(client):
    tc, _ = client
    tc.post("/v1/push/register", json={"token": "tok1234567890", "platform": "ios"})
    tc.post("/v1/push/register", json={"token": "tok9876543210", "platform": "android"})
    r = tc.get("/v1/push/devices")
    assert r.json()["count"] == 2


def test_list_devices_filter_by_did(client):
    tc, _ = client
    tc.post("/v1/push/register", json={"token": "tok-alice-1", "platform": "ios", "did_key": "did:key:z6MkAlice"})
    tc.post("/v1/push/register", json={"token": "tok-bob-1", "platform": "android", "did_key": "did:key:z6MkBob"})
    r = tc.get("/v1/push/devices?did_key=did:key:z6MkAlice")
    assert r.json()["count"] == 1
    assert r.json()["devices"][0]["token"] == "tok-alice-1"


def test_unregister_device(client):
    tc, vault = client
    tc.post("/v1/push/register", json={"token": "to-be-removed-12345", "platform": "ios"})
    r = tc.delete("/v1/push/devices/to-be-removed-12345")
    assert r.status_code == 200
    assert r.json()["success"] is True
    # gone from list
    assert tc.get("/v1/push/devices").json()["count"] == 0


def test_unregister_missing_404(client):
    tc, _ = client
    r = tc.delete("/v1/push/devices/never-existed-token")
    assert r.status_code == 404


def test_push_test_skeleton_no_apns(client):
    tc, _ = client
    tc.post("/v1/push/register", json={"token": "ios-test-tok-1234", "platform": "ios", "did_key": "did:key:z6MkA"})
    tc.post("/v1/push/register", json={"token": "and-test-tok-1234", "platform": "android", "did_key": "did:key:z6MkA"})
    r = tc.post("/v1/push/test", json={"title": "hi", "body": "hello", "target_did": "did:key:z6MkA"})
    assert r.status_code == 200
    j = r.json()
    assert j["sent"] == 0  # skeleton
    assert len(j["devices_targeted"]) == 2
    assert "APNs" in j["note"] or "FCM" in j["note"]


def test_push_test_no_target_includes_all(client):
    tc, _ = client
    tc.post("/v1/push/register", json={"token": "tok-anyone-1", "platform": "ios"})
    tc.post("/v1/push/register", json={"token": "tok-anyone-2", "platform": "android"})
    r = tc.post("/v1/push/test", json={"title": "broadcast", "body": "hi all"})
    assert r.json()["sent"] == 0
    assert len(r.json()["devices_targeted"]) == 2


def test_register_persists_across_requests(client):
    tc, vault = client
    tc.post("/v1/push/register", json={"token": "persist-test-12345", "platform": "ios"})
    # Manually re-read file
    f = vault / "push_devices.json"
    rows = json.loads(f.read_text())
    assert len(rows) == 1
    assert rows[0]["token"] == "persist-test-12345"
