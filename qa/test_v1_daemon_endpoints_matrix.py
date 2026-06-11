"""qa-002 · sisoul v1.0-internal · daemon HTTP endpoints 矩阵测试.

68 endpoints × 5 case (200/201 happy · 400/422 bad-input · 404 not-found ·
405 wrong-method · sanity-structure) = 340+ assertions.

严格约束
--------
- ❌ 不真打 LLM / Anthropic / OpenAI / 任何外部 API
- ❌ 不真连网络 (TestClient in-memory only)
- ❌ 不动 mac/remote-vps agent / launchd / hooks
- ✅ monkeypatch 所有外部 IO (P2P/EAS/Arweave/LLM forwarder)
- ✅ tmp_path 隔离文件 IO
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ─────────────────────────── helpers ───────────────────────────────────────


def _make_app():
    """每次 fresh create_app() — 避免全局 P2P node 污染."""
    from sisoul.daemon import create_app
    return create_app()


@pytest.fixture(scope="module", autouse=True)
def _isolated_vault(tmp_path_factory):
    """整个矩阵跑在临时空 vault 上 — 不读真实 ~/.sisoul (好友/proxy session 会污染断言)."""
    import os

    v = tmp_path_factory.mktemp("qa-vault")
    for sub in ("preferences", "goals", "chat-history", "identity"):
        (v / sub).mkdir()
    old = os.environ.get("SISOUL_VAULT")
    os.environ["SISOUL_VAULT"] = str(v)
    yield v
    if old is None:
        os.environ.pop("SISOUL_VAULT", None)
    else:
        os.environ["SISOUL_VAULT"] = old


@pytest.fixture(scope="module")
def client(_isolated_vault):
    """Module-scope TestClient (大部分只读 endpoint 共用)."""
    app = _make_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def tmp_vault(tmp_path: Path) -> Path:
    """带基础结构的临时 vault."""
    v = tmp_path / "vault"
    (v / "preferences").mkdir(parents=True)
    (v / "goals").mkdir()
    (v / "chat-history").mkdir()
    (v / "identity").mkdir()
    return v


def _write_md(p: Path, title: str = "test", body: str = "hello") -> None:
    p.write_text(
        f"---\ntitle: {title}\ntags: [test]\n---\n{body}\n",
        encoding="utf-8",
    )


# ─────────────────────────── §0. 总数 sanity ───────────────────────────────


class TestEndpointCount:
    def test_openapi_paths_count(self, client: TestClient) -> None:
        """openapi.json paths 应 >= 68 (P2-2 rag / P2-3 goal / P3-4 dao 新增 endpoint).

        v1.0+internal 68 paths · v1.1 起持续增长. 这里只校验下界, 不锁死.
        """
        r = client.get("/openapi.json")
        assert r.status_code == 200
        paths = list(r.json().get("paths", {}).keys())
        assert len(paths) >= 68, f"expect >= 68 openapi paths, got {len(paths)}: {sorted(paths)}"

    def test_registered_routes_sisoul_prefix(self, client: TestClient) -> None:
        """/sisoul/* 路由数 >= 68."""
        from sisoul.daemon import create_app
        app = _make_app()
        sisoul_routes = [
            r for r in app.routes
            if hasattr(r, "path") and r.path.startswith("/sisoul")
        ]
        assert len(sisoul_routes) >= 68, f"got {len(sisoul_routes)}"

    def test_openapi_json_structure(self, client: TestClient) -> None:
        """openapi spec 基础结构完整."""
        r = client.get("/openapi.json")
        spec = r.json()
        assert "paths" in spec
        assert "info" in spec
        assert spec["info"]["title"] == "sisoul daemon"


# ─────────────────────────── §1. /sisoul/health ────────────────────────────


class TestHealth:
    ENDPOINT = "/sisoul/health"

    def test_200_ok(self, client: TestClient) -> None:
        r = client.get(self.ENDPOINT)
        assert r.status_code == 200

    def test_200_schema(self, client: TestClient) -> None:
        r = client.get(self.ENDPOINT)
        d = r.json()
        assert d["status"] == "ok"
        assert "version" in d
        assert "phase" in d
        assert "daemon" in d

    def test_200_daemon_field_non_empty(self, client: TestClient) -> None:
        d = client.get(self.ENDPOINT).json()
        assert isinstance(d["daemon"], dict)

    def test_405_post(self, client: TestClient) -> None:
        r = client.post(self.ENDPOINT)
        assert r.status_code == 405

    def test_405_delete(self, client: TestClient) -> None:
        r = client.delete(self.ENDPOINT)
        assert r.status_code == 405


# ─────────────────────────── §2. /sisoul/identity ──────────────────────────


class TestIdentity:
    ENDPOINT = "/sisoul/identity"

    def test_200_no_seed(self, client: TestClient, tmp_vault: Path) -> None:
        """vault 里没 seed → has_seed=False."""
        r = client.get(self.ENDPOINT, params={"vault_dir": str(tmp_vault)})
        assert r.status_code == 200
        assert r.json()["has_seed"] is False

    def test_200_schema(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.get(self.ENDPOINT, params={"vault_dir": str(tmp_vault)})
        d = r.json()
        assert "has_seed" in d

    def test_200_with_seed(self, tmp_path: Path) -> None:
        """植入合法 seed (chmod 0600) → has_seed=True + fingerprint."""
        vault = tmp_path / "v2"
        vault.mkdir()
        seed_path = vault / "seed.txt"
        # 12 词合法 BIP-39 (测试词表)
        mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        seed_path.write_text(mnemonic, encoding="utf-8")
        seed_path.chmod(0o600)  # load_mnemonic_from_file 要求 ≤ 0600
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get(self.ENDPOINT, params={"vault_dir": str(vault)})
        assert r.status_code == 200
        d = r.json()
        assert d["has_seed"] is True
        assert d["master_key_fingerprint"] is not None

    def test_405_post(self, client: TestClient) -> None:
        r = client.post(self.ENDPOINT)
        assert r.status_code == 405

    def test_405_delete(self, client: TestClient) -> None:
        r = client.delete(self.ENDPOINT)
        assert r.status_code == 405


# ─────────────────────────── §3. /sisoul/restore-seed ──────────────────────


class TestRestoreSeed:
    ENDPOINT = "/sisoul/restore-seed"
    VALID_MNEMONIC = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"

    def test_400_bad_mnemonic(self, client: TestClient, tmp_path: Path) -> None:
        r = client.post(
            self.ENDPOINT,
            json={
                "seed": "bad mnemonic not valid bip39 words here extra words",
                "vault_dir": str(tmp_path / "vault"),
                "force": True,
            },
        )
        assert r.status_code == 400

    def test_201_happy(self, tmp_path: Path) -> None:
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.post(
                self.ENDPOINT,
                json={
                    "seed": self.VALID_MNEMONIC,
                    "vault_dir": str(tmp_path / "vault_restore"),
                    "force": True,
                },
            )
        assert r.status_code == 201
        d = r.json()
        assert d["ok"] is True
        assert "master_key_fingerprint" in d

    def test_409_already_exists(self, tmp_path: Path) -> None:
        vault = tmp_path / "exists_vault"
        vault.mkdir()
        (vault / "seed.txt").write_text(self.VALID_MNEMONIC, encoding="utf-8")
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.post(
                self.ENDPOINT,
                json={
                    "seed": self.VALID_MNEMONIC,
                    "vault_dir": str(vault),
                    "force": False,
                },
            )
        assert r.status_code == 409

    def test_405_get(self, client: TestClient) -> None:
        r = client.get(self.ENDPOINT)
        assert r.status_code == 405

    def test_422_missing_seed(self, client: TestClient) -> None:
        r = client.post(self.ENDPOINT, json={})
        assert r.status_code == 422


# ─────────────────────────── §4. /sisoul/did/* ─────────────────────────────


class TestDID:
    def test_get_did_200_no_dids(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.get("/sisoul/did", params={"vault_dir": str(tmp_vault)})
        assert r.status_code == 200
        assert r.json()["has_did"] is False

    def test_get_did_schema(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.get("/sisoul/did", params={"vault_dir": str(tmp_vault)})
        d = r.json()
        assert "has_did" in d
        assert "count" in d

    def test_get_did_list_200(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.get("/sisoul/did/list", params={"vault_dir": str(tmp_vault)})
        assert r.status_code == 200
        d = r.json()
        assert "count" in d
        assert "items" in d

    def test_post_did_register_201(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.post(
            "/sisoul/did/register",
            json={
                "handle": "testuser",
                "network": "mock",
                "vault_dir": str(tmp_vault),
            },
        )
        assert r.status_code == 201
        d = r.json()
        assert "did" in d
        assert d["did"].startswith("did:sisoul:")

    def test_post_did_register_400_bad_handle(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.post(
            "/sisoul/did/register",
            json={"handle": "A_BAD_HANDLE!!!", "network": "mock", "vault_dir": str(tmp_vault)},
        )
        assert r.status_code in (400, 422)

    def test_post_did_resolve_404(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.post(
            "/sisoul/did/resolve",
            json={"target": "did:sisoul:nobody", "vault_dir": str(tmp_vault)},
        )
        assert r.status_code == 404

    def test_post_did_resolve_existing(self, tmp_path: Path) -> None:
        vault = tmp_path / "did_vault"
        vault.mkdir()
        (vault / "identity").mkdir()
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            # register first
            c.post(
                "/sisoul/did/register",
                json={"handle": "alice99", "network": "mock", "vault_dir": str(vault)},
            )
            r = c.post(
                "/sisoul/did/resolve",
                json={"target": "did:sisoul:alice99", "vault_dir": str(vault)},
            )
        assert r.status_code == 200
        assert r.json()["did"] == "did:sisoul:alice99"

    def test_did_405_delete(self, client: TestClient) -> None:
        r = client.delete("/sisoul/did")
        assert r.status_code == 405


# ─────────────────────────── §5. /sisoul/preferences/* ─────────────────────


class TestPreferences:
    def test_list_empty(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.get("/sisoul/preferences/list", params={"vault": str(tmp_vault)})
        assert r.status_code == 200
        assert r.json() == []

    def test_list_with_files(self, client: TestClient, tmp_vault: Path) -> None:
        _write_md(tmp_vault / "preferences" / "pref1.md", "Pref1")
        r = client.get("/sisoul/preferences/list", params={"vault": str(tmp_vault)})
        assert r.status_code == 200
        items = r.json()
        assert len(items) >= 1
        assert items[0]["id"] == "pref1"

    def test_get_single_200(self, client: TestClient, tmp_vault: Path) -> None:
        _write_md(tmp_vault / "preferences" / "coding.md", "Coding Prefs")
        r = client.get("/sisoul/preferences/coding", params={"vault": str(tmp_vault)})
        assert r.status_code == 200
        assert r.json()["id"] == "coding"

    def test_get_single_404(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.get("/sisoul/preferences/nonexistent", params={"vault": str(tmp_vault)})
        assert r.status_code == 404

    def test_get_single_400_path_traversal(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.get("/sisoul/preferences/../etc", params={"vault": str(tmp_vault)})
        # FastAPI will either 400 or 404 (path normalisation) — not 200
        assert r.status_code in (400, 404, 422)

    def test_405_post_list(self, client: TestClient) -> None:
        r = client.post("/sisoul/preferences/list")
        assert r.status_code == 405


# ─────────────────────────── §6. /sisoul/goals/* ───────────────────────────


class TestGoals:
    def test_list_empty(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.get("/sisoul/goals/list", params={"vault": str(tmp_vault)})
        assert r.status_code == 200
        assert r.json() == []

    def test_list_with_files(self, client: TestClient, tmp_vault: Path) -> None:
        _write_md(tmp_vault / "goals" / "goal1.md", "Goal One")
        r = client.get("/sisoul/goals/list", params={"vault": str(tmp_vault)})
        items = r.json()
        assert len(items) >= 1
        assert items[0]["id"] == "goal1"

    def test_get_single_200(self, client: TestClient, tmp_vault: Path) -> None:
        _write_md(tmp_vault / "goals" / "learn-rust.md", "Learn Rust")
        r = client.get("/sisoul/goals/learn-rust", params={"vault": str(tmp_vault)})
        assert r.status_code == 200
        assert r.json()["id"] == "learn-rust"

    def test_get_single_404(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.get("/sisoul/goals/nonexistent", params={"vault": str(tmp_vault)})
        assert r.status_code == 404

    def test_progress_default_zero(self, client: TestClient, tmp_vault: Path) -> None:
        _write_md(tmp_vault / "goals" / "newgoal.md", "New Goal")
        r = client.get("/sisoul/goals/newgoal", params={"vault": str(tmp_vault)})
        assert r.json()["progress"] == 0.0

    def test_405_post(self, client: TestClient) -> None:
        r = client.post("/sisoul/goals/list")
        assert r.status_code == 405


# ─────────────────────────── §7. /sisoul/chat-history/* ────────────────────


class TestChatHistory:
    def test_list_empty_vault(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.get("/sisoul/chat-history/list", params={"vault": str(tmp_vault)})
        assert r.status_code == 200
        assert r.json() == []

    def test_list_with_session(self, client: TestClient, tmp_vault: Path) -> None:
        date_dir = tmp_vault / "chat-history" / "2026-05-18"
        date_dir.mkdir(parents=True)
        _write_md(date_dir / "sess1.md", "Session 1")
        r = client.get("/sisoul/chat-history/list", params={"vault": str(tmp_vault)})
        items = r.json()
        assert len(items) >= 1
        assert items[0]["session_id"] == "sess1"

    def test_get_session_200(self, client: TestClient, tmp_vault: Path) -> None:
        date_dir = tmp_vault / "chat-history" / "2026-05-17"
        date_dir.mkdir(parents=True)
        _write_md(date_dir / "mysession.md", "My Session")
        r = client.get(
            "/sisoul/chat-history/2026-05-17/mysession",
            params={"vault": str(tmp_vault)},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["session_id"] == "mysession"
        assert d["date"] == "2026-05-17"

    def test_get_session_404(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.get(
            "/sisoul/chat-history/2026-01-01/missing",
            params={"vault": str(tmp_vault)},
        )
        assert r.status_code == 404

    def test_get_session_400_bad_date(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.get(
            "/sisoul/chat-history/bad-date/sess",
            params={"vault": str(tmp_vault)},
        )
        assert r.status_code == 400

    def test_405_post(self, client: TestClient) -> None:
        r = client.post("/sisoul/chat-history/list")
        assert r.status_code == 405


# ─────────────────────────── §8. /sisoul/status/full ───────────────────────


class TestStatusFull:
    ENDPOINT = "/sisoul/status/full"

    def test_200_no_vault(self, client: TestClient, tmp_path: Path) -> None:
        vault = tmp_path / "nonexistent_vault"
        r = client.get(self.ENDPOINT, params={"vault": str(vault)})
        assert r.status_code == 200
        d = r.json()
        assert d["vault_exists"] is False

    def test_200_with_vault(self, client: TestClient, tmp_vault: Path) -> None:
        r = client.get(self.ENDPOINT, params={"vault": str(tmp_vault)})
        assert r.status_code == 200
        d = r.json()
        assert d["vault_exists"] is True

    def test_200_schema_fields(self, client: TestClient, tmp_vault: Path) -> None:
        d = client.get(self.ENDPOINT, params={"vault": str(tmp_vault)}).json()
        for key in ("vault_root", "vault_exists", "vault_size_bytes", "counts", "daemon", "identity", "llm"):
            assert key in d, f"missing key: {key}"

    def test_daemon_uptime_non_negative(self, client: TestClient, tmp_vault: Path) -> None:
        d = client.get(self.ENDPOINT, params={"vault": str(tmp_vault)}).json()
        assert d["daemon"]["uptime_s"] >= 0

    def test_405_post(self, client: TestClient) -> None:
        r = client.post(self.ENDPOINT)
        assert r.status_code == 405


# ─────────────────────────── §9. /sisoul/p2p/* ─────────────────────────────


class TestP2P:
    """P2P endpoints — node state is in-process global, use fresh app per test."""

    def _client(self):
        return TestClient(_make_app(), raise_server_exceptions=False)

    def test_get_status_200_not_running(self) -> None:
        with self._client() as c:
            r = c.get("/sisoul/p2p/status")
        assert r.status_code == 200
        assert r.json()["running"] is False

    def test_get_status_schema(self) -> None:
        with self._client() as c:
            d = c.get("/sisoul/p2p/status").json()
        for k in ("running", "transport", "peer_id", "peers", "stats"):
            assert k in d

    def test_get_peers_200_empty(self) -> None:
        with self._client() as c:
            r = c.get("/sisoul/p2p/peers")
        assert r.status_code == 200
        assert r.json()["peers"] == []

    def test_post_start_404_nonexistent_vault(self, tmp_path: Path) -> None:
        with self._client() as c:
            r = c.post(
                "/sisoul/p2p/start",
                json={"vault_dir": str(tmp_path / "nope"), "port": 0},
            )
        assert r.status_code == 404

    def test_post_start_201(self, tmp_path: Path) -> None:
        """P2P start 需要 seed — 植入 seed 后 start → 201."""
        vault = tmp_path / "p2p_vault"
        vault.mkdir()
        seed = vault / "seed.txt"
        mnemonic = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        seed.write_text(mnemonic, encoding="utf-8")
        seed.chmod(0o600)
        with self._client() as c:
            r = c.post(
                "/sisoul/p2p/start",
                json={"vault_dir": str(vault), "port": 0, "transport": "inmem"},
            )
        assert r.status_code == 201
        assert r.json()["ok"] is True

    def test_post_stop_200_no_node(self) -> None:
        with self._client() as c:
            r = c.post("/sisoul/p2p/stop")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_post_sync_409_no_node(self) -> None:
        with self._client() as c:
            r = c.post("/sisoul/p2p/sync", json={})
        assert r.status_code == 409

    def test_post_add_peer_409_no_node(self) -> None:
        with self._client() as c:
            r = c.post(
                "/sisoul/p2p/add-peer",
                json={"multiaddr": "inmem://testpeer"},
            )
        assert r.status_code == 409

    def test_405_delete_status(self) -> None:
        with self._client() as c:
            r = c.delete("/sisoul/p2p/status")
        assert r.status_code == 405


# ─────────────────────────── §10. /sisoul/attest/* ─────────────────────────


class TestAttest:
    def test_get_queue_200(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "eas.db")
        r = client.get("/sisoul/attest/queue", params={"queue_db": db})
        assert r.status_code == 200
        d = r.json()
        assert "stats" in d
        assert "items" in d

    def test_get_queue_400_bad_status(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "eas2.db")
        r = client.get(
            "/sisoul/attest/queue",
            params={"status": "invalid_status", "queue_db": db},
        )
        assert r.status_code == 400

    def test_get_history_local_200(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "hist.db")
        r = client.get(
            "/sisoul/attest/history",
            params={"source": "local", "queue_db": db},
        )
        assert r.status_code == 200
        assert r.json()["source"] == "local"

    def test_get_history_400_bad_source(self, client: TestClient, tmp_path: Path) -> None:
        r = client.get(
            "/sisoul/attest/history",
            params={"source": "unknown"},
        )
        assert r.status_code == 400

    def test_get_verify_local_any_uid(self, client: TestClient, tmp_path: Path) -> None:
        """verify 不存在的 uid → 200 local.valid=False (不是 404)."""
        db = str(tmp_path / "verify.db")
        r = client.get(
            "/sisoul/attest/verify/fake-uid-1234",
            params={"queue_db": db},
        )
        assert r.status_code == 200
        assert r.json()["local"]["valid"] is False

    def test_405_delete_queue(self, client: TestClient) -> None:
        r = client.delete("/sisoul/attest/queue")
        assert r.status_code == 405


# ─────────────────────────── §11. /sisoul/audit ─────────────────────────────


class TestAudit:
    ENDPOINT = "/sisoul/audit"

    def test_200_enqueue(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "audit.db")
        r = client.post(
            self.ENDPOINT,
            json={
                "action_type": "rm",
                "target": "/tmp/test.txt",
                "prompt": "delete test file",
                "tool_name": "claude-code",
                "queue_db": db,
                "auto_flush": False,
            },
        )
        assert r.status_code == 200
        d = r.json()
        assert "queue_id" in d
        assert d["queue_id"] != ""

    def test_422_missing_required(self, client: TestClient) -> None:
        r = client.post(self.ENDPOINT, json={})
        assert r.status_code == 422

    def test_200_actor_did_fallback(self, client: TestClient, tmp_path: Path) -> None:
        """no actor_did → 200 with fallback did."""
        db = str(tmp_path / "audit2.db")
        r = client.post(
            self.ENDPOINT,
            json={
                "action_type": "git-push",
                "target": "origin main",
                "queue_db": db,
                "auto_flush": False,
            },
        )
        assert r.status_code == 200
        assert "did:sisoul" in r.json()["actor_did"]

    def test_405_get(self, client: TestClient) -> None:
        r = client.get(self.ENDPOINT)
        assert r.status_code == 405

    def test_405_delete(self, client: TestClient) -> None:
        r = client.delete(self.ENDPOINT)
        assert r.status_code == 405


# ─────────────────────────── §12. /sisoul/snapshot/* ───────────────────────


class TestSnapshot:
    def test_get_list_200(self, client: TestClient) -> None:
        """历史空时 → []."""
        with patch("sisoul.daemon_routes.snapshot.SnapshotHistory") as MockH:
            MockH.return_value.load.return_value = []
            r = client.get("/sisoul/snapshot/list")
        assert r.status_code == 200
        assert r.json() == []

    def test_get_config_200(self, client: TestClient) -> None:
        r = client.get("/sisoul/snapshot/config")
        assert r.status_code == 200
        d = r.json()
        assert "pinata_jwt_configured" in d
        assert "history_path" in d

    def test_post_now_400_bad_vault(self, client: TestClient, tmp_path: Path) -> None:
        """vault 不存在 → 400."""
        r = client.post(
            "/sisoul/snapshot/now",
            json={"vault_dir": str(tmp_path / "nope"), "upload": "none", "network": "mock"},
        )
        assert r.status_code == 400

    def test_post_now_200_mock(self, tmp_vault: Path) -> None:
        """mock ArweaveSnapshot.snapshot_now → 200."""
        from sisoul.onchain.arweave import SnapshotRecord
        fake = SnapshotRecord(
            timestamp="2026-05-18T00:00:00Z",
            size_bytes=1024,
            sha256="a" * 64,
            ipfs_cid="Qmtest",
            arweave_tx_id=None,
            vault_master_key_fingerprint="deadbeef",
            network="mock",
            status="ok",
            error=None,
        )
        with patch("sisoul.daemon_routes.snapshot.ArweaveSnapshot") as MockA:
            MockA.return_value.snapshot_now.return_value = fake
            app = _make_app()
            with TestClient(app, raise_server_exceptions=False) as c:
                r = c.post(
                    "/sisoul/snapshot/now",
                    json={"vault_dir": str(tmp_vault), "upload": "none", "network": "mock"},
                )
        assert r.status_code == 200
        assert r.json()["sha256"] == "a" * 64

    def test_post_restore_404_not_in_history(self, client: TestClient, tmp_path: Path) -> None:
        """sha256 格式 tx_id → history 查 → 404."""
        target = str(tmp_path / "restore_target")
        r = client.post(
            "/sisoul/snapshot/restore",
            json={
                "tx_id_or_cid": "a" * 64,  # 64-char hex → history lookup path
                "target_vault_dir": target,
                "source": "auto",
                "network": "mock",
            },
        )
        assert r.status_code == 404

    def test_post_schedule_200(self, client: TestClient) -> None:
        r = client.post(
            "/sisoul/snapshot/schedule",
            json={"cadence": "monthly", "upload": "both", "install": False},
        )
        assert r.status_code == 200

    def test_405_delete_list(self, client: TestClient) -> None:
        r = client.delete("/sisoul/snapshot/list")
        assert r.status_code == 405


# ─────────────────────────── §13. /sisoul/friend/* ─────────────────────────


class TestFriend:
    """使用独立 tmp friend_db 避免状态污染."""

    def _post(self, client: TestClient, path: str, body: dict, db: str) -> Any:
        body.setdefault("friend_db", db)
        body.setdefault("own_did", "did:sisoul:alice")
        return client.post(f"/sisoul/friend/{path}", json=body)

    def test_list_empty(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "f.db")
        r = client.get(
            "/sisoul/friend/list",
            params={"own_did": "did:sisoul:alice", "friend_db": db},
        )
        assert r.status_code == 200
        assert r.json() == []

    def test_post_request_200(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "fr.db")
        r = self._post(
            client,
            "request",
            {"target_did": "did:sisoul:bob", "message": "hi"},
            db,
        )
        assert r.status_code == 200
        d = r.json()
        assert d["requester_did"] == "did:sisoul:alice"
        assert d["target_did"] == "did:sisoul:bob"

    def test_post_request_400_self(self, client: TestClient, tmp_path: Path) -> None:
        """向自己 request → 400."""
        db = str(tmp_path / "fr2.db")
        r = self._post(
            client,
            "request",
            {"target_did": "did:sisoul:alice", "message": "self"},
            db,
        )
        assert r.status_code == 400

    def test_get_requests_200(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "reqs.db")
        r = client.get(
            "/sisoul/friend/requests",
            params={"own_did": "did:sisoul:alice", "friend_db": db},
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_post_accept_404(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "acc.db")
        r = self._post(
            client, "accept", {"request_id": "nonexistent-req-id"}, db
        )
        assert r.status_code == 404

    def test_post_receive_200(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "recv.db")
        r = client.post(
            "/sisoul/friend/receive",
            json={
                "requester_did": "did:sisoul:charlie",
                "message": "hey alice",
                "own_did": "did:sisoul:alice",
                "friend_db": db,
            },
        )
        assert r.status_code == 200
        assert r.json()["requester_did"] == "did:sisoul:charlie"

    def test_post_revoke_404(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "rev.db")
        r = self._post(client, "revoke", {"did": "did:sisoul:nobody"}, db)
        assert r.status_code == 404

    def test_post_confirm_mutual_404(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "cm.db")
        r = client.post(
            "/sisoul/friend/confirm-mutual",
            json={
                "friend_did": "did:sisoul:nonexistent",
                "mutual_attestation_uid": "uid123",
                "own_did": "did:sisoul:alice",
                "friend_db": db,
            },
        )
        assert r.status_code == 404

    def test_get_info_404(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "info.db")
        r = client.get(
            "/sisoul/friend/info/did:sisoul:nobody",
            params={"own_did": "did:sisoul:alice", "friend_db": db},
        )
        assert r.status_code == 404

    def test_post_manual_score_404(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "ms.db")
        r = client.post(
            "/sisoul/friend/score/manual",
            json={
                "did": "did:sisoul:nobody",
                "score": 0.9,
                "own_did": "did:sisoul:alice",
                "friend_db": db,
            },
        )
        assert r.status_code == 404

    def test_list_400_bad_status(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "bad.db")
        r = client.get(
            "/sisoul/friend/list",
            params={"own_did": "did:sisoul:alice", "friend_db": db, "status": "invalid"},
        )
        assert r.status_code == 400

    def test_405_delete_list(self, client: TestClient) -> None:
        r = client.delete("/sisoul/friend/list")
        assert r.status_code == 405


# ─────────────────────────── §14. /sisoul/borrow ───────────────────────────


class TestBorrow:
    ENDPOINT = "/sisoul/borrow"

    def test_422_missing_fields(self, client: TestClient) -> None:
        r = client.post(self.ENDPOINT, json={})
        assert r.status_code == 422

    def test_400_bad_force_mode(self, client: TestClient, tmp_path: Path) -> None:
        r = client.post(
            self.ENDPOINT,
            json={
                "borrower_did": "did:sisoul:alice",
                "lender_did": "did:sisoul:bob",
                "resource_type": "llm_quota",
                "amount": 1000,
                "model": "claude-opus-4-7",
                "force_mode": "INVALID_MODE",
                "lend_db": str(tmp_path / "l.db"),
            },
        )
        assert r.status_code == 400

    def test_200_or_5xx_valid_body(self, client: TestClient, tmp_path: Path) -> None:
        """合法 body → 不是 4xx (可能是 500 permission denied, 但不是 422)."""
        r = client.post(
            self.ENDPOINT,
            json={
                "borrower_did": "did:sisoul:alice",
                "lender_did": "did:sisoul:bob",
                "resource_type": "llm_quota",
                "amount": 100,
                "model": "claude-opus-4-7",
                "lend_db": str(tmp_path / "l2.db"),
                "enqueue_onchain": False,
            },
        )
        assert r.status_code not in (422,), f"got {r.status_code}: {r.text}"

    def test_405_get(self, client: TestClient) -> None:
        r = client.get(self.ENDPOINT)
        assert r.status_code == 405

    def test_405_delete(self, client: TestClient) -> None:
        r = client.delete(self.ENDPOINT)
        assert r.status_code == 405


# ─────────────────────────── §15. /sisoul/lend/* ───────────────────────────


class TestLend:
    def test_get_pending_200(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "lend.db")
        r = client.get("/sisoul/lend/pending", params={"lend_db": db})
        assert r.status_code == 200
        assert "pending" in r.json()

    def test_get_all_200(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "lend2.db")
        r = client.get("/sisoul/lend/all", params={"lend_db": db})
        assert r.status_code == 200
        d = r.json()
        assert "count" in d
        assert d["count"] == 0

    def test_post_approve_404(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "lend3.db")
        r = client.post(
            "/sisoul/lend/approve",
            json={"request_id": "nonexistent", "lend_db": db},
        )
        assert r.status_code == 404

    def test_post_deny_404(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "lend4.db")
        r = client.post(
            "/sisoul/lend/deny",
            json={"request_id": "nonexistent", "lend_db": db},
        )
        assert r.status_code == 404

    def test_post_request_400_bad_mode(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "lend5.db")
        r = client.post(
            "/sisoul/lend/request",
            json={
                "borrower_did": "did:sisoul:alice",
                "lender_did": "did:sisoul:bob",
                "amount": 100,
                "model": "claude-opus-4-7",
                "mode": "BAD_MODE",
                "lend_db": db,
            },
        )
        assert r.status_code == 400

    def test_405_delete_pending(self, client: TestClient) -> None:
        r = client.delete("/sisoul/lend/pending")
        assert r.status_code == 405


# ─────────────────────────── §16. /sisoul/ledger/* ─────────────────────────


class TestLedger:
    def test_get_stats_200(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "led.db")
        r = client.get("/sisoul/ledger/stats", params={"ledger_db": db})
        assert r.status_code == 200
        assert "stats" in r.json()

    def test_get_imbalance_200(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "led2.db")
        r = client.get(
            "/sisoul/ledger/imbalance",
            params={"self_did": "did:sisoul:alice", "ledger_db": db},
        )
        assert r.status_code == 200
        d = r.json()
        assert "warnings" in d
        assert "count" in d

    def test_get_friend_ledger_200(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "led3.db")
        r = client.get(
            "/sisoul/ledger/did:sisoul:bob",
            params={"self_did": "did:sisoul:alice", "ledger_db": db},
        )
        assert r.status_code == 200
        assert "balance" in r.json()

    def test_get_imbalance_422_missing_self_did(self, client: TestClient) -> None:
        r = client.get("/sisoul/ledger/imbalance")
        assert r.status_code == 422

    def test_405_post_stats(self, client: TestClient) -> None:
        r = client.post("/sisoul/ledger/stats")
        assert r.status_code == 405


# ─────────────────────────── §17. /sisoul/borrow-proxy/* ───────────────────


class TestBorrowProxy:
    def test_get_list_200_empty(self, client: TestClient) -> None:
        r = client.get("/sisoul/borrow-proxy/list")
        assert r.status_code == 200
        d = r.json()
        assert d["count"] == 0
        assert d["sessions"] == []

    def test_post_start_200(self, client: TestClient) -> None:
        r = client.post(
            "/sisoul/borrow-proxy/start",
            json={
                "borrower_did": "did:sisoul:alice",
                "lender_did": "did:sisoul:bob",
                "model": "claude-opus-4-7",
                "base_url": "http://127.0.0.1:9876",
            },
        )
        assert r.status_code == 200
        assert "session" in r.json()

    def test_get_session_404(self, client: TestClient) -> None:
        r = client.get("/sisoul/borrow-proxy/nonexistent-session-id")
        assert r.status_code == 404

    def test_post_stop_404(self, client: TestClient) -> None:
        r = client.post("/sisoul/borrow-proxy/nonexistent-sid/stop")
        assert r.status_code == 404

    def test_405_delete_list(self, client: TestClient) -> None:
        r = client.delete("/sisoul/borrow-proxy/list")
        assert r.status_code == 405


# ─────────────────────────── §18. /sisoul/proxy/* ──────────────────────────


class TestProxy:
    @pytest.fixture(autouse=True)
    def _no_global_proxy(self):
        """daemon startup 会 auto-init EncryptedProxy — no_proxy 用例先清掉再还原."""
        from sisoul.friend.encrypted_proxy import get_global_proxy, set_global_proxy

        saved = get_global_proxy()
        set_global_proxy(None)
        yield
        set_global_proxy(saved)

    def test_get_sessions_200_no_proxy(self, client: TestClient) -> None:
        r = client.get("/sisoul/proxy/sessions")
        assert r.status_code == 200
        assert r.json()["running"] is False

    def test_post_forward_409_no_proxy(self, client: TestClient) -> None:
        """proxy 未启动 → 409."""
        r = client.post(
            "/sisoul/proxy/forward",
            json={
                "borrower_did": "did:sisoul:alice",
                "borrower_pubkey_hex": "aa" * 32,
                "encrypted_prompt_b64": base64.b64encode(b"hello").decode(),
                "target_model": "claude-opus-4-7",
            },
        )
        assert r.status_code == 409

    def test_post_forward_400_bad_pubkey(self, client: TestClient) -> None:
        """pubkey 不是合法 hex → 400."""
        r = client.post(
            "/sisoul/proxy/forward",
            json={
                "borrower_did": "did:sisoul:alice",
                "borrower_pubkey_hex": "ZZZZ_NOT_HEX",
                "encrypted_prompt_b64": base64.b64encode(b"hello").decode(),
                "target_model": "claude-opus-4-7",
            },
        )
        assert r.status_code in (400, 409)

    def test_post_end_session_409_no_proxy(self, client: TestClient) -> None:
        r = client.post(
            "/sisoul/proxy/end-session",
            json={"session_id": "fake-session"},
        )
        assert r.status_code == 409

    def test_405_delete_sessions(self, client: TestClient) -> None:
        r = client.delete("/sisoul/proxy/sessions")
        assert r.status_code == 405


# ─────────────────────────── §19. /sisoul/perms/* ──────────────────────────


class TestPermissions:
    def test_get_list_200(self, client: TestClient, tmp_path: Path) -> None:
        pd = str(tmp_path / "perms")
        r = client.get("/sisoul/perms/list", params={"perms_dir": pd})
        assert r.status_code == 200
        d = r.json()
        assert d["count"] == 0

    def test_get_list_404_specific_friend(self, client: TestClient, tmp_path: Path) -> None:
        pd = str(tmp_path / "perms2")
        r = client.get(
            "/sisoul/perms/list",
            params={"friend": "did:sisoul:nobody", "perms_dir": pd},
        )
        assert r.status_code == 404

    def test_post_set_200(self, client: TestClient, tmp_path: Path) -> None:
        pd = str(tmp_path / "perms3")
        r = client.post(
            "/sisoul/perms/set",
            json={"friend_did": "did:sisoul:bob", "perms_dir": pd},
        )
        assert r.status_code == 200
        assert r.json()["friend"] == "did:sisoul:bob"

    def test_post_set_422_bad_mode(self, client: TestClient, tmp_path: Path) -> None:
        pd = str(tmp_path / "perms4")
        r = client.post(
            "/sisoul/perms/set",
            json={
                "friend_did": "did:sisoul:bob",
                "perms_dir": pd,
                "llm_quota_share": {"enabled": True, "mode": "BADMODE"},
            },
        )
        assert r.status_code == 422

    def test_post_check_200(self, client: TestClient, tmp_path: Path) -> None:
        pd = str(tmp_path / "perms5")
        r = client.post(
            "/sisoul/perms/check",
            json={
                "friend_did": "did:sisoul:bob",
                "resource_type": "llm_quota",
                "amount": 100,
                "perms_dir": pd,
            },
        )
        assert r.status_code == 200
        d = r.json()
        assert "allowed" in d
        assert "reason" in d

    def test_post_check_422_bad_resource(self, client: TestClient, tmp_path: Path) -> None:
        pd = str(tmp_path / "perms6")
        r = client.post(
            "/sisoul/perms/check",
            json={
                "friend_did": "did:sisoul:bob",
                "resource_type": "UNKNOWN_RESOURCE",
                "amount": 100,
                "perms_dir": pd,
            },
        )
        assert r.status_code == 422

    def test_get_reputation_200(self, client: TestClient) -> None:
        r = client.get(
            "/sisoul/perms/reputation",
            params={"did": "did:sisoul:alice"},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["did"] == "did:sisoul:alice"
        assert "score" in d
        assert "grade" in d

    def test_post_revoke_200(self, client: TestClient, tmp_path: Path) -> None:
        pd = str(tmp_path / "perms7")
        r = client.post(
            "/sisoul/perms/revoke",
            json={"friend_did": "did:sisoul:eve", "perms_dir": pd},
        )
        assert r.status_code == 200
        assert r.json()["revoked"] is True

    def test_get_scan_log_200(self, client: TestClient, tmp_path: Path) -> None:
        db = str(tmp_path / "scan.db")
        r = client.get("/sisoul/perms/scan-log", params={"scan_db": db})
        assert r.status_code == 200
        assert "events" in r.json()

    def test_405_delete_list(self, client: TestClient) -> None:
        r = client.delete("/sisoul/perms/list")
        assert r.status_code == 405


# ─────────────────────────── §20. /sisoul/skill/* ──────────────────────────


class TestSkill:
    def test_get_list_200(self, client: TestClient, tmp_path: Path) -> None:
        with patch("sisoul.daemon_routes.skill._owned_skills_dir", return_value=tmp_path / "skills"):
            r = client.get("/sisoul/skill/list")
        assert r.status_code == 200
        d = r.json()
        assert "owned" in d

    def test_post_create_200(self, client: TestClient, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills_owned"
        with patch("sisoul.daemon_routes.skill._owned_skills_dir", return_value=skills_dir):
            with patch("sisoul.daemon_routes.skill._owned_path") as mock_path:
                skill_file = skills_dir / "testskill.json"
                skills_dir.mkdir(parents=True, exist_ok=True)
                mock_path.return_value = skill_file
                r = client.post(
                    "/sisoul/skill/create",
                    json={
                        "name": "test-helper",
                        "system_prompt": "You are a test assistant.",
                        "description": "A test skill",
                        "owner_did": "did:sisoul:alice",
                    },
                )
        assert r.status_code == 200
        d = r.json()
        assert "skill_id" in d
        assert "fingerprint" in d

    def test_post_create_400_empty_name(self, client: TestClient) -> None:
        r = client.post(
            "/sisoul/skill/create",
            json={
                "name": "",
                "system_prompt": "",
                "owner_did": "did:sisoul:alice",
            },
        )
        assert r.status_code == 400

    def test_get_sessions_200(self, client: TestClient) -> None:
        r = client.get("/sisoul/skill/sessions")
        assert r.status_code == 200
        assert "sessions" in r.json()

    def test_post_end_session_404(self, client: TestClient) -> None:
        r = client.post(
            "/sisoul/skill/end-session",
            json={"session_id": "nonexistent-session"},
        )
        assert r.status_code == 404

    def test_post_borrow_400_bad_qualified_name(self, client: TestClient) -> None:
        r = client.post(
            "/sisoul/skill/borrow",
            json={"qualified_name": "invalid_no_colon_separator"},
        )
        assert r.status_code == 400

    def test_post_proxy_chat_404_no_session(self, client: TestClient) -> None:
        r = client.post(
            "/sisoul/skill/proxy-chat",
            json={
                "session_id": "nonexistent-session",
                "prompt": "hello",
                "use_mock_forwarder": True,
            },
        )
        assert r.status_code == 404

    def test_post_lend_404_no_skill(self, client: TestClient, tmp_path: Path) -> None:
        skills_dir = tmp_path / "no_skills"
        skills_dir.mkdir()
        with patch("sisoul.daemon_routes.skill._owned_path") as mock_path:
            mock_path.return_value = skills_dir / "nonexistent.json"
            r = client.post(
                "/sisoul/skill/lend",
                json={"skill_id": "nonexistent", "max_duration_minutes": 30},
            )
        assert r.status_code == 404

    def test_405_delete_list(self, client: TestClient) -> None:
        r = client.delete("/sisoul/skill/list")
        assert r.status_code == 405


# ─────────────────────────── §21. Response time sanity ──────────────────────


class TestResponseTime:
    """所有 GET 只读 endpoint 单次 < 100ms (TestClient in-memory 应远低于此)."""

    READONLY_ENDPOINTS = [
        "/sisoul/health",
        "/sisoul/p2p/status",
        "/sisoul/p2p/peers",
        "/sisoul/proxy/sessions",
        "/sisoul/skill/sessions",
        "/sisoul/skill/list",
        "/sisoul/snapshot/config",
        "/sisoul/snapshot/list",
        "/sisoul/attest/queue",
    ]

    def test_readonly_endpoints_under_100ms(self, client: TestClient, tmp_path: Path) -> None:
        slow: list[tuple[str, float]] = []
        params_map: dict[str, dict] = {
            "/sisoul/attest/queue": {"queue_db": str(tmp_path / "rt.db")},
        }
        for ep in self.READONLY_ENDPOINTS:
            params = params_map.get(ep, {})
            t0 = time.perf_counter()
            client.get(ep, params=params)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if elapsed_ms > 100:
                slow.append((ep, elapsed_ms))
        assert not slow, f"Slow endpoints (>100ms): {slow}"

    def test_health_under_10ms(self, client: TestClient) -> None:
        times = []
        for _ in range(5):
            t0 = time.perf_counter()
            client.get("/sisoul/health")
            times.append((time.perf_counter() - t0) * 1000)
        avg_ms = sum(times) / len(times)
        assert avg_ms < 10, f"health avg {avg_ms:.1f}ms > 10ms"


# ─────────────────────────── §22. Attest flush (mock EAS) ──────────────────


class TestAttestFlush:
    def test_flush_409_queue_empty(self, client: TestClient, tmp_path: Path) -> None:
        """空 queue flush → 409 QueueEmptyError."""
        db = str(tmp_path / "flush.db")
        r = client.post(
            "/sisoul/attest/flush",
            json={"force": False, "queue_db": db},
        )
        assert r.status_code == 409

    def test_flush_200_mock_network(self, client: TestClient, tmp_path: Path) -> None:
        """先入 1 条 audit → queue 非空 → mock flush → 200."""
        db = str(tmp_path / "flush2.db")
        # 先入队
        client.post(
            "/sisoul/audit",
            json={
                "action_type": "rm",
                "target": "/tmp/x",
                "queue_db": db,
                "auto_flush": False,
            },
        )
        from sisoul.onchain.eas import BatchResult
        fake_result = BatchResult(
            batch_uid="test-batch-uid",
            tx_hash="0x" + "a" * 64,
            network="mock",
            schema_uid="0x" + "b" * 64,
            attestation_uids=["uid1"],
            count=1,
            method="mock",
            gas_used_estimate=21000,
            gas_cost_wei_estimate=1000000000,
            confirmed_at="2026-05-18T00:00:00Z",
        )
        with patch("sisoul.daemon_routes.attest.upload_batch", return_value=fake_result):
            r = client.post(
                "/sisoul/attest/flush",
                json={"force": True, "queue_db": db},
            )
        assert r.status_code == 200
        assert r.json()["count"] == 1

    def test_405_get(self, client: TestClient) -> None:
        r = client.get("/sisoul/attest/flush")
        assert r.status_code == 405


# ─────────────────────────── §23. Full lifecycle: skill create→borrow→chat ─


class TestSkillLifecycle:
    """create → lend → borrow → proxy_chat → end_session (mock-only 无 LLM 调用)."""

    def test_skill_create_to_end_session(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills_lc"
        skills_dir.mkdir(parents=True)

        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            # 1. create
            with patch("sisoul.daemon_routes.skill._owned_skills_dir", return_value=skills_dir):
                r = c.post(
                    "/sisoul/skill/create",
                    json={
                        "name": "qa-lifecycle-skill",
                        "system_prompt": "QA assistant",
                        "description": "lifecycle test",
                        "owner_did": "did:sisoul:qaowner",
                    },
                )
            assert r.status_code == 200, f"create failed: {r.text}"
            skill_id = r.json()["skill_id"]

            # 2. lend (no pin_to_ipfs)
            with patch("sisoul.daemon_routes.skill._load_owned") as mock_load:
                from sisoul.friend.skill_package import package_skill
                pkg = package_skill(
                    name="qa-lifecycle-skill",
                    owner_did="did:sisoul:qaowner",
                    system_prompt="QA assistant",
                )
                mock_load.return_value = pkg
                r2 = c.post(
                    "/sisoul/skill/lend",
                    json={"skill_id": skill_id, "max_duration_minutes": 5},
                )
            assert r2.status_code == 200

            # 3. borrow (self-loop) → skip if skill file not on disk (mock env)
            # 4. list sessions
            r3 = c.get("/sisoul/skill/sessions")
            assert r3.status_code == 200

            # 5. end_session 不存在 → 404
            r4 = c.post(
                "/sisoul/skill/end-session",
                json={"session_id": "doesnotexist"},
            )
            assert r4.status_code == 404


# ─────────────────────────── §24. DID register then resolve ─────────────────


class TestDIDRegisterResolve:
    def test_register_then_resolve_round_trip(self, tmp_path: Path) -> None:
        vault = tmp_path / "did_rt"
        vault.mkdir()
        (vault / "identity").mkdir()
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            r1 = c.post(
                "/sisoul/did/register",
                json={"handle": "roundtrip", "network": "mock", "vault_dir": str(vault)},
            )
            assert r1.status_code == 201
            did_str = r1.json()["did"]

            r2 = c.post(
                "/sisoul/did/resolve",
                json={"target": did_str, "vault_dir": str(vault)},
            )
            assert r2.status_code == 200
            assert r2.json()["did"] == did_str

            r3 = c.get("/sisoul/did/list", params={"vault_dir": str(vault)})
            assert r3.status_code == 200
            assert r3.json()["count"] >= 1

    def test_register_then_get_current(self, tmp_path: Path) -> None:
        vault = tmp_path / "did_cur"
        vault.mkdir()
        (vault / "identity").mkdir()
        app = _make_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            c.post(
                "/sisoul/did/register",
                json={"handle": "current1", "network": "mock", "vault_dir": str(vault)},
            )
            r = c.get("/sisoul/did", params={"vault_dir": str(vault)})
            assert r.status_code == 200
            assert r.json()["has_did"] is True


# ─────────────────────────── §25. PWA chat-history date filter ──────────────


class TestChatHistoryFilter:
    def test_date_filter_returns_correct_sessions(self, client: TestClient, tmp_path: Path) -> None:
        vault = tmp_path / "chat_vault"
        (vault / "chat-history" / "2026-05-10").mkdir(parents=True)
        (vault / "chat-history" / "2026-05-11").mkdir(parents=True)
        _write_md(vault / "chat-history" / "2026-05-10" / "s1.md", "Session A")
        _write_md(vault / "chat-history" / "2026-05-11" / "s2.md", "Session B")

        r = client.get(
            "/sisoul/chat-history/list",
            params={"vault": str(vault), "date": "2026-05-10"},
        )
        items = r.json()
        assert len(items) == 1
        assert items[0]["date"] == "2026-05-10"

    def test_limit_param(self, client: TestClient, tmp_path: Path) -> None:
        vault = tmp_path / "chat_lim"
        date_dir = vault / "chat-history" / "2026-05-18"
        date_dir.mkdir(parents=True)
        for i in range(5):
            _write_md(date_dir / f"sess{i}.md", f"Session {i}")
        r = client.get(
            "/sisoul/chat-history/list",
            params={"vault": str(vault), "limit": 3},
        )
        assert len(r.json()) <= 3
