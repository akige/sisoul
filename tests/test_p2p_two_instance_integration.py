"""真跑同机双实例 integration test (波 4 dev-A 验收).

场景:
- alice node + alice vault dir
- bob node + bob vault dir (同 BIP-39 seed → 同 P2P key)
- alice 写偏好 → 加 bob 为 peer → alice 主动 sync → bob 收到
- wall time < 5s

注: Phase 3 范围用 InMemoryTransport (同进程 bus). 真 NAT 穿透 / 真跨进程 daemon
跨机互发是 Phase 4 (朋友共享) 工作.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from sisoul.identity import generate_mnemonic, save_mnemonic_to_file
from sisoul.p2p.node import SisoulP2PNode


def _prep_vault(root: Path, mnemonic: str) -> Path:
    """建一个 vault dir + 写 seed + 几个偏好文件."""
    root.mkdir(parents=True, exist_ok=True)
    save_mnemonic_to_file(mnemonic, root / "seed.txt")
    return root


class TestTwoInstanceSync:
    @pytest.mark.asyncio
    async def test_alice_writes_bob_receives(self, tmp_path):
        """端到端: alice 写偏好 → sync → bob vault 收到."""
        start_t = time.time()

        # 同 mnemonic = 同 BIP-39 seed = 同 P2P key (双方互认)
        shared_mnemonic = generate_mnemonic(strength=128)

        alice_vault = _prep_vault(tmp_path / "alice-vault", shared_mnemonic)
        bob_vault = _prep_vault(tmp_path / "bob-vault", shared_mnemonic)

        # alice 写偏好 (sync 前)
        pref_dir = alice_vault / "preferences"
        pref_dir.mkdir()
        pref_file = pref_dir / "2026-05-18.md"
        pref_file.write_text("# alice 的偏好\n喜欢中文回复.\n", encoding="utf-8")

        alice = SisoulP2PNode(vault_dir=alice_vault, transport_prefer="inmem")
        bob = SisoulP2PNode(vault_dir=bob_vault, transport_prefer="inmem")

        try:
            await alice.start(port=0)
            await bob.start(port=0)

            # 互相加 peer (in-memory bus, peer_id 是 transport-level)
            alice.add_peer(f"inmem://{bob._transport.peer_id}",  # noqa: SLF001
                           peer_id=bob._transport.peer_id, transport="inmem")  # noqa: SLF001
            bob.add_peer(f"inmem://{alice._transport.peer_id}",  # noqa: SLF001
                         peer_id=alice._transport.peer_id, transport="inmem")  # noqa: SLF001

            # alice 主动 sync 给 bob
            result = await alice.sync_with(bob._transport.peer_id, timeout=4.0)  # noqa: SLF001
            assert result["ok"], f"sync 失败: {result.get('error')}"
            assert result["pushed"] >= 1, f"应至少推 1 偏好文件, 实际 {result}"

            # 等 bob recv loop 处理完 push (默认 poll 0.5s)
            bob_pref = bob_vault / "preferences" / "2026-05-18.md"
            for _ in range(20):  # 最多 2s
                if bob_pref.exists():
                    break
                await asyncio.sleep(0.1)
            assert bob_pref.exists(), "bob 没收到 alice 的 preferences/2026-05-18.md"
            assert "alice 的偏好" in bob_pref.read_text(encoding="utf-8")

            # bob 加 goal 反向 sync
            bob_goal_dir = bob_vault / "goals"
            bob_goal_dir.mkdir(exist_ok=True)
            (bob_goal_dir / "g1.md").write_text("# 长期目标\n搞通 P2P\n", encoding="utf-8")

            result2 = await bob.sync_with(alice._transport.peer_id, timeout=4.0)  # noqa: SLF001
            assert result2["ok"]
            assert result2["pushed"] >= 1

            alice_goal = alice_vault / "goals" / "g1.md"
            for _ in range(20):
                if alice_goal.exists():
                    break
                await asyncio.sleep(0.1)
            assert alice_goal.exists()
            assert "搞通 P2P" in alice_goal.read_text(encoding="utf-8")

            elapsed = time.time() - start_t
            assert elapsed < 5.0, f"wall time 超 5s: {elapsed:.2f}s"
        finally:
            await alice.stop()
            await bob.stop()

    @pytest.mark.asyncio
    async def test_different_seeds_cannot_sync(self, tmp_path):
        """反向: 不同 seed 双方加密 key 不匹配, sync 应失败 (DecryptionError) 或拿不到 inventory."""
        alice_mn = generate_mnemonic(strength=128)
        bob_mn = generate_mnemonic(strength=128)
        while alice_mn == bob_mn:
            bob_mn = generate_mnemonic(strength=128)

        alice_vault = _prep_vault(tmp_path / "alice-v", alice_mn)
        bob_vault = _prep_vault(tmp_path / "bob-v", bob_mn)
        (alice_vault / "preferences").mkdir()
        (alice_vault / "preferences" / "a.md").write_text("alice", encoding="utf-8")

        alice = SisoulP2PNode(vault_dir=alice_vault, transport_prefer="inmem")
        bob = SisoulP2PNode(vault_dir=bob_vault, transport_prefer="inmem")
        try:
            await alice.start()
            await bob.start()
            alice.add_peer(f"inmem://{bob._transport.peer_id}",  # noqa: SLF001
                           peer_id=bob._transport.peer_id, transport="inmem")  # noqa: SLF001

            # alice 发 INVENTORY_REQUEST → bob 收到但解密失败 (key 不匹配)
            # 结果: alice 等不到 INVENTORY_RESPONSE, sync timeout 后 result["ok"]=False
            result = await alice.sync_with(bob._transport.peer_id, timeout=1.5)  # noqa: SLF001
            assert result["ok"] is False
            assert result["error"] is not None
            # bob vault 没收到 alice 的文件
            assert not (bob_vault / "preferences" / "a.md").exists()
        finally:
            await alice.stop()
            await bob.stop()

    @pytest.mark.asyncio
    async def test_conflict_creates_log(self, tmp_path):
        """同 file 两 mtime 接近 + hash 不同 → conflict log + .conflict 副本."""
        shared_mn = generate_mnemonic(strength=128)
        alice_v = _prep_vault(tmp_path / "av", shared_mn)
        bob_v = _prep_vault(tmp_path / "bv", shared_mn)

        # 双方在 conflict 窗口内同时写不同内容
        import os
        fixed_mtime = int(time.time_ns())
        (alice_v / "preferences").mkdir()
        (bob_v / "preferences").mkdir()
        a_file = alice_v / "preferences" / "c.md"
        b_file = bob_v / "preferences" / "c.md"
        a_file.write_text("alice version", encoding="utf-8")
        b_file.write_text("bob version", encoding="utf-8")
        os.utime(a_file, ns=(fixed_mtime, fixed_mtime))
        os.utime(b_file, ns=(fixed_mtime + 1000, fixed_mtime + 1000))  # 1us 差 < 2s 窗口

        alice = SisoulP2PNode(vault_dir=alice_v, transport_prefer="inmem")
        bob = SisoulP2PNode(vault_dir=bob_v, transport_prefer="inmem")
        try:
            await alice.start()
            await bob.start()
            alice.add_peer(f"inmem://{bob._transport.peer_id}",  # noqa: SLF001
                           peer_id=bob._transport.peer_id, transport="inmem")  # noqa: SLF001
            result = await alice.sync_with(bob._transport.peer_id, timeout=4.0)  # noqa: SLF001
            assert result["ok"]
            assert result["conflicts"] >= 1, f"应至少 1 conflict, 实际 {result}"

            # conflict log 写入
            log_path = alice_v / "p2p" / "conflicts.log"
            assert log_path.exists()
            log_lines = log_path.read_text().strip().split("\n")
            assert any("preferences/c.md" in line for line in log_lines)

            # .conflict 副本存在
            conflict_copies = list((alice_v / "preferences").glob("c.md.conflict-*"))
            assert len(conflict_copies) >= 1
        finally:
            await alice.stop()
            await bob.stop()

    @pytest.mark.asyncio
    async def test_status_and_peer_list_after_sync(self, tmp_path):
        """sync 后 stats 应反映."""
        mn = generate_mnemonic(strength=128)
        av = _prep_vault(tmp_path / "av", mn)
        bv = _prep_vault(tmp_path / "bv", mn)
        (av / "preferences").mkdir()
        (av / "preferences" / "p.md").write_text("p", encoding="utf-8")

        a = SisoulP2PNode(vault_dir=av, transport_prefer="inmem")
        b = SisoulP2PNode(vault_dir=bv, transport_prefer="inmem")
        try:
            await a.start()
            await b.start()
            a.add_peer(f"inmem://{b._transport.peer_id}",  # noqa: SLF001
                       peer_id=b._transport.peer_id, transport="inmem")  # noqa: SLF001
            await a.sync_with(b._transport.peer_id, timeout=4.0)  # noqa: SLF001
            st = a.status()
            assert st.stats.syncs_total == 1
            assert st.stats.syncs_ok == 1
            assert st.stats.last_sync_pushed >= 1
            assert len(st.peers) == 1
        finally:
            await a.stop()
            await b.stop()
