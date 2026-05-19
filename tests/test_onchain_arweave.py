"""tests · onchain.arweave (Phase 3 W41-W43 dev-C).

覆盖:
- ArweaveSnapshot 加密 + ZIP round-trip
- IPFS pin (mock + Pinata HTTP mock with httpx)
- Arweave upload (mock 模式 + 无 wallet fallback fake)
- SnapshotHistory load/append/find
- snapshot_now 流程 + history 落盘
- restore_from_arweave (本地 round-trip, 不真访问网)
- schedule_monthly_snapshot (launchd + systemd 文本生成 + install=True 写文件)
- mainnet gate (无 ARWEAVE_ALLOW_MAINNET 自动降 testnet)
- 反模式: 错 key 解密抛 RuntimeError; vault 不存在抛 FileNotFoundError
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from sisoul.identity.seed import (
    derive_subkey,
    generate_mnemonic,
    mnemonic_to_master_key,
)
from sisoul.onchain.arweave import (
    ArweaveSnapshot,
    SnapshotHistory,
    SnapshotRecord,
    _should_exclude,
    schedule_monthly_snapshot,
)


# ─────────────────────────────────────────────────────────────────────────
# fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def mnemonic() -> str:
    """合法 BIP-39 12 词."""
    return generate_mnemonic(strength=128)


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    """tmp vault, 含几个文件 + 一个排除 dir."""
    root = tmp_path / "vault"
    (root / "preferences").mkdir(parents=True)
    (root / "goals").mkdir()
    (root / "preferences" / "a.md").write_text("# pref a\nbody a\n", encoding="utf-8")
    (root / "preferences" / "b.md").write_text("# pref b\nbody b\n", encoding="utf-8")
    (root / "goals" / "g1.md").write_text("# goal 1\n", encoding="utf-8")
    (root / "dna.json").write_text(json.dumps({"did": "did:sisoul:test"}), encoding="utf-8")
    # 排除项
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "junk.pyc").write_text("junk")
    return root


@pytest.fixture()
def history_path(tmp_path: Path) -> Path:
    return tmp_path / "snapshot_history.json"


@pytest.fixture()
def client(
    mnemonic: str,
    history_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ArweaveSnapshot:
    # 默认 mock 网络 + 无 Pinata jwt → mockcid
    monkeypatch.delenv("PINATA_JWT", raising=False)
    monkeypatch.delenv("ARWEAVE_WALLET", raising=False)
    monkeypatch.delenv("ARWEAVE_ALLOW_MAINNET", raising=False)
    return ArweaveSnapshot(
        mnemonic=mnemonic,
        network="mock",
        history=SnapshotHistory(history_path),
    )


# ─────────────────────────────────────────────────────────────────────────
# _should_exclude
# ─────────────────────────────────────────────────────────────────────────


def test_should_exclude_patterns() -> None:
    assert _should_exclude(".git/HEAD") is True
    assert _should_exclude("foo/__pycache__/x.pyc") is True
    assert _should_exclude("foo/.DS_Store") is True
    assert _should_exclude("preferences/a.md") is False
    assert _should_exclude("vault/dna.json") is False


# ─────────────────────────────────────────────────────────────────────────
# snapshot_vault (加密 + ZIP)
# ─────────────────────────────────────────────────────────────────────────


def test_snapshot_vault_encrypts_and_zips(
    client: ArweaveSnapshot, vault_dir: Path
) -> None:
    blob, sha, key_fp = client.snapshot_vault(vault_dir)
    # 加密非空 + sha256 64 hex + key fingerprint 16 hex
    assert isinstance(blob, bytes) and len(blob) > 100
    assert len(sha) == 64
    assert all(c in "0123456789abcdef" for c in sha)
    assert len(key_fp) == 16
    # 校验 sha 真是 blob 的
    assert hashlib.sha256(blob).hexdigest() == sha


def test_snapshot_vault_missing_vault_raises(client: ArweaveSnapshot, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        client.snapshot_vault(tmp_path / "noexist")


def test_snapshot_vault_excludes_pycache(
    client: ArweaveSnapshot, vault_dir: Path, mnemonic: str
) -> None:
    """__pycache__/ 不能进 zip."""
    key = derive_subkey(mnemonic_to_master_key(mnemonic), "arweave", index=0)
    blob, _, _ = client.snapshot_vault(vault_dir, encryption_key=key)
    # 解密
    from sisoul.vault.encryption import decrypt_bytes

    plain = decrypt_bytes(blob, key)
    zf = zipfile.ZipFile(io.BytesIO(plain), "r")
    names = zf.namelist()
    assert any("vault/preferences/a.md" == n for n in names)
    assert "snapshot-meta.json" in names
    # __pycache__ 不应出现
    assert not any("__pycache__" in n for n in names), f"junk leaked: {names}"


# ─────────────────────────────────────────────────────────────────────────
# round-trip: snapshot → decrypt → unzip → 内容一致
# ─────────────────────────────────────────────────────────────────────────


def test_snapshot_roundtrip_decrypts_to_same_files(
    client: ArweaveSnapshot, vault_dir: Path, tmp_path: Path, mnemonic: str
) -> None:
    record = client.snapshot_now(vault_dir, upload="none")
    assert record.status == "ok"
    # 拿 blob 反推 (snapshot_now 没返 blob, 重新加密拿一次 — 同 mnemonic 同 key 决定性, ZIP 内容也决定性除时间戳)
    blob, _, _ = client.snapshot_vault(vault_dir)
    # 还原
    target = tmp_path / "restored"
    from sisoul.vault.encryption import decrypt_bytes

    key = derive_subkey(mnemonic_to_master_key(mnemonic), "arweave", index=0)
    plain = decrypt_bytes(blob, key)
    target.mkdir()
    with zipfile.ZipFile(io.BytesIO(plain), "r") as zf:
        zf.extractall(target)
    # 主文件存在
    assert (target / "vault" / "preferences" / "a.md").read_text() == "# pref a\nbody a\n"


# ─────────────────────────────────────────────────────────────────────────
# IPFS pin
# ─────────────────────────────────────────────────────────────────────────


def test_pin_to_ipfs_no_jwt_returns_mock(client: ArweaveSnapshot) -> None:
    cid = client.pin_to_ipfs(b"hello")
    assert cid is not None
    assert cid.startswith("mockcid-")


def test_pin_to_ipfs_with_pinata_jwt(mnemonic: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PINATA_JWT", "fakejwt123456789")
    monkeypatch.delenv("ARWEAVE_WALLET", raising=False)
    client = ArweaveSnapshot(mnemonic=mnemonic, network="mock")

    # mock httpx.Client.post → 返 Pinata-shape JSON
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"IpfsHash": "QmFake123", "PinSize": 100}
    fake_resp.raise_for_status = MagicMock()

    with patch("httpx.Client") as mc:
        instance = MagicMock()
        instance.post.return_value = fake_resp
        mc.return_value.__enter__.return_value = instance

        cid = client.pin_to_ipfs(b"data")
    assert cid == "QmFake123"


def test_pin_to_ipfs_http_error_returns_none(
    mnemonic: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PINATA_JWT", "fakejwt")
    client = ArweaveSnapshot(mnemonic=mnemonic, network="mock")
    with patch("httpx.Client") as mc:
        instance = MagicMock()
        instance.post.side_effect = httpx.HTTPError("net down")
        mc.return_value.__enter__.return_value = instance
        cid = client.pin_to_ipfs(b"data")
    assert cid is None


# ─────────────────────────────────────────────────────────────────────────
# Arweave upload
# ─────────────────────────────────────────────────────────────────────────


def test_upload_to_arweave_mock_network(client: ArweaveSnapshot) -> None:
    tx = client.upload_to_arweave(b"data")
    assert tx is not None and tx.startswith("mocktx-")


def test_upload_to_arweave_testnet_no_wallet_returns_fake(
    mnemonic: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARWEAVE_WALLET", raising=False)
    monkeypatch.delenv("PINATA_JWT", raising=False)
    client = ArweaveSnapshot(mnemonic=mnemonic, network="testnet")
    tx = client.upload_to_arweave(b"data")
    assert tx is not None and tx.startswith("testnet-fake-")


def test_mainnet_gated_without_env_flag_downgrades(
    mnemonic: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ARWEAVE_ALLOW_MAINNET", raising=False)
    client = ArweaveSnapshot(mnemonic=mnemonic, network="mainnet")
    assert client.network == "testnet"  # 自动降


def test_mainnet_allowed_with_env_flag(
    mnemonic: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARWEAVE_ALLOW_MAINNET", "1")
    client = ArweaveSnapshot(mnemonic=mnemonic, network="mainnet")
    assert client.network == "mainnet"
    assert client.gateway == "https://arweave.net"


# ─────────────────────────────────────────────────────────────────────────
# SnapshotHistory
# ─────────────────────────────────────────────────────────────────────────


def test_history_roundtrip(history_path: Path) -> None:
    h = SnapshotHistory(history_path)
    assert h.load() == []
    rec = SnapshotRecord(
        timestamp="2026-05-18T00:00:00+00:00",
        size_bytes=1024,
        sha256="a" * 64,
        ipfs_cid="Qm1",
        arweave_tx_id="tx1",
        vault_master_key_fingerprint="ff" * 8,
        network="mock",
        status="ok",
    )
    h.append(rec)
    loaded = h.load()
    assert len(loaded) == 1 and loaded[0].sha256 == "a" * 64


def test_history_find_by_any_id(history_path: Path) -> None:
    h = SnapshotHistory(history_path)
    h.append(SnapshotRecord(
        timestamp="t", size_bytes=10, sha256="abc",
        ipfs_cid="Qm1", arweave_tx_id="tx1",
    ))
    assert h.find("tx1") is not None
    assert h.find("Qm1") is not None
    assert h.find("abc") is not None
    assert h.find("none") is None


def test_history_corrupted_file_returns_empty(history_path: Path) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text("not json {{{", encoding="utf-8")
    h = SnapshotHistory(history_path)
    assert h.load() == []


# ─────────────────────────────────────────────────────────────────────────
# snapshot_now 主流程
# ─────────────────────────────────────────────────────────────────────────


def test_snapshot_now_both_writes_history(
    client: ArweaveSnapshot, vault_dir: Path
) -> None:
    record = client.snapshot_now(vault_dir, upload="both")
    assert record.status == "ok"
    assert record.ipfs_cid and record.ipfs_cid.startswith("mockcid-")
    assert record.arweave_tx_id and record.arweave_tx_id.startswith("mocktx-")
    # 落到 history
    loaded = client.history.load()
    assert len(loaded) == 1
    assert loaded[0].sha256 == record.sha256


def test_snapshot_now_ipfs_only(client: ArweaveSnapshot, vault_dir: Path) -> None:
    record = client.snapshot_now(vault_dir, upload="ipfs")
    assert record.ipfs_cid is not None
    assert record.arweave_tx_id is None


def test_snapshot_now_none_just_zips(client: ArweaveSnapshot, vault_dir: Path) -> None:
    record = client.snapshot_now(vault_dir, upload="none")
    assert record.ipfs_cid is None
    assert record.arweave_tx_id is None
    assert record.size_bytes > 0


# ─────────────────────────────────────────────────────────────────────────
# restore (本地 round-trip · 不访问网)
# ─────────────────────────────────────────────────────────────────────────


def test_restore_roundtrip_local(
    client: ArweaveSnapshot, vault_dir: Path, tmp_path: Path
) -> None:
    """模拟: snapshot 得到 blob → mock IPFS gateway 返这个 blob → restore 出文件一致."""
    blob, sha, _ = client.snapshot_vault(vault_dir)

    # mock _fetch_ipfs / _fetch_arweave 都返 blob
    client._fetch_ipfs = lambda cid: blob  # type: ignore[method-assign]
    client._fetch_arweave = lambda tx: blob  # type: ignore[method-assign]

    target = tmp_path / "restored"
    out = client.restore_from_arweave("Qmtestfake", target_vault_dir=target, source="ipfs")
    assert out == target.resolve()
    assert (target / "preferences" / "a.md").read_text() == "# pref a\nbody a\n"
    assert (target / "dna.json").exists()


def test_restore_wrong_key_raises(
    client: ArweaveSnapshot, vault_dir: Path, tmp_path: Path, mnemonic: str
) -> None:
    blob, _, _ = client.snapshot_vault(vault_dir)

    # 换一个 mnemonic 当解密 key
    other = generate_mnemonic()
    while other == mnemonic:
        other = generate_mnemonic()
    other_client = ArweaveSnapshot(
        mnemonic=other,
        network="mock",
        history=SnapshotHistory(tmp_path / "other.json"),
    )
    other_client._fetch_ipfs = lambda cid: blob  # type: ignore[method-assign]
    target = tmp_path / "restored-bad"
    with pytest.raises(RuntimeError, match="解密失败"):
        other_client.restore_from_arweave("Qmfake", target_vault_dir=target, source="ipfs")


def test_restore_target_non_empty_raises(
    client: ArweaveSnapshot, tmp_path: Path
) -> None:
    target = tmp_path / "non-empty"
    target.mkdir()
    (target / "preexisting").write_text("x")
    with pytest.raises(FileExistsError):
        client.restore_from_arweave("anything", target_vault_dir=target)


def test_restore_auto_source_detection(
    client: ArweaveSnapshot, vault_dir: Path, tmp_path: Path
) -> None:
    blob, _, _ = client.snapshot_vault(vault_dir)
    captured_source: list[str] = []

    def fake_ipfs(cid: str) -> bytes:
        captured_source.append("ipfs")
        return blob

    def fake_ar(tx: str) -> bytes:
        captured_source.append("arweave")
        return blob

    client._fetch_ipfs = fake_ipfs  # type: ignore[method-assign]
    client._fetch_arweave = fake_ar  # type: ignore[method-assign]

    # CID 前缀 Qm → ipfs
    client.restore_from_arweave("QmAuto", target_vault_dir=tmp_path / "r1", source="auto")
    # 非 Qm/bafy → arweave
    client.restore_from_arweave(
        "ar-tx-no-prefix-123", target_vault_dir=tmp_path / "r2", source="auto"
    )
    assert captured_source == ["ipfs", "arweave"]


def test_restore_mock_cid_refuses_real_fetch(
    client: ArweaveSnapshot, tmp_path: Path
) -> None:
    """mockcid 不应该真去网取 (避免 mock 测试意外打真 gateway)."""
    with pytest.raises(RuntimeError, match="mockcid"):
        client._fetch_ipfs("mockcid-abc")


# ─────────────────────────────────────────────────────────────────────────
# 派生 key: 同 mnemonic 决定性, 不同 mnemonic 不一样
# ─────────────────────────────────────────────────────────────────────────


def test_derive_key_deterministic(mnemonic: str) -> None:
    c1 = ArweaveSnapshot(mnemonic=mnemonic, network="mock")
    c2 = ArweaveSnapshot(mnemonic=mnemonic, network="mock")
    assert c1._derive_encryption_key() == c2._derive_encryption_key()


def test_derive_key_different_mnemonic(mnemonic: str) -> None:
    other = generate_mnemonic()
    while other == mnemonic:
        other = generate_mnemonic()
    c1 = ArweaveSnapshot(mnemonic=mnemonic, network="mock")
    c2 = ArweaveSnapshot(mnemonic=other, network="mock")
    assert c1._derive_encryption_key() != c2._derive_encryption_key()


def test_derive_key_no_seed_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SISOUL_MNEMONIC", raising=False)
    # 把 SISOUL_SEED_FILE 指向不存在的 path, 避免 fallback ~/.sisoul/seed.txt
    monkeypatch.setenv("SISOUL_SEED_FILE", str(tmp_path / "noexist.txt"))
    client = ArweaveSnapshot(network="mock")
    # 但 ArweaveSnapshot._derive_encryption_key 直接用 load_mnemonic_from_file 的 DEFAULT
    # 测试: monkey patch 它
    import sisoul.onchain.arweave as mod

    def boom(_=None) -> str:  # noqa: ANN001
        raise FileNotFoundError("no seed")

    monkeypatch.setattr(mod, "load_mnemonic_from_file", boom)
    with pytest.raises(RuntimeError, match="无 seed"):
        client._derive_encryption_key()


def test_derive_key_invalid_mnemonic_raises() -> None:
    client = ArweaveSnapshot(mnemonic="not a real bip39 mnemonic at all", network="mock")
    with pytest.raises(ValueError, match="BIP-39"):
        client._derive_encryption_key()


# ─────────────────────────────────────────────────────────────────────────
# schedule_monthly_snapshot
# ─────────────────────────────────────────────────────────────────────────


def test_schedule_never_returns_placeholder(tmp_path: Path) -> None:
    r = schedule_monthly_snapshot(cadence="never", install=True, target_dir=tmp_path)
    assert r["installed"] is False
    assert "never" in r["unit_text"]


def test_schedule_darwin_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    r = schedule_monthly_snapshot(cadence="monthly", upload="both", install=False)
    assert r["system"] == "darwin"
    assert "Label" in r["unit_text"]
    assert "io.sisoul.snapshot.monthly" in r["unit_text"]
    assert "snapshot" in r["unit_text"] and "now" in r["unit_text"]


def test_schedule_linux_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")
    r = schedule_monthly_snapshot(cadence="weekly", upload="ipfs", install=False)
    assert r["system"] == "linux"
    assert "OnCalendar=weekly" in r["unit_text"]
    assert "--upload ipfs" in r["unit_text"]


def test_schedule_install_writes_file_darwin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    r = schedule_monthly_snapshot(
        cadence="monthly", upload="both", install=True, target_dir=tmp_path
    )
    assert r["installed"] is True
    p = r["install_path"]
    assert Path(p).exists()
    assert "io.sisoul.snapshot.monthly" in Path(p).read_text()


def test_schedule_install_writes_file_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")
    r = schedule_monthly_snapshot(
        cadence="daily", upload="arweave", install=True, target_dir=tmp_path
    )
    assert r["installed"] is True
    assert (tmp_path / "sisoul-snapshot.service").exists()
    assert (tmp_path / "sisoul-snapshot.timer").exists()


def test_schedule_unsupported_system(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Solaris")
    r = schedule_monthly_snapshot(cadence="monthly", install=False)
    assert r["system"] == "unsupported"
