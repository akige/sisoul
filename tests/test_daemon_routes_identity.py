"""tests for sisoul.daemon_routes.identity FastAPI router (Phase 2 W17-W20, 波 3 dev-A).

不依赖主 daemon.py, 自己 mount router 跑 TestClient.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sisoul.daemon_routes.identity import identity_router
from sisoul.identity import generate_mnemonic, save_mnemonic_to_file


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(identity_router)
    return TestClient(app)


# ── GET /sisoul/identity ─────────────────────────────────────────────────────


def test_get_identity_no_seed(client: TestClient, tmp_path: Path) -> None:
    """vault_dir 无 seed.txt → has_seed=False, 不报错."""
    resp = client.get("/sisoul/identity", params={"vault_dir": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_seed"] is False
    assert body["seed_path"] is None
    assert body["master_key_fingerprint"] is None


def test_get_identity_with_seed(client: TestClient, tmp_path: Path) -> None:
    m = generate_mnemonic()
    seed_file = tmp_path / "seed.txt"
    save_mnemonic_to_file(m, seed_file)
    resp = client.get("/sisoul/identity", params={"vault_dir": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_seed"] is True
    assert body["seed_path"] == str(seed_file)
    assert len(body["master_key_fingerprint"]) == 16
    assert body["seed_word_count"] == 12


def test_get_identity_corrupted_seed(client: TestClient, tmp_path: Path) -> None:
    seed_file = tmp_path / "seed.txt"
    seed_file.write_text("garbage not bip39\n", encoding="utf-8")
    seed_file.chmod(0o600)
    resp = client.get("/sisoul/identity", params={"vault_dir": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_seed"] is False
    assert body["error"] is not None


def test_get_identity_loose_permissions_error(client: TestClient, tmp_path: Path) -> None:
    m = generate_mnemonic()
    seed_file = tmp_path / "seed.txt"
    save_mnemonic_to_file(m, seed_file)
    seed_file.chmod(0o644)
    resp = client.get("/sisoul/identity", params={"vault_dir": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_seed"] is False
    assert "权限" in (body["error"] or "")


# ── POST /sisoul/restore-seed ────────────────────────────────────────────────


def test_post_restore_seed_creates_vault(client: TestClient, tmp_path: Path) -> None:
    m = generate_mnemonic()
    vault_dir = tmp_path / "new-vault"
    resp = client.post(
        "/sisoul/restore-seed",
        json={"seed": m, "vault_dir": str(vault_dir), "force": False},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["vault_dir"] == str(vault_dir)
    assert len(body["master_key_fingerprint"]) == 16
    assert (vault_dir / "dna.json").exists()
    assert (vault_dir / "seed.txt").exists()
    assert stat.S_IMODE((vault_dir / "seed.txt").stat().st_mode) == 0o600


def test_post_restore_seed_invalid_400(client: TestClient, tmp_path: Path) -> None:
    resp = client.post(
        "/sisoul/restore-seed",
        json={
            "seed": "bogus bogus bogus bogus bogus bogus bogus bogus bogus bogus bogus bogus",
            "vault_dir": str(tmp_path / "vault"),
        },
    )
    assert resp.status_code == 400
    assert "BIP-39" in resp.text or "mnemonic" in resp.text.lower()


def test_post_restore_seed_existing_vault_409(client: TestClient, tmp_path: Path) -> None:
    m = generate_mnemonic()
    vault_dir = tmp_path / "vault"
    # 先建一个 vault
    r1 = client.post(
        "/sisoul/restore-seed",
        json={"seed": m, "vault_dir": str(vault_dir), "force": False},
    )
    assert r1.status_code == 201

    # 再 POST 不 force → 409
    r2 = client.post(
        "/sisoul/restore-seed",
        json={"seed": m, "vault_dir": str(vault_dir), "force": False},
    )
    assert r2.status_code == 409


def test_post_restore_seed_force_overwrites(client: TestClient, tmp_path: Path) -> None:
    m1 = generate_mnemonic()
    vault_dir = tmp_path / "vault"
    r1 = client.post(
        "/sisoul/restore-seed",
        json={"seed": m1, "vault_dir": str(vault_dir)},
    )
    assert r1.status_code == 201

    m2 = generate_mnemonic()
    r2 = client.post(
        "/sisoul/restore-seed",
        json={"seed": m2, "vault_dir": str(vault_dir), "force": True},
    )
    assert r2.status_code == 201
    # 新 fingerprint ≠ 旧
    assert r2.json()["master_key_fingerprint"] != r1.json()["master_key_fingerprint"]


def test_post_restore_seed_missing_seed_422(client: TestClient, tmp_path: Path) -> None:
    """缺必填 seed 字段 → FastAPI 422 validation error."""
    resp = client.post(
        "/sisoul/restore-seed",
        json={"vault_dir": str(tmp_path / "v")},
    )
    assert resp.status_code == 422


# ── round trip GET after POST ────────────────────────────────────────────────


def test_post_then_get_consistent_fingerprint(client: TestClient, tmp_path: Path) -> None:
    m = generate_mnemonic()
    vault_dir = tmp_path / "v"
    post = client.post(
        "/sisoul/restore-seed",
        json={"seed": m, "vault_dir": str(vault_dir)},
    )
    assert post.status_code == 201
    get = client.get("/sisoul/identity", params={"vault_dir": str(vault_dir)})
    assert get.status_code == 200
    assert get.json()["master_key_fingerprint"] == post.json()["master_key_fingerprint"]
