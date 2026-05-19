"""波 4 qa-D · 反向验证 (§J-2 #3 · 4 反模式自检).

测试范畴 (3 项):
    1. P2P node 启动失败 (端口占用 / seed.txt 不存在) → graceful 报错, 不污染全局
    2. EAS RPC 断 → upload_batch 应 fail-open 走 mock + queue 不丢
    3. Arweave 上传失败 mock + 用户错 mainnet flag → 双重 gate 应硬阻断

设计: 这些 case 是 dev 易 "patch 告警而非根因" 的高发场景 (反模式 #3).
"""
from __future__ import annotations

import os
import socket
import time
from pathlib import Path

import pytest

from sisoul.identity.seed import generate_mnemonic, save_mnemonic_to_file
from sisoul.onchain.arweave import ArweaveSnapshot, SnapshotHistory
from sisoul.onchain.eas import (
    AttestConfig,
    AttestQueue,
    AuditAttestation,
    EASError,
    NetworkNotSupportedError,
    upload_batch,
)
from sisoul.p2p.node import SisoulP2PNode


# ---------------------------------------------------------------------------
# 1. P2P graceful failure modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p2p_start_without_seed_raises_clear_error(tmp_path: Path) -> None:
    """vault 无 seed.txt → start raise FileNotFoundError 含 'sisoul init' 提示, 不污染全局."""
    vault = tmp_path / "no-seed-vault"
    vault.mkdir()
    node = SisoulP2PNode(vault_dir=vault, transport_prefer="inmem")
    with pytest.raises(FileNotFoundError, match="sisoul init|seed"):
        await node.start(port=0)


@pytest.mark.asyncio
async def test_p2p_start_with_bad_seed_raises(tmp_path: Path) -> None:
    """seed.txt 内容非法 → start 真验签失败, raise (RuntimeError / PermissionError / ValueError), 不静默走 None key."""
    vault = tmp_path / "bad-seed"
    vault.mkdir()
    seed_path = vault / "seed.txt"
    seed_path.write_text("not a real mnemonic\n", encoding="utf-8")
    # 把权限改 0600 防 dev-A 的硬规定 PermissionError 抢先 (这测试针对 mnemonic 校验)
    seed_path.chmod(0o600)
    node = SisoulP2PNode(vault_dir=vault, transport_prefer="inmem")
    with pytest.raises((RuntimeError, ValueError, PermissionError)):
        await node.start(port=0)


@pytest.mark.asyncio
async def test_p2p_double_start_blocked(tmp_path: Path) -> None:
    """已 start 再 start → RuntimeError, 不静默替换 transport (防资源泄漏)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    save_mnemonic_to_file(generate_mnemonic(), vault / "seed.txt")
    node = SisoulP2PNode(vault_dir=vault, transport_prefer="inmem")
    try:
        await node.start(port=0)
        with pytest.raises(RuntimeError, match="running"):
            await node.start(port=0)
    finally:
        await node.stop()


@pytest.mark.asyncio
async def test_p2p_stop_idempotent_when_not_running(tmp_path: Path) -> None:
    """未 start 直接 stop → 不 raise (idempotent)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    save_mnemonic_to_file(generate_mnemonic(), vault / "seed.txt")
    node = SisoulP2PNode(vault_dir=vault, transport_prefer="inmem")
    # 没 start, 直接 stop, 不该 raise
    await node.stop()


@pytest.mark.asyncio
async def test_p2p_sync_with_unknown_peer_returns_failure_not_crash(tmp_path: Path) -> None:
    """sync_with 不存在 peer_id → 返 ok=False (graceful) 不 crash."""
    vault = tmp_path / "v"
    vault.mkdir()
    save_mnemonic_to_file(generate_mnemonic(), vault / "seed.txt")
    node = SisoulP2PNode(vault_dir=vault, transport_prefer="inmem")
    try:
        await node.start(port=0)
        r = await node.sync_with("non-existent-peer-id", timeout=1.0)
        assert r.get("ok") is False, f"未知 peer 应 ok=False: {r}"
    finally:
        await node.stop()


# ---------------------------------------------------------------------------
# 2. EAS RPC 断 / queue 保留 + retry
# ---------------------------------------------------------------------------


def _make_audit(idx: int) -> AuditAttestation:
    import hashlib
    ph = hashlib.sha256(f"audit-{idx}".encode()).hexdigest()
    return AuditAttestation(
        actor_did="did:sisoul:qa",
        action_type="edit",
        target=f"/tmp/x{idx}",
        prompt_hash="0x" + ph,
        timestamp=int(time.time()) + idx,
        tool_name="qa-reverse",
    )


def test_eas_rpc_down_falls_back_to_mock_not_lose_queue(tmp_path: Path) -> None:
    """RPC 不可达 (假 URL) → upload_batch fail-open 走 mock + queue items 仍标 confirmed.

    设计: §K-NN fail-open 原则; 不让 RPC SPOF 让 audit 全卡死. 但 method 标 mock 防误报"已上链".
    """
    db = tmp_path / "q.db"
    q = AttestQueue(db_path=db)
    q.enqueue(_make_audit(0))
    q.enqueue(_make_audit(1))

    cfg = AttestConfig(
        network="optimism-sepolia",
        rpc_url="https://this-rpc-does-not-exist-1234567.invalid",
        batch_size=10,
    )
    batch = upload_batch(q, cfg)
    # fail-open: 当 RPC 不通, method 退回 "mock" (不假装 live)
    assert batch.method == "mock", f"RPC 断应降级 mock, 实 method={batch.method}"
    assert batch.count == 2
    # queue 真转移, 不丢
    assert len(q.pending()) == 0
    confirmed = q.all_items(status="confirmed", limit=10)
    assert len(confirmed) == 2


def test_eas_mainnet_hard_block(tmp_path: Path) -> None:
    """反模式 #3 防御: mainnet 不许走, 必须 NetworkNotSupportedError, 不能"patch 告警"放行."""
    q = AttestQueue(db_path=tmp_path / "q.db")
    q.enqueue(_make_audit(0))
    cfg = AttestConfig(network="optimism-mainnet")
    with pytest.raises(NetworkNotSupportedError):
        upload_batch(q, cfg)
    # queue 保留 pending (上链失败不该清 queue)
    assert len(q.pending()) == 1


def test_eas_live_tx_path_aborts_when_no_real_signing(tmp_path: Path) -> None:
    """live-tx 路径有 private_key_path 但波 4 约束 readonly → 必 raise (EASError abort / 解 key 失败), 不静默 mock 假装上链.

    任一 raise 均合规 (核心: 不能假装上链, 不能默 mock); 实测 dev-B 代码先解 key 再 abort,
    我们接受其中一种 (eth_account ValueError / EASError 'readonly|live-tx').
    """
    q = AttestQueue(db_path=tmp_path / "q.db")
    q.enqueue(_make_audit(0))
    # 真合法 hex private key (32 字节) 让 eth_account.from_key OK, 真走到 dev-B 的 abort
    fake_key = tmp_path / "fake.hex"
    fake_key.write_text("0x" + "11" * 32, encoding="utf-8")
    cfg = AttestConfig(
        network="optimism-sepolia",
        rpc_url="https://sepolia.optimism.io",
        private_key_path=str(fake_key),
    )
    with pytest.raises(EASError):
        upload_batch(q, cfg)


# ---------------------------------------------------------------------------
# 3. Arweave + IPFS 失败 + mainnet 双 gate
# ---------------------------------------------------------------------------


def test_arweave_mainnet_double_gate_blocks_without_env(tmp_path: Path) -> None:
    """mainnet + 无 ARWEAVE_ALLOW_MAINNET=1 → 降级 testnet (不假装上 mainnet)."""
    os.environ.pop("ARWEAVE_ALLOW_MAINNET", None)
    mnemonic = generate_mnemonic()
    snap = ArweaveSnapshot(mnemonic=mnemonic, network="mainnet")
    assert snap.network == "testnet", \
        f"mainnet 无 env → 必须降 testnet, 实 {snap.network}"


def test_arweave_no_jwt_falls_back_mockcid_with_warning(tmp_path: Path) -> None:
    """无 PINATA_JWT → pin_to_ipfs 返 mockcid- 前缀 (不假装真上传, 不静默 None)."""
    mnemonic = generate_mnemonic()
    # 确保 env 没 jwt
    os.environ.pop("PINATA_JWT", None)
    snap = ArweaveSnapshot(mnemonic=mnemonic, network="mock")
    cid = snap.pin_to_ipfs(b"fake blob bytes here")
    assert cid is not None
    assert cid.startswith("mockcid-"), f"无 jwt 应 mock CID, 实 {cid}"


def test_arweave_mockcid_fetch_raises_not_silently_returns_garbage(tmp_path: Path) -> None:
    """mockcid 真拉 → RuntimeError, 不静默返空/破数据 (sanity check §J-2 #4)."""
    mnemonic = generate_mnemonic()
    snap = ArweaveSnapshot(mnemonic=mnemonic, network="mock")
    with pytest.raises(RuntimeError, match="mockcid|mock"):
        snap._fetch_ipfs("mockcid-deadbeef")


def test_arweave_fake_tx_fetch_raises(tmp_path: Path) -> None:
    """mocktx- / testnet-fake- 等 fake tx_id 真拉 → RuntimeError, 不打真 gateway."""
    mnemonic = generate_mnemonic()
    snap = ArweaveSnapshot(mnemonic=mnemonic, network="testnet")
    with pytest.raises(RuntimeError):
        snap._fetch_arweave("mocktx-fakefake")


def test_arweave_restore_target_must_be_empty(tmp_path: Path) -> None:
    """restore target_vault_dir 非空 → FileExistsError, 不静默覆盖用户数据."""
    mnemonic = generate_mnemonic()
    snap = ArweaveSnapshot(mnemonic=mnemonic, network="mock")
    target = tmp_path / "existing"
    target.mkdir()
    (target / "user-data.md").write_text("用户已有数据 - 不该被覆盖", encoding="utf-8")
    with pytest.raises(FileExistsError):
        snap.restore_from_arweave("any-tx-id", target)


def test_arweave_snapshot_now_failed_status_when_pin_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模拟 pin_to_ipfs 返 None → record.status = 'failed', 不静默 ok."""
    mnemonic = generate_mnemonic()
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "x.md").write_text("# x\n", encoding="utf-8")
    hist = SnapshotHistory(path=tmp_path / "h.json")
    snap = ArweaveSnapshot(mnemonic=mnemonic, network="mock", history=hist)
    # 强制 pin_to_ipfs 返 None (模拟 Pinata 失败)
    monkeypatch.setattr(snap, "pin_to_ipfs", lambda *a, **k: None)
    rec = snap.snapshot_now(vault_dir=vault, upload="ipfs")
    assert rec.status == "failed", f"pin 失败应 status=failed, 实 {rec.status}"
    assert rec.error and "ipfs" in rec.error
    # history 仍记录 (审计完整性)
    assert len(hist.load()) == 1
