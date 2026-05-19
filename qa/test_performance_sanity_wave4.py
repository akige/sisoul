"""波 4 qa-D · 性能 sanity (波 4 §30 通过标准).

目标:
- P2P sync 双实例 wall < 5s (M3 核心目标)
- EAS batched 10 条 gas 估算 < $0.01 (mock 算)
- Arweave snapshot 100MB vault wall < 60s (mock 模式, 不真上传)
- 全 qa 性能 test wall < 60s
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from sisoul.identity.seed import generate_mnemonic, save_mnemonic_to_file
from sisoul.onchain.arweave import ArweaveSnapshot, SnapshotHistory
from sisoul.onchain.eas import (
    AttestConfig,
    AttestQueue,
    AuditAttestation,
    upload_batch,
)
from sisoul.p2p.node import SisoulP2PNode


# ETH price assumption (mock); real-time fetch not required for sanity test.
_ETH_USD = 3500.0


# ---------------------------------------------------------------------------
# P2P
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_perf_p2p_two_instance_sync_under_5s(tmp_path: Path) -> None:
    """同 seed 双 node, 1 文件双向 sync, wall < 5s (M3 核心)."""
    seed = generate_mnemonic()
    alice_dir = tmp_path / "alice"
    bob_dir = tmp_path / "bob"
    for d in (alice_dir, bob_dir):
        d.mkdir()
        save_mnemonic_to_file(seed, d / "seed.txt")
        (d / "preferences").mkdir()

    alice = SisoulP2PNode(vault_dir=alice_dir, transport_prefer="inmem")
    bob = SisoulP2PNode(vault_dir=bob_dir, transport_prefer="inmem")

    (alice_dir / "preferences" / "test.md").write_text("# bench\n" + "x\n" * 100, encoding="utf-8")

    try:
        t0 = time.time()
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
        r = await alice.sync_with(bob._transport.peer_id, timeout=4.0)  # type: ignore[union-attr]
        # 等 bob 落盘
        bob_file = bob_dir / "preferences" / "test.md"
        for _ in range(20):
            if bob_file.exists():
                break
            await asyncio.sleep(0.05)
        wall = time.time() - t0
        assert r.get("ok") is True
        assert bob_file.exists()
        assert wall < 5.0, f"P2P sync 性能违规: {wall:.3f}s >= 5s"
        print(f"\n[perf] P2P 2-instance sync wall = {wall*1000:.0f}ms")
    finally:
        await alice.stop()
        await bob.stop()


# ---------------------------------------------------------------------------
# EAS batched gas
# ---------------------------------------------------------------------------


def test_perf_eas_batched_10_gas_under_1cent(tmp_path: Path) -> None:
    """10 条 batch flush, mock 估算 gas, 折合 USD < $0.01."""
    q = AttestQueue(db_path=tmp_path / "q.db")
    import hashlib
    for i in range(10):
        ph = hashlib.sha256(f"a{i}".encode()).hexdigest()
        q.enqueue(
            AuditAttestation(
                actor_did="did:sisoul:qa",
                action_type="edit",
                target=f"/x/{i}.md",
                prompt_hash="0x" + ph,
                timestamp=int(time.time()) + i,
                tool_name="qa",
            )
        )

    cfg = AttestConfig(network="mock", batch_size=10)
    batch = upload_batch(q, cfg)
    assert batch.count == 10
    # gas_cost_wei = gas_units * 1000 (mock); 折合 ETH = wei * 1e-18
    cost_eth = batch.gas_cost_wei_estimate * 1e-18
    cost_usd = cost_eth * _ETH_USD
    assert cost_usd < 0.01, (
        f"10 条 batch gas USD 估算 {cost_usd:.6f} 超 $0.01 (raw wei={batch.gas_cost_wei_estimate})"
    )
    print(
        f"\n[perf] EAS batch 10 = gas_units {batch.gas_used_estimate}, "
        f"wei {batch.gas_cost_wei_estimate}, ETH {cost_eth:.10f}, USD {cost_usd:.8f}"
    )


# ---------------------------------------------------------------------------
# Arweave snapshot 100MB vault wall
# ---------------------------------------------------------------------------


def test_perf_arweave_snapshot_100mb_vault_under_60s(tmp_path: Path) -> None:
    """vault 共 100MB (10 个 10MB 文件) snapshot encrypt + IPFS mock pin + history wall < 60s.

    mock 网络不真上链, 验证 ZIP + libsodium encrypt + sha256 + history.append 真路径性能.
    """
    mnemonic = generate_mnemonic()
    vault = tmp_path / "vault"
    vault.mkdir()
    # 真造 100MB 文件 (10 个 * 10MB 随机不可压缩内容, 接近 zip after-encrypt 实际大小)
    import os
    for i in range(10):
        # 用 os.urandom 防止 ZIP 压缩太狠失真
        (vault / f"big-{i}.bin").write_bytes(os.urandom(10 * 1024 * 1024))

    hist = SnapshotHistory(path=tmp_path / "h.json")
    snap = ArweaveSnapshot(mnemonic=mnemonic, network="mock", history=hist)
    t0 = time.time()
    rec = snap.snapshot_now(vault_dir=vault, upload="both")
    wall = time.time() - t0
    # 100MB 输入加密 + mock 上传, 应快于 60s
    assert wall < 60.0, f"snapshot 100MB vault 性能违规: {wall:.2f}s >= 60s"
    # sanity: 加密后 size 跟 100MB 量级一致 (urandom 不压缩, encrypt 加 ~40B overhead)
    assert rec.size_bytes > 90 * 1024 * 1024, f"加密 blob 应近 100MB: {rec.size_bytes}"
    print(
        f"\n[perf] snapshot 100MB vault wall = {wall:.2f}s; "
        f"blob_size = {rec.size_bytes/1024/1024:.1f}MB; "
        f"throughput = {rec.size_bytes/wall/1024/1024:.1f} MB/s"
    )
