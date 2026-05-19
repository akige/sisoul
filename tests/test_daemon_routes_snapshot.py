"""tests · daemon_routes.snapshot (FastAPI TestClient).

5 endpoints, mock 模式不打真网.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.snapshot import snapshot_router
from sisoul.identity.seed import generate_mnemonic
from sisoul.onchain.arweave import DEFAULT_HISTORY_PATH


@pytest.fixture(autouse=True)
def isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("SISOUL_MNEMONIC", generate_mnemonic(128))
    monkeypatch.delenv("PINATA_JWT", raising=False)
    monkeypatch.delenv("ARWEAVE_WALLET", raising=False)
    monkeypatch.delenv("ARWEAVE_ALLOW_MAINNET", raising=False)
    # DEFAULT_HISTORY_PATH 在 import 时 resolve 了, 每 test 隔离
    import sisoul.onchain.arweave as arw_mod

    monkeypatch.setattr(arw_mod, "DEFAULT_HISTORY_PATH", tmp_path / "snapshot_history.json")


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(snapshot_router)
    return TestClient(app)


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / "preferences").mkdir(parents=True)
    (v / "preferences" / "a.md").write_text("# a\nbody\n", encoding="utf-8")
    return v


# ── /now ────────────────────────────────────────────────────────────────


def test_post_now_mock(client: TestClient, vault: Path) -> None:
    r = client.post(
        "/sisoul/snapshot/now",
        json={"vault_dir": str(vault), "upload": "both", "network": "mock"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "ok"
    assert data["ipfs_cid"].startswith("mockcid-")
    assert data["arweave_tx_id"].startswith("mocktx-")
    assert data["size_bytes"] > 0
    assert len(data["sha256"]) == 64


def test_post_now_vault_missing(client: TestClient, tmp_path: Path) -> None:
    r = client.post(
        "/sisoul/snapshot/now",
        json={"vault_dir": str(tmp_path / "noexist"), "network": "mock"},
    )
    assert r.status_code == 400


def test_post_now_invalid_upload(client: TestClient, vault: Path) -> None:
    r = client.post(
        "/sisoul/snapshot/now",
        json={"vault_dir": str(vault), "upload": "wrong", "network": "mock"},
    )
    assert r.status_code == 422  # pydantic Literal 验证


# ── /list ──────────────────────────────────────────────────────────────


def test_get_list_empty(client: TestClient) -> None:
    r = client.get("/sisoul/snapshot/list")
    assert r.status_code == 200
    assert r.json() == []


def test_get_list_after_now(client: TestClient, vault: Path) -> None:
    client.post(
        "/sisoul/snapshot/now",
        json={"vault_dir": str(vault), "upload": "ipfs", "network": "mock"},
    )
    r = client.get("/sisoul/snapshot/list")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["ipfs_cid"].startswith("mockcid-")


def test_get_list_limit(client: TestClient, vault: Path) -> None:
    for _ in range(3):
        client.post(
            "/sisoul/snapshot/now",
            json={"vault_dir": str(vault), "upload": "none", "network": "mock"},
        )
    r = client.get("/sisoul/snapshot/list?limit=2")
    assert r.status_code == 200
    assert len(r.json()) == 2


# ── /restore ──────────────────────────────────────────────────────────


def test_post_restore_unknown_hash_404(client: TestClient, tmp_path: Path) -> None:
    r = client.post(
        "/sisoul/snapshot/restore",
        json={
            "tx_id_or_cid": "a" * 64,
            "target_vault_dir": str(tmp_path / "target"),
            "source": "auto",
            "network": "mock",
        },
    )
    assert r.status_code == 404


def test_post_restore_target_exists_409(
    client: TestClient, vault: Path, tmp_path: Path
) -> None:
    # snapshot 拿到 hash
    r1 = client.post(
        "/sisoul/snapshot/now",
        json={"vault_dir": str(vault), "upload": "ipfs", "network": "mock"},
    )
    cid = r1.json()["ipfs_cid"]
    # 这是 mockcid → 真 restore 调 _fetch_ipfs 会抛 mockcid RuntimeError → 500
    # 我们换一个: 创个非空 target, 用任意 tx_id (会走 ar 路径前先撞 target 非空检查)
    target = tmp_path / "non-empty"
    target.mkdir()
    (target / "x").write_text("preexisting")
    r = client.post(
        "/sisoul/snapshot/restore",
        json={
            "tx_id_or_cid": "ar-real-tx-fake-not-mock",
            "target_vault_dir": str(target),
            "source": "arweave",
            "network": "mock",
        },
    )
    assert r.status_code == 409


# ── /schedule ─────────────────────────────────────────────────────────


def test_post_schedule_monthly_no_install(client: TestClient) -> None:
    r = client.post(
        "/sisoul/snapshot/schedule",
        json={"cadence": "monthly", "upload": "both", "install": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["cadence"] == "monthly"
    assert "unit_text" in data
    assert data["installed"] is False


def test_post_schedule_never(client: TestClient) -> None:
    r = client.post(
        "/sisoul/snapshot/schedule",
        json={"cadence": "never", "upload": "both"},
    )
    assert r.status_code == 200
    assert r.json()["installed"] is False


def test_post_schedule_invalid_cadence(client: TestClient) -> None:
    r = client.post(
        "/sisoul/snapshot/schedule",
        json={"cadence": "yearly", "upload": "both"},
    )
    assert r.status_code == 422


# ── /config ───────────────────────────────────────────────────────────


def test_get_config_default(client: TestClient) -> None:
    r = client.get("/sisoul/snapshot/config")
    assert r.status_code == 200
    data = r.json()
    assert data["pinata_jwt_configured"] is False
    assert data["arweave_allow_mainnet"] is False
    assert data["history_path"]  # 非空


def test_get_config_reflects_env(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PINATA_JWT", "x")
    monkeypatch.setenv("ARWEAVE_WALLET", "/tmp/wallet.json")
    monkeypatch.setenv("ARWEAVE_ALLOW_MAINNET", "1")
    r = client.get("/sisoul/snapshot/config")
    data = r.json()
    assert data["pinata_jwt_configured"] is True
    assert data["arweave_wallet_path"] == "/tmp/wallet.json"
    assert data["arweave_allow_mainnet"] is True


# ── router 命名规范 ──────────────────────────────────────────────────


def test_router_prefix_and_tags() -> None:
    assert snapshot_router.prefix == "/sisoul/snapshot"
    assert "snapshot" in snapshot_router.tags
