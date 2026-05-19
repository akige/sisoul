"""tests · daemon_routes.pwa (Phase 2 W23-W28 dev-C).

覆盖:
- preferences/list 空 + 有数据
- preferences/{id} 200 + 404
- goals/list 进度解析 (0.0, 0.5, '50%', done)
- goals/{id} 200 + 404
- chat-history/list 空 + 多日期 + 日期过滤 + limit
- chat-history/{date}/{session} 200 + 404 + 非法 date 400
- status/full 全字段
- path traversal 防御 (../) 400
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.pwa import create_router
from sisoul.vault.frontmatter import dump_frontmatter
from sisoul.vault.storage import VaultPaths


@pytest.fixture()
def vault_root(tmp_path: Path) -> Path:
    """tmp_path 下建一个 vault, 预置 preferences / goals / chat-history 数据."""
    root = tmp_path / "vault"
    vp = VaultPaths(root)
    vp.ensure_dirs()

    # preferences
    (vp.preferences_dir / "coffee.md").write_text(
        dump_frontmatter(
            {"title": "Coffee taste", "tags": ["food", "morning"], "updated": "2026-05-18"},
            "I prefer light roast pour-over.",
        ),
        encoding="utf-8",
    )
    (vp.preferences_dir / "code-style.md").write_text(
        dump_frontmatter(
            {"title": "Code style", "tags": ["dev"]},
            "Use 4-space indent in Python.",
        ),
        encoding="utf-8",
    )
    # 没 frontmatter 的也要能读
    (vp.preferences_dir / "raw.md").write_text("naked markdown body", encoding="utf-8")

    # goals
    (vp.goals_dir / "ship-sisoul-v1.md").write_text(
        dump_frontmatter(
            {
                "title": "Ship sisoul v1.0",
                "progress": 0.35,
                "status": "active",
                "target_date": "2026-12-31",
            },
            "ship sisoul v1.0 to v1.0-internal tag.",
        ),
        encoding="utf-8",
    )
    (vp.goals_dir / "learn-rust.md").write_text(
        dump_frontmatter(
            {"title": "Learn Rust", "progress": "50%", "status": "active"},
            "do rustlings.",
        ),
        encoding="utf-8",
    )
    (vp.goals_dir / "buy-coffee-machine.md").write_text(
        dump_frontmatter(
            {"title": "Buy machine", "status": "done"},
            "done",
        ),
        encoding="utf-8",
    )

    # chat-history (2 days)
    d1 = vp.chat_history_dir / "2026-05-17"
    d2 = vp.chat_history_dir / "2026-05-18"
    d1.mkdir(parents=True, exist_ok=True)
    d2.mkdir(parents=True, exist_ok=True)
    (d1 / "session-001.md").write_text(
        dump_frontmatter(
            {"title": "Morning chat", "message_count": 5},
            "## user\nhi\n## assistant\nhello\n",
        ),
        encoding="utf-8",
    )
    (d2 / "session-002.md").write_text(
        dump_frontmatter({"title": "Evening chat"}, "## user\nfoo\n## assistant\nbar\n"),
        encoding="utf-8",
    )

    # dna.json (mock dev-A 产出)
    vp.dna.write_text(
        json.dumps(
            {
                "did": "did:ethr:0xabc",
                "handle": "alice.sisoul.eth",
                "seed_fingerprint": "abcd1234",
                "created": "2026-05-18T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    return root


@pytest.fixture()
def client(vault_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(create_router())
    c = TestClient(app)
    c.vault_root = str(vault_root)  # type: ignore[attr-defined]
    return c


def _vq(client: TestClient) -> dict[str, str]:
    """生成 ?vault=<path> query dict."""
    return {"vault": client.vault_root}  # type: ignore[attr-defined]


# ──────────────────────────────────────────────────────────
# preferences
# ──────────────────────────────────────────────────────────


def test_preferences_list_returns_three(client: TestClient) -> None:
    r = client.get("/sisoul/preferences/list", params=_vq(client))
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    ids = sorted(item["id"] for item in body)
    assert ids == ["code-style", "coffee", "raw"]


def test_preferences_list_includes_metadata(client: TestClient) -> None:
    r = client.get("/sisoul/preferences/list", params=_vq(client))
    coffee = next(x for x in r.json() if x["id"] == "coffee")
    assert coffee["title"] == "Coffee taste"
    assert "food" in coffee["tags"]
    assert coffee["updated"] == "2026-05-18"


def test_preferences_list_handles_no_frontmatter(client: TestClient) -> None:
    """没 frontmatter 也要能列出 + title fallback id."""
    r = client.get("/sisoul/preferences/list", params=_vq(client))
    raw = next(x for x in r.json() if x["id"] == "raw")
    assert raw["title"] == "raw"
    assert raw["tags"] == []


def test_preferences_get_returns_full(client: TestClient) -> None:
    r = client.get("/sisoul/preferences/coffee", params=_vq(client))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "coffee"
    assert body["frontmatter"]["title"] == "Coffee taste"
    assert "light roast" in body["body"]
    assert body["size_bytes"] > 0


def test_preferences_get_404(client: TestClient) -> None:
    r = client.get("/sisoul/preferences/missing", params=_vq(client))
    assert r.status_code == 404


def test_preferences_path_traversal_blocked(client: TestClient) -> None:
    r = client.get("/sisoul/preferences/..%2Fetc%2Fpasswd", params=_vq(client))
    assert r.status_code in (400, 404)
    # 不应 5xx, 不应泄露文件
    if r.status_code == 200:
        pytest.fail("path traversal should be blocked")


# ──────────────────────────────────────────────────────────
# goals
# ──────────────────────────────────────────────────────────


def test_goals_list_progress_parsing(client: TestClient) -> None:
    r = client.get("/sisoul/goals/list", params=_vq(client))
    assert r.status_code == 200
    body = {x["id"]: x for x in r.json()}
    assert abs(body["ship-sisoul-v1"]["progress"] - 0.35) < 1e-6
    assert abs(body["learn-rust"]["progress"] - 0.5) < 1e-6
    assert body["buy-coffee-machine"]["progress"] == 1.0
    assert body["buy-coffee-machine"]["status"] == "done"


def test_goals_get_returns_progress(client: TestClient) -> None:
    r = client.get("/sisoul/goals/ship-sisoul-v1", params=_vq(client))
    assert r.status_code == 200
    body = r.json()
    assert body["frontmatter"]["target_date"] == "2026-12-31"
    assert abs(body["progress"] - 0.35) < 1e-6


def test_goals_get_404(client: TestClient) -> None:
    r = client.get("/sisoul/goals/nope", params=_vq(client))
    assert r.status_code == 404


# ──────────────────────────────────────────────────────────
# chat history
# ──────────────────────────────────────────────────────────


def test_chat_history_list_all(client: TestClient) -> None:
    r = client.get("/sisoul/chat-history/list", params=_vq(client))
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    # 倒序 (新日期在前)
    assert body[0]["date"] == "2026-05-18"
    assert body[1]["date"] == "2026-05-17"


def test_chat_history_list_filter_by_date(client: TestClient) -> None:
    r = client.get(
        "/sisoul/chat-history/list",
        params={**_vq(client), "date": "2026-05-17"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["session_id"] == "session-001"
    assert body[0]["message_count"] == 5  # frontmatter 显式给的


def test_chat_history_list_limit(client: TestClient) -> None:
    r = client.get(
        "/sisoul/chat-history/list",
        params={**_vq(client), "limit": 1},
    )
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_chat_history_list_bad_date_400(client: TestClient) -> None:
    r = client.get(
        "/sisoul/chat-history/list",
        params={**_vq(client), "date": "not-a-date"},
    )
    assert r.status_code == 400


def test_chat_history_get_session(client: TestClient) -> None:
    r = client.get(
        "/sisoul/chat-history/2026-05-18/session-002",
        params=_vq(client),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["frontmatter"]["title"] == "Evening chat"
    assert "foo" in body["body"]


def test_chat_history_get_404(client: TestClient) -> None:
    r = client.get(
        "/sisoul/chat-history/2026-05-18/missing",
        params=_vq(client),
    )
    assert r.status_code == 404


def test_chat_history_get_bad_date_400(client: TestClient) -> None:
    r = client.get(
        "/sisoul/chat-history/badformat/session-001",
        params=_vq(client),
    )
    assert r.status_code == 400


# ──────────────────────────────────────────────────────────
# status/full
# ──────────────────────────────────────────────────────────


def test_status_full_basic(client: TestClient) -> None:
    r = client.get("/sisoul/status/full", params=_vq(client))
    assert r.status_code == 200
    body = r.json()
    assert body["vault_exists"] is True
    assert body["vault_size_bytes"] > 0
    assert body["counts"]["preferences"] == 3
    assert body["counts"]["goals"] == 3
    assert body["counts"]["chat_sessions"] == 2
    assert body["daemon"]["port"] == 9876
    assert body["daemon"]["version"]
    assert body["identity"]["did"] == "did:ethr:0xabc"
    assert body["identity"]["has_seed"] is True


def test_status_full_no_vault(tmp_path: Path) -> None:
    """vault 不存在时不应 5xx, 应返 vault_exists=False + 0 计数."""
    app = FastAPI()
    app.include_router(create_router())
    c = TestClient(app)
    r = c.get("/sisoul/status/full", params={"vault": str(tmp_path / "nope")})
    assert r.status_code == 200
    body = r.json()
    assert body["vault_exists"] is False
    assert body["vault_size_bytes"] == 0
    assert body["counts"]["preferences"] == 0


def test_status_full_llm_section_present(client: TestClient) -> None:
    r = client.get("/sisoul/status/full", params=_vq(client))
    body = r.json()
    assert "llm" in body
    assert "primary_provider" in body["llm"]
    assert "available_providers" in body["llm"]
    assert "has_any_key" in body["llm"]


# ──────────────────────────────────────────────────────────
# vault env var resolution
# ──────────────────────────────────────────────────────────


def test_vault_env_var_picked_up(vault_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SISOUL_VAULT_ROOT", str(vault_root))
    app = FastAPI()
    app.include_router(create_router())
    c = TestClient(app)
    r = c.get("/sisoul/preferences/list")  # 不传 ?vault, 走 env
    assert r.status_code == 200
    assert len(r.json()) == 3


# ──────────────────────────────────────────────────────────
# 反向验证 (broken vault)
# ──────────────────────────────────────────────────────────


def test_broken_dna_json_does_not_crash(vault_root: Path) -> None:
    """dna.json corrupt → status/full 仍 200, identity 段 error 标记."""
    (vault_root / "dna.json").write_text("{not json", encoding="utf-8")
    app = FastAPI()
    app.include_router(create_router())
    c = TestClient(app)
    r = c.get("/sisoul/status/full", params={"vault": str(vault_root)})
    assert r.status_code == 200
    body = r.json()
    assert body["identity"]["did"] is None
    assert "error" in body["identity"]


def test_preferences_with_corrupt_frontmatter(vault_root: Path) -> None:
    """偏好 .md frontmatter 坏掉 → 列表里仍出现, 不 5xx."""
    bad = vault_root / "preferences" / "bad.md"
    bad.write_text("---\n: : : invalid yaml\n---\nbody", encoding="utf-8")
    app = FastAPI()
    app.include_router(create_router())
    c = TestClient(app)
    r = c.get("/sisoul/preferences/list", params={"vault": str(vault_root)})
    assert r.status_code == 200
    ids = [x["id"] for x in r.json()]
    assert "bad" in ids
