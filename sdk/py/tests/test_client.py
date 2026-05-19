"""pytest - SisoulClient 全面用例 (httpx MockTransport)."""

from __future__ import annotations

import json

import httpx
import pytest

from sisoul_client import (
    AuthError,
    DaemonError,
    NetworkError,
    SisoulClient,
    SkillCreateRequest,
    TimeoutError,
)


def make_client(handler) -> SisoulClient:
    transport = httpx.MockTransport(handler)
    return SisoulClient(base_url="http://daemon.test/sisoul", transport=transport)


# ─── construction ─────────────────────────────────────────────────────────
def test_client_strips_trailing_slash():
    c = make_client(lambda r: httpx.Response(200, json={}))
    assert c.base_url == "http://daemon.test/sisoul"


def test_client_exposes_subapis():
    c = make_client(lambda r: httpx.Response(200, json={}))
    assert c.vault and c.goals and c.friends and c.skills and c.attest


# ─── vault ────────────────────────────────────────────────────────────────
def test_vault_list():
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/sisoul/preferences/list"
        return httpx.Response(
            200,
            json={"items": [{"key": "theme", "value": "dark", "updated_at": "2026"}]},
        )

    c = make_client(h)
    items = c.vault.list()
    assert len(items) == 1
    assert items[0].key == "theme"


def test_vault_get_encodes_key():
    captured = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["key"] = req.url.params.get("key")
        return httpx.Response(200, json={"key": "x", "value": "v"})

    c = make_client(h)
    c.vault.get("hello world")
    assert captured["key"] == "hello world"


def test_vault_set_posts_body():
    captured = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True})

    c = make_client(h)
    c.vault.set("a", "b")
    assert captured["body"] == {"key": "a", "value": "b"}


def test_vault_rejects_empty_key():
    c = make_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError, match="key required"):
        c.vault.set("", "x")


# ─── goals ────────────────────────────────────────────────────────────────
def test_goals_list():
    c = make_client(
        lambda r: httpx.Response(200, json={"goals": [{"id": "g1", "title": "T", "progress": 0.5}]})
    )
    g = c.goals.list()
    assert g[0].id == "g1"


def test_goals_add_requires_title():
    c = make_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError, match="title required"):
        c.goals.add({"title": ""})


def test_goals_bump_progress_clamps_upper():
    captured = {}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/goals/list"):
            return httpx.Response(
                200, json={"goals": [{"id": "g1", "title": "T", "progress": 0.9}]}
            )
        if req.url.path.endswith("/goals/update"):
            captured["body"] = json.loads(req.content)
            return httpx.Response(200, json={"id": "g1", "title": "T", "progress": 1.0})
        return httpx.Response(404)

    c = make_client(h)
    c.goals.bump_progress("g1", 0.5)
    assert captured["body"]["progress"] == 1.0


# ─── friends ──────────────────────────────────────────────────────────────
def test_friends_strong_ties_filters():
    c = make_client(
        lambda r: httpx.Response(
            200,
            json={
                "friends": [
                    {"did": "did:1", "trust_level": 0.8, "connected_at": "x"},
                    {"did": "did:2", "trust_level": 0.3, "connected_at": "x"},
                ]
            },
        )
    )
    strong = c.friends.strong_ties(0.7)
    assert len(strong) == 1 and strong[0].did == "did:1"


def test_friends_lend_requires_fields():
    c = make_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError, match="friend_did required"):
        c.friends.lend({"friend_did": "", "resource_type": "skill", "resource_id": "x"})


# ─── skills ───────────────────────────────────────────────────────────────
def test_skills_list_uses_absolute_path():
    captured = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["path"] = req.url.path
        return httpx.Response(
            200, json={"own_did": "did:1", "owned": [], "available_to_borrow": []}
        )

    c = make_client(h)
    c.skills.list()
    # 必须直接打 /sisoul/skill/list, 不能拼成 /sisoul/sisoul/skill/list
    assert captured["path"] == "/sisoul/skill/list"


def test_skills_create_rejects_empty_system_prompt():
    c = make_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError, match="system_prompt required"):
        c.skills.create(SkillCreateRequest(name="x", description="", system_prompt=""))


def test_skills_active_sessions_filter():
    c = make_client(
        lambda r: httpx.Response(
            200,
            json={
                "own_did": "did:1",
                "sessions": [
                    {
                        "session_id": "s1",
                        "skill_id": "k",
                        "skill_name": "n",
                        "qualified_name": "q",
                        "owner_did": "o",
                        "borrower_did": "b",
                        "status": "active",
                        "started_at": 0,
                        "expires_at": 1,
                        "proxy_endpoint": "e",
                        "wiped": False,
                    },
                    {
                        "session_id": "s2",
                        "skill_id": "k",
                        "skill_name": "n",
                        "qualified_name": "q",
                        "owner_did": "o",
                        "borrower_did": "b",
                        "status": "expired",
                        "started_at": 0,
                        "expires_at": 1,
                        "proxy_endpoint": "e",
                        "wiped": False,
                    },
                ],
            },
        )
    )
    act = c.skills.active_sessions()
    assert len(act) == 1 and act[0].session_id == "s1"


# ─── attest ───────────────────────────────────────────────────────────────
def test_attest_by_schema_filters():
    c = make_client(
        lambda r: httpx.Response(
            200,
            json={
                "history": [
                    {"uid": "1", "schema": "skill", "timestamp": 100, "chain": "op"},
                    {"uid": "2", "schema": "kyc", "timestamp": 200, "chain": "op"},
                ]
            },
        )
    )
    out = c.attest.by_schema("kyc")
    assert len(out) == 1 and out[0].uid == "2"


def test_attest_since():
    c = make_client(
        lambda r: httpx.Response(
            200,
            json={
                "history": [
                    {"uid": "1", "schema": "x", "timestamp": 100, "chain": "c"},
                    {"uid": "2", "schema": "x", "timestamp": 200, "chain": "c"},
                ]
            },
        )
    )
    out = c.attest.since(150)
    assert len(out) == 1 and out[0].uid == "2"


# ─── error handling ───────────────────────────────────────────────────────
def test_404_raises_daemon_error():
    c = make_client(lambda r: httpx.Response(404, text="nope"))
    with pytest.raises(DaemonError) as exc:
        c.vault.list()
    assert exc.value.status == 404


def test_401_raises_auth_error():
    c = make_client(lambda r: httpx.Response(401, text="unauth"))
    with pytest.raises(AuthError):
        c.vault.list()


def test_network_failure():
    def h(req):
        raise httpx.ConnectError("conn refused")

    c = make_client(h)
    with pytest.raises(NetworkError):
        c.vault.list()


def test_timeout():
    def h(req):
        raise httpx.ReadTimeout("timed out")

    c = make_client(h)
    with pytest.raises(TimeoutError):
        c.vault.list()
