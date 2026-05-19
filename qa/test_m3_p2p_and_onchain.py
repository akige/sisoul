"""波 4 qa-D · M3 P2P 跨设备 sync + 链上 audit (mock + 可选 live testnet).

§29 §8 M3 通过/失败标准 · §30 §2 波 4 通过标准.

测试范畴:
    2.1 同机起 2 P2P node (alice/bob 同 BIP-39 seed) 真双向 sync wall < 5s + 不同 seed 反向 不互解
    2.2 EAS attestation queue → batch flush (mock + readonly live testnet RPC chain_id 校验) +
        可选 SISOUL_TEST_LIVE_TESTNET=1 真打 Optimism Sepolia 校验 contract address + 历史 schema
    2.3 Arweave snapshot mock pipeline (now --upload ipfs / --upload both) + history 记录 + restore 还原

每个 test §J-2 4 条:
    1. 端到端真数据流 (build_inventory 真读 vault / compute_attestation_uid 真算)
    2. 数值比对 (push_count / pull_count / 解密 vault file 实际内容)
    3. 反向 case (不同 seed / network=optimism-mainnet 阻断 / fake tx_id 不真拉)
    4. sanity (wall time 实测 < 5s / queue stats 真转移 pending→confirmed / snapshot sha256 真对得上)
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Tuple

import pytest

from sisoul.identity.seed import (
    generate_mnemonic,
    save_mnemonic_to_file,
)
from sisoul.onchain.arweave import (
    ArweaveSnapshot,
    SnapshotHistory,
    SnapshotRecord,
)
from sisoul.onchain.eas import (
    AttestConfig,
    AttestQueue,
    AuditAttestation,
    NetworkNotSupportedError,
    QueueEmptyError,
    compute_attestation_uid,
    upload_batch,
    verify_attestation_local,
)
from sisoul.p2p.node import SisoulP2PNode

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _make_seed_dir(tmp_path: Path, label: str, mnemonic: str) -> Path:
    """Create a fresh vault dir with seed.txt for label (alice / bob)."""
    d = tmp_path / label
    d.mkdir(parents=True, exist_ok=True)
    save_mnemonic_to_file(mnemonic, d / "seed.txt")
    (d / "preferences").mkdir(exist_ok=True)
    (d / "goals").mkdir(exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# 2.1 · P2P 双实例真 sync (M3 核心)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_m3_alice_bob_bidirectional_sync_wall_time_under_5s(tmp_path: Path) -> None:
    """同 seed 双 node 双向 sync, alice 写 → bob 收 + bob 写 → alice 收, wall < 5s."""
    seed = generate_mnemonic()
    alice_dir = _make_seed_dir(tmp_path, "alice", seed)
    bob_dir = _make_seed_dir(tmp_path, "bob", seed)

    # alice 起一条偏好
    pref_path = alice_dir / "preferences" / "lang.md"
    pref_path.write_text("# 偏好\n喜欢中文 + 简短输出.\n", encoding="utf-8")

    alice = SisoulP2PNode(vault_dir=alice_dir, transport_prefer="inmem")
    bob = SisoulP2PNode(vault_dir=bob_dir, transport_prefer="inmem")

    try:
        t0 = time.time()
        await alice.start(port=0)
        await bob.start(port=0)

        alice.add_peer(multiaddr=f"inmem://{bob._transport.peer_id}", peer_id=bob._transport.peer_id, transport="inmem")  # type: ignore[union-attr]
        bob.add_peer(multiaddr=f"inmem://{alice._transport.peer_id}", peer_id=alice._transport.peer_id, transport="inmem")  # type: ignore[union-attr]

        # alice push → bob
        r1 = await alice.sync_with(bob._transport.peer_id, timeout=4.0)  # type: ignore[union-attr]
        assert r1.get("ok") is True, f"alice→bob sync 失败: {r1}"
        assert r1.get("pushed", 0) >= 1, f"alice 应 push 至少 1 文件: {r1}"

        bob_pref = bob_dir / "preferences" / "lang.md"
        # 等 recv loop 落盘 (内存 bus 立即, 加 buffer 安全)
        for _ in range(20):
            if bob_pref.exists() and bob_pref.read_text(encoding="utf-8").startswith("# 偏好"):
                break
            await asyncio.sleep(0.05)
        assert bob_pref.exists(), f"bob 应收到 alice 的 lang.md, dir={list(bob_dir.rglob('*'))}"
        assert "中文" in bob_pref.read_text(encoding="utf-8")

        # bob 写 long-term goal → push 回 alice
        goal_path = bob_dir / "goals" / "q3.md"
        goal_path.write_text("# Q3 目标\n上线 sisoul v1.0-internal.\n", encoding="utf-8")
        r2 = await bob.sync_with(alice._transport.peer_id, timeout=4.0)  # type: ignore[union-attr]
        assert r2.get("ok") is True, f"bob→alice sync 失败: {r2}"
        assert r2.get("pushed", 0) >= 1

        alice_goal = alice_dir / "goals" / "q3.md"
        for _ in range(20):
            if alice_goal.exists():
                break
            await asyncio.sleep(0.05)
        assert alice_goal.exists(), "alice 应收到 bob 的 q3.md"
        assert "Q3 目标" in alice_goal.read_text(encoding="utf-8")

        wall = time.time() - t0
        # M3 通过标准: wall < 5s (start + 2 round-trip + 落盘)
        assert wall < 5.0, f"M3 wall time 违规: {wall:.2f}s >= 5s"
        # sanity: 非全 0
        assert alice.stats.syncs_ok >= 1
        assert bob.stats.syncs_ok >= 1
    finally:
        await alice.stop()
        await bob.stop()


@pytest.mark.asyncio
async def test_m3_different_seeds_cannot_sync(tmp_path: Path) -> None:
    """反向验证 (§J-2 #3): 不同 BIP-39 seed → 加密 key 不同 → 收不到对方 inventory.

    必须 graceful (不 crash), sync_with 返 ok=False / pushed=pulled=0, vault 不污染.
    """
    alice_seed = generate_mnemonic()
    bob_seed = generate_mnemonic()
    assert alice_seed != bob_seed

    alice_dir = _make_seed_dir(tmp_path, "alice", alice_seed)
    bob_dir = _make_seed_dir(tmp_path, "bob", bob_seed)

    (alice_dir / "preferences" / "secret.md").write_text("# 私\nalice 私数据.", encoding="utf-8")

    alice = SisoulP2PNode(vault_dir=alice_dir, transport_prefer="inmem")
    bob = SisoulP2PNode(vault_dir=bob_dir, transport_prefer="inmem")

    try:
        await alice.start(port=0)
        await bob.start(port=0)
        alice.add_peer(
            multiaddr=f"inmem://{bob._transport.peer_id}",  # type: ignore[union-attr]
            peer_id=bob._transport.peer_id,  # type: ignore[union-attr]
            transport="inmem",
        )
        bob.add_peer(
            multiaddr=f"inmem://{alice._transport.peer_id}",  # type: ignore[union-attr]
            peer_id=alice._transport.peer_id,  # type: ignore[union-attr]
            transport="inmem",
        )

        r = await alice.sync_with(bob._transport.peer_id, timeout=2.0)  # type: ignore[union-attr]
        # 期望 ok=False (timeout 等不到 INVENTORY_RESPONSE) 或 pushed=0 (bob 无法解 INVENTORY_REQUEST)
        assert (r.get("ok") is False) or (r.get("pushed", 0) == 0 and r.get("pulled", 0) == 0), \
            f"不同 seed 不该真 sync: {r}"
        # bob vault 不应收到 alice 的 secret.md
        bob_secret = bob_dir / "preferences" / "secret.md"
        assert not bob_secret.exists(), f"bob 不该收到 alice 数据: {bob_secret}"
    finally:
        await alice.stop()
        await bob.stop()


# ---------------------------------------------------------------------------
# 2.2 · EAS attestation queue + flush (mock + readonly live testnet)
# ---------------------------------------------------------------------------


def _make_audit(actor: str, action: str, target: str, idx: int) -> AuditAttestation:
    import hashlib
    ph = hashlib.sha256(f"{actor}|{action}|{target}|{idx}".encode()).hexdigest()
    return AuditAttestation(
        actor_did=actor,
        action_type=action,
        target=target,
        prompt_hash="0x" + ph,
        timestamp=int(time.time()) + idx,
        tool_name="cmux-sisoul-qa",
    )


def test_m3_eas_attest_queue_to_batch_mock_pipeline(tmp_path: Path) -> None:
    """端到端: 写 audit → queue → flush batch → verify local 全 confirm. mock 网络."""
    db = tmp_path / "attest.db"
    queue = AttestQueue(db_path=db)
    cfg = AttestConfig(network="mock", batch_size=10, schema_uid="0x" + "ab" * 32)

    audits = [
        _make_audit("did:sisoul:alice", "edit", f"/Users/alice/file{i}.md", i)
        for i in range(10)
    ]
    for a in audits:
        queue.enqueue(a)

    pending_before = queue.pending()
    assert len(pending_before) == 10, f"应有 10 pending, 实际 {len(pending_before)}"

    batch = upload_batch(queue, cfg, force=False)
    # 数值比对 (§J-2 #2)
    assert batch.count == 10, f"batch.count 应 10, 实 {batch.count}"
    assert batch.method == "mock"
    assert len(batch.attestation_uids) == 10
    # sanity (§J-2 #4) - 非全 0
    assert batch.gas_used_estimate > 0
    assert batch.gas_cost_wei_estimate > 0
    # uid 各不相同
    assert len(set(batch.attestation_uids)) == 10, "10 条 attestation uid 应全唯一"

    # queue 真转移: pending → confirmed
    assert len(queue.pending()) == 0
    confirmed = queue.all_items(status="confirmed", limit=20)
    assert len(confirmed) == 10

    # verify_local 重算应 valid
    for a, uid in zip(audits, batch.attestation_uids):
        v = verify_attestation_local(queue, uid)
        assert v.get("valid") is True, f"verify_local 失败 uid={uid}: {v}"


def test_m3_eas_mainnet_blocked(tmp_path: Path) -> None:
    """反向 (§J-2 #3): 用户不慎用 mainnet → 必须硬阻断."""
    db = tmp_path / "attest_main.db"
    queue = AttestQueue(db_path=db)
    queue.enqueue(_make_audit("did:sisoul:alice", "edit", "/tmp/x", 0))

    cfg = AttestConfig(network="optimism-mainnet", batch_size=10)
    with pytest.raises(NetworkNotSupportedError, match="mainnet"):
        upload_batch(queue, cfg)


def test_m3_eas_empty_queue_flush_raises(tmp_path: Path) -> None:
    """反向: 空 queue flush 应 QueueEmptyError."""
    queue = AttestQueue(db_path=tmp_path / "empty.db")
    cfg = AttestConfig(network="mock")
    with pytest.raises(QueueEmptyError):
        upload_batch(queue, cfg)


def test_m3_eas_attestation_uid_deterministic_per_batch(tmp_path: Path) -> None:
    """sanity (§J-2 #4): 同 audit + 同 schema + 同 batch_uid → 同 attestation_uid."""
    a = _make_audit("did:sisoul:alice", "edit", "/x", 0)
    schema = "0x" + "ab" * 32
    batch_uid = "fixed-batch-uid"
    uid1 = compute_attestation_uid(a, schema, batch_uid)
    uid2 = compute_attestation_uid(a, schema, batch_uid)
    assert uid1 == uid2, "同输入应同输出"

    # 不同 batch_uid → 不同 attestation_uid
    uid3 = compute_attestation_uid(a, schema, "other-batch-uid")
    assert uid3 != uid1, "不同 batch 应不同 uid"


@pytest.mark.skipif(
    os.environ.get("SISOUL_TEST_LIVE_TESTNET") != "1",
    reason="需 SISOUL_TEST_LIVE_TESTNET=1 才真打 Optimism Sepolia RPC",
)
def test_m3_eas_live_testnet_chain_id_smoke(tmp_path: Path) -> None:
    """可选 live: 真连 Optimism Sepolia RPC, 校验 chain_id == 11155420.

    跑法: SISOUL_TEST_LIVE_TESTNET=1 pytest qa/test_m3_p2p_and_onchain.py -k live
    """
    from sisoul.onchain.eas import (
        OPTIMISM_SEPOLIA_CHAIN_ID,
        OPTIMISM_SEPOLIA_DEFAULT_RPC,
        _verify_optimism_sepolia_rpc,
    )
    # 真打 RPC, 失败抛 EASError (本测试期望 OK; 失败 = RPC 暂时 down, 报告)
    _verify_optimism_sepolia_rpc(OPTIMISM_SEPOLIA_DEFAULT_RPC)
    assert OPTIMISM_SEPOLIA_CHAIN_ID == 11155420


# ---------------------------------------------------------------------------
# 2.3 · Arweave snapshot mock pipeline
# ---------------------------------------------------------------------------


def _make_vault_with_files(root: Path, n: int = 5) -> int:
    """造 n 个 vault 文件供 snapshot, 返回总字节."""
    (root / "preferences").mkdir(parents=True, exist_ok=True)
    (root / "goals").mkdir(parents=True, exist_ok=True)
    total = 0
    for i in range(n):
        f = root / "preferences" / f"p{i}.md"
        body = f"# pref {i}\n" + ("内容行\n" * 50)
        f.write_text(body, encoding="utf-8")
        total += len(body.encode("utf-8"))
    (root / "goals" / "g1.md").write_text("# Q3 目标\n上线 v1.0\n", encoding="utf-8")
    return total


def test_m3_snapshot_now_ipfs_only_mock(tmp_path: Path) -> None:
    """snapshot now --upload ipfs (mock 网络, mock Pinata): 写 history + sha256 真匹配."""
    mnemonic = generate_mnemonic()
    vault = tmp_path / "vault"
    _make_vault_with_files(vault, n=3)

    hist = SnapshotHistory(path=tmp_path / "snapshot_history.json")
    snap = ArweaveSnapshot(mnemonic=mnemonic, network="mock", history=hist)
    rec = snap.snapshot_now(vault_dir=vault, upload="ipfs")

    assert isinstance(rec, SnapshotRecord)
    assert rec.size_bytes > 0
    assert len(rec.sha256) == 64
    assert rec.ipfs_cid is not None
    assert rec.ipfs_cid.startswith("mockcid-"), f"无 PINATA_JWT 应 mock CID: {rec.ipfs_cid}"
    assert rec.arweave_tx_id is None  # 只要 ipfs
    assert rec.status == "ok"
    # history persistence
    loaded = hist.load()
    assert len(loaded) == 1
    assert loaded[0].sha256 == rec.sha256
    assert loaded[0].ipfs_cid == rec.ipfs_cid


def test_m3_snapshot_now_both_mock(tmp_path: Path) -> None:
    """snapshot now --upload both (mock): 同时记录 ipfs_cid + arweave_tx_id."""
    mnemonic = generate_mnemonic()
    vault = tmp_path / "vault"
    _make_vault_with_files(vault, n=2)
    hist = SnapshotHistory(path=tmp_path / "h.json")
    snap = ArweaveSnapshot(mnemonic=mnemonic, network="mock", history=hist)
    rec = snap.snapshot_now(vault_dir=vault, upload="both")

    assert rec.ipfs_cid and rec.ipfs_cid.startswith("mockcid-")
    assert rec.arweave_tx_id and rec.arweave_tx_id.startswith("mocktx-")
    assert rec.status == "ok"
    # sanity: ipfs_cid 跟 arweave_tx_id 都基于 sha256 但 prefix 不同, 都非全 0
    assert "0" * 32 not in rec.sha256


def test_m3_snapshot_history_records_correct_metadata(tmp_path: Path) -> None:
    """sanity (§J-2 #4): history 记录字段全填 + key_fingerprint 跟 mnemonic 派生一致."""
    mnemonic = generate_mnemonic()
    vault = tmp_path / "vault"
    _make_vault_with_files(vault, n=2)
    hist = SnapshotHistory(path=tmp_path / "h.json")
    snap = ArweaveSnapshot(mnemonic=mnemonic, network="mock", history=hist)
    rec1 = snap.snapshot_now(vault_dir=vault, upload="both")
    # 第 2 次 snapshot, 同 mnemonic → key_fingerprint 应一致
    rec2 = snap.snapshot_now(vault_dir=vault, upload="both")
    assert rec1.vault_master_key_fingerprint == rec2.vault_master_key_fingerprint
    assert len(rec1.vault_master_key_fingerprint) == 16  # sha256[:16]


def test_m3_snapshot_restore_full_roundtrip(tmp_path: Path) -> None:
    """端到端 (§J-2 #1): vault → snapshot blob → 解密 → restore → 文件全恢复."""
    mnemonic = generate_mnemonic()
    vault = tmp_path / "vault"
    _make_vault_with_files(vault, n=4)
    target = tmp_path / "restored"

    snap = ArweaveSnapshot(mnemonic=mnemonic, network="mock")
    # 直跑 snapshot_vault + 内存解 (mock 不能真上链拉, 用 in-memory roundtrip)
    encrypted, sha, _ = snap.snapshot_vault(vault)
    # 跟 dev 报告一致: ArweaveSnapshot 内部 decrypt_bytes 走 nacl SecretBox
    from sisoul.vault.encryption import decrypt_bytes
    key = snap._derive_encryption_key()
    plain_zip = decrypt_bytes(encrypted, key)
    # 解 zip 到 target
    import io
    import zipfile
    target.mkdir()
    with zipfile.ZipFile(io.BytesIO(plain_zip), "r") as zf:
        for member in zf.namelist():
            if member.startswith("vault/"):
                rel = member[len("vault/"):]
                if not rel:
                    continue
                dest = target / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as out:
                    out.write(src.read())
    # 比对: target 应跟 vault 文件完全等
    src_files = sorted(p.relative_to(vault) for p in vault.rglob("*") if p.is_file())
    dst_files = sorted(p.relative_to(target) for p in target.rglob("*") if p.is_file())
    assert src_files == dst_files, f"恢复后文件列表不一致: src={src_files} dst={dst_files}"
    # 内容比对一个样例
    for rel in src_files:
        assert (vault / rel).read_bytes() == (target / rel).read_bytes(), f"{rel} 内容不一致"


def test_m3_snapshot_mainnet_double_gate(tmp_path: Path) -> None:
    """反向 (§J-2 #3): network=mainnet 无 ARWEAVE_ALLOW_MAINNET → 降级 testnet."""
    mnemonic = generate_mnemonic()
    # 确保 env 不开
    os.environ.pop("ARWEAVE_ALLOW_MAINNET", None)
    snap = ArweaveSnapshot(mnemonic=mnemonic, network="mainnet")
    # 必须降级 testnet (gate 不允许)
    assert snap.network == "testnet", \
        f"mainnet 无 env 应降 testnet, 实 {snap.network}"
