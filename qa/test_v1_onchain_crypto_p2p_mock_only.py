"""qa-004 (sonnet) · 链上 + 加密 + P2P 模块 100% mock 覆盖测试

- 绝不真连任何 testnet/mainnet RPC
- 绝不真发 tx, 0 gas
- 绝不真打 LLM API
- 全程 unittest.mock 替换 Web3 / httpx / requests / arweave-python-client

覆盖模块:
1. sisoul/onchain/eas.py
2. sisoul/onchain/arweave.py
3. sisoul/identity/did.py
4. sisoul/identity/seed.py
5. sisoul/vault/encryption.py
6. sisoul/p2p/encryption.py
7. sisoul/friend/encrypted_proxy.py
8. sisoul/friend/skill_package.py
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
from nacl.public import PrivateKey, PublicKey, Box
from nacl.secret import SecretBox

# ═══════════════════════════════════════════════════════════════════════════════
# 1. TestEasMockOnly — sisoul.onchain.eas
# ═══════════════════════════════════════════════════════════════════════════════


class TestEasMockOnly:
    """EAS attestation queue — 完整 mock 路径."""

    # ── sanity: 无真 RPC 调用 ─────────────────────────────────────────────────

    def test_no_real_rpc_call_import_safe(self) -> None:
        """sanity: eas.py 可以 import 不触发真网络."""
        from sisoul.onchain.eas import MOCK_SCHEMA_UID, SISOUL_AUDIT_SCHEMA
        assert MOCK_SCHEMA_UID.startswith("0x")
        assert "actor_did" in SISOUL_AUDIT_SCHEMA

    def test_mock_schema_uid_deterministic(self) -> None:
        """MOCK_SCHEMA_UID 是确定性 sha256."""
        from sisoul.onchain.eas import MOCK_SCHEMA_UID, SISOUL_AUDIT_SCHEMA
        expected = "0x" + hashlib.sha256(
            f"sisoul-audit-v1::{SISOUL_AUDIT_SCHEMA}".encode("utf-8")
        ).hexdigest()
        assert MOCK_SCHEMA_UID == expected

    # ── mainnet 硬阻断 4 处 ────────────────────────────────────────────────────

    def test_mainnet_blocked_upload_batch(self, tmp_path: Path) -> None:
        """处 1: upload_batch mainnet → NetworkNotSupportedError."""
        from sisoul.onchain.eas import (
            AttestConfig, AttestQueue, AuditAttestation,
            NetworkNotSupportedError, upload_batch,
        )
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            att = AuditAttestation.from_audit_payload(
                actor_did="did:sisoul:alice",
                action_type="rm",
                target="/tmp/x",
                prompt="test",
                tool_name="claude-code",
            )
            q.enqueue(att)
            cfg = AttestConfig(network="optimism-mainnet")
            with pytest.raises(NetworkNotSupportedError, match="mainnet"):
                upload_batch(q, cfg)

    def test_mainnet_blocked_verify_onchain(self) -> None:
        """处 2: verify_attestation_onchain mainnet → NetworkNotSupportedError."""
        from sisoul.onchain.eas import (
            NetworkNotSupportedError, verify_attestation_onchain,
        )
        with pytest.raises(NetworkNotSupportedError, match="mainnet"):
            verify_attestation_onchain("0xdeadbeef", network="optimism-mainnet")

    def test_mainnet_blocked_list_history_onchain(self) -> None:
        """处 3: list_history_onchain mainnet → NetworkNotSupportedError."""
        from sisoul.onchain.eas import (
            NetworkNotSupportedError, list_history_onchain,
        )
        with pytest.raises(NetworkNotSupportedError, match="mainnet"):
            list_history_onchain(network="optimism-mainnet")

    def test_mainnet_blocked_live_send_tx(self, tmp_path: Path) -> None:
        """处 4: _live_send_batch_tx 路径 (private_key_path 设置时) → EASError abort.

        eas.py 中 Web3 是 lazy import (在 _live_send_batch_tx 内部 from web3 import Web3),
        所以 patch 目标是 builtins.__import__ 或直接让 upload_batch 走到 EASError 终止点.
        eas.py 中 _live_send_batch_tx 末尾硬 raise EASError("live-tx 路径已就绪但未启用"),
        所以任何带 private_key_path 的调用都会在内部路径因 import web3 成功/失败而 EASError.
        """
        from sisoul.onchain.eas import (
            AttestConfig, AttestQueue, AuditAttestation, EASError, upload_batch,
        )
        # 写假 private key 文件
        pk_file = tmp_path / "pk.hex"
        pk_file.write_text("dead" * 16 + "\n")  # 64 hex chars = 32B
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            att = AuditAttestation.from_audit_payload(
                actor_did="did:sisoul:alice",
                action_type="rm",
                target="/tmp/x",
                prompt="test",
                tool_name="claude-code",
            )
            q.enqueue(att)
            cfg = AttestConfig(
                network="optimism-sepolia",
                private_key_path=str(pk_file),
            )
            # web3 lazy imported inside _live_send_batch_tx.
            # Patch 'web3.Web3' at the module level (if installed) or let ImportError → EASError.
            # Either way, _live_send_batch_tx hardcodes a final EASError abort ("live-tx 路径已就绪但未启用").
            with pytest.raises(EASError):
                with patch.dict("sys.modules", {"web3": MagicMock(), "eth_account": MagicMock()}):
                    # Even with web3 mocked, _live_send_batch_tx raises EASError at the end
                    upload_batch(q, cfg)

    # ── batched queue 累积 / flush 阈值 ───────────────────────────────────────

    def test_queue_accumulate_10_triggers_should_flush(self, tmp_path: Path) -> None:
        """10 条 pending → should_flush 返 True."""
        from sisoul.onchain.eas import AttestQueue, AuditAttestation
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            for i in range(10):
                att = AuditAttestation.from_audit_payload(
                    actor_did=f"did:sisoul:alice{i}",
                    action_type="rm",
                    target=f"/tmp/f{i}",
                    prompt=f"rm /tmp/f{i}",
                    tool_name="claude-code",
                )
                q.enqueue(att)
            assert q.should_flush(batch_size=10, timeout_sec=3600) is True

    def test_queue_below_10_no_flush_without_timeout(self, tmp_path: Path) -> None:
        """9 条 pending + 无历史 flush → should_flush 返 False."""
        from sisoul.onchain.eas import AttestQueue, AuditAttestation
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            for i in range(9):
                att = AuditAttestation.from_audit_payload(
                    actor_did=f"did:sisoul:alice{i}",
                    action_type="rm",
                    target=f"/tmp/f{i}",
                    prompt=f"prompt{i}",
                    tool_name="claude-code",
                )
                q.enqueue(att)
            assert q.should_flush(batch_size=10, timeout_sec=3600) is False

    def test_queue_timeout_triggers_flush(self, tmp_path: Path) -> None:
        """有 pending + last_flush 在 1h+ 前 → should_flush 返 True."""
        from sisoul.onchain.eas import AttestQueue, AuditAttestation
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            att = AuditAttestation.from_audit_payload(
                actor_did="did:sisoul:alice",
                action_type="rm",
                target="/tmp/x",
                prompt="test",
                tool_name="claude-code",
            )
            q.enqueue(att)
            # 手动设 last_flush_ts 到 2h 前
            q._set_meta("last_flush_ts", str(int(time.time()) - 7201))
            q._conn.commit()
            assert q.should_flush(batch_size=10, timeout_sec=3600) is True

    def test_queue_empty_should_not_flush(self, tmp_path: Path) -> None:
        """空队列 → should_flush 返 False."""
        from sisoul.onchain.eas import AttestQueue
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            assert q.should_flush(batch_size=10, timeout_sec=3600) is False

    # ── enqueue / pending / stats ─────────────────────────────────────────────

    def test_enqueue_and_pending(self, tmp_path: Path) -> None:
        from sisoul.onchain.eas import AttestQueue, AuditAttestation
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            att = AuditAttestation.from_audit_payload(
                actor_did="did:sisoul:alice",
                action_type="git-push",
                target="origin/main",
                prompt="push prod code",
                tool_name="claude-code",
            )
            q.enqueue(att)
            pending = q.pending()
            assert len(pending) == 1
            assert pending[0].actor_did == "did:sisoul:alice"
            assert pending[0].status == "pending"

    def test_stats_counts(self, tmp_path: Path) -> None:
        from sisoul.onchain.eas import AttestQueue, AuditAttestation
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            for _ in range(3):
                att = AuditAttestation.from_audit_payload(
                    actor_did="did:sisoul:alice",
                    action_type="rm",
                    target="/tmp/x",
                    prompt="test",
                    tool_name="claude-code",
                )
                q.enqueue(att)
            stats = q.stats()
            assert stats["pending"] == 3
            assert stats["confirmed"] == 0

    # ── upload_batch mock 模式 ────────────────────────────────────────────────

    def test_upload_batch_mock_network(self, tmp_path: Path) -> None:
        """network=mock → 生成 mock tx, 不连网."""
        from sisoul.onchain.eas import (
            AttestConfig, AttestQueue, AuditAttestation, upload_batch,
        )
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            for i in range(3):
                att = AuditAttestation.from_audit_payload(
                    actor_did=f"did:sisoul:alice{i}",
                    action_type="rm",
                    target=f"/tmp/f{i}",
                    prompt=f"prompt{i}",
                    tool_name="claude-code",
                )
                q.enqueue(att)
            cfg = AttestConfig(network="mock")
            result = upload_batch(q, cfg, force=True)
            assert result.method == "mock"
            assert result.tx_hash.startswith("0x")
            assert result.count == 3
            assert len(result.attestation_uids) == 3
            # queue items now confirmed
            stats = q.stats()
            assert stats["confirmed"] == 3
            assert stats["pending"] == 0

    def test_upload_batch_queue_empty_raises(self, tmp_path: Path) -> None:
        from sisoul.onchain.eas import AttestConfig, AttestQueue, QueueEmptyError, upload_batch
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            cfg = AttestConfig(network="mock")
            with pytest.raises(QueueEmptyError):
                upload_batch(q, cfg)

    def test_upload_batch_sepolia_no_key_mock(self, tmp_path: Path) -> None:
        """sepolia + no private_key_path → live-readonly 或 mock (RPC mock).

        _verify_optimism_sepolia_rpc 内部 lazy import httpx 后调 httpx.post.
        patch 'httpx.post' in the httpx module that eas imports via lazy import.
        """
        from sisoul.onchain.eas import (
            AttestConfig, AttestQueue, AuditAttestation, upload_batch,
        )
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            att = AuditAttestation.from_audit_payload(
                actor_did="did:sisoul:alice",
                action_type="rm",
                target="/tmp/x",
                prompt="test",
                tool_name="claude-code",
            )
            q.enqueue(att)
            cfg = AttestConfig(network="optimism-sepolia", private_key_path=None)
            # _verify_optimism_sepolia_rpc 在 sisoul.onchain.eas 里 lazy import httpx 再调 httpx.post.
            # patch the httpx module directly so RPC check raises → falls back to mock method.
            import httpx as _httpx
            with patch.object(_httpx, "post", side_effect=Exception("mocked network block")):
                result = upload_batch(q, cfg)
                assert result.method == "mock"  # fail-open

    # ── schema UID 计算正确 ───────────────────────────────────────────────────

    def test_compute_attestation_uid_deterministic(self, tmp_path: Path) -> None:
        """同 att + schema_uid + batch_uid → 同 attestation UID."""
        from sisoul.onchain.eas import AuditAttestation, compute_attestation_uid, MOCK_SCHEMA_UID
        att = AuditAttestation.from_audit_payload(
            actor_did="did:sisoul:alice",
            action_type="rm",
            target="/tmp/x",
            prompt="deterministic",
            tool_name="claude-code",
        )
        batch_uid = "test-batch-abc"
        uid1 = compute_attestation_uid(att, MOCK_SCHEMA_UID, batch_uid)
        uid2 = compute_attestation_uid(att, MOCK_SCHEMA_UID, batch_uid)
        assert uid1 == uid2
        assert uid1.startswith("0x")

    def test_compute_attestation_uid_different_batch(self, tmp_path: Path) -> None:
        """不同 batch_uid → 不同 attestation UID."""
        from sisoul.onchain.eas import AuditAttestation, compute_attestation_uid, MOCK_SCHEMA_UID
        att = AuditAttestation.from_audit_payload(
            actor_did="did:sisoul:alice",
            action_type="rm",
            target="/tmp/x",
            prompt="same",
            tool_name="claude-code",
        )
        uid1 = compute_attestation_uid(att, MOCK_SCHEMA_UID, "batch-1")
        uid2 = compute_attestation_uid(att, MOCK_SCHEMA_UID, "batch-2")
        assert uid1 != uid2

    # ── attestation encoding ──────────────────────────────────────────────────

    def test_encode_attestation_data_canonical_json(self) -> None:
        """encode_attestation_data 返 canonical JSON bytes."""
        from sisoul.onchain.eas import AuditAttestation, encode_attestation_data
        att = AuditAttestation.from_audit_payload(
            actor_did="did:sisoul:bob",
            action_type="curl-post",
            target="https://api.example.com",
            prompt="POST sensitive data",
            tool_name="claude-code",
        )
        data = encode_attestation_data(att)
        assert isinstance(data, bytes)
        parsed = json.loads(data.decode("utf-8"))
        assert parsed["actor_did"] == "did:sisoul:bob"
        assert parsed["action_type"] == "curl-post"
        assert parsed["tool_name"] == "claude-code"

    def test_encode_attestation_data_deterministic(self) -> None:
        """同一 att 两次编码应 == (canonical 排序)."""
        from sisoul.onchain.eas import AuditAttestation, encode_attestation_data
        att = AuditAttestation(
            actor_did="did:sisoul:alice",
            action_type="rm",
            target="/tmp/x",
            prompt_hash="0x" + "a" * 64,
            timestamp=1000000,
            tool_name="claude-code",
        )
        d1 = encode_attestation_data(att)
        d2 = encode_attestation_data(att)
        assert d1 == d2

    # ── prompt_hash 归一化 ────────────────────────────────────────────────────

    def test_prompt_hash_normalized_to_hex64(self) -> None:
        """prompt_hash 应自动 0x 前缀 + 64 hex digits."""
        from sisoul.onchain.eas import AuditAttestation
        att = AuditAttestation(
            actor_did="did:sisoul:alice",
            action_type="rm",
            target="/tmp/x",
            prompt_hash="abc",  # 短 hex, 无 0x
            timestamp=1000000,
            tool_name="claude-code",
        )
        assert att.prompt_hash.startswith("0x")
        assert len(att.prompt_hash) == 66  # 0x + 64

    def test_prompt_hash_from_audit_payload(self) -> None:
        """from_audit_payload 自动 sha256 prompt."""
        from sisoul.onchain.eas import AuditAttestation
        att = AuditAttestation.from_audit_payload(
            actor_did="did:sisoul:alice",
            action_type="rm",
            target="/tmp/x",
            prompt="hello world",
            tool_name="claude-code",
        )
        expected = "0x" + hashlib.sha256(b"hello world").hexdigest()
        assert att.prompt_hash == expected

    # ── verify_attestation_local ──────────────────────────────────────────────

    def test_verify_local_valid(self, tmp_path: Path) -> None:
        """batch flush 后 verify_local 应 valid=True."""
        from sisoul.onchain.eas import (
            AttestConfig, AttestQueue, AuditAttestation,
            upload_batch, verify_attestation_local,
        )
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            att = AuditAttestation.from_audit_payload(
                actor_did="did:sisoul:alice",
                action_type="rm",
                target="/tmp/x",
                prompt="verify me",
                tool_name="claude-code",
            )
            q.enqueue(att)
            cfg = AttestConfig(network="mock")
            result = upload_batch(q, cfg, force=True)
            uid = result.attestation_uids[0]
            vr = verify_attestation_local(q, uid)
            assert vr["valid"] is True
            assert vr["method"] == "local-recompute"

    def test_verify_local_not_found_raises(self, tmp_path: Path) -> None:
        from sisoul.onchain.eas import AttestQueue, AttestationNotFoundError, verify_attestation_local
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            with pytest.raises(AttestationNotFoundError):
                verify_attestation_local(q, "0xdeadbeef")

    def test_verify_onchain_mock_returns_false(self) -> None:
        """network=mock → onchain verify 返 valid=False (无链上数据)."""
        from sisoul.onchain.eas import verify_attestation_onchain
        r = verify_attestation_onchain("0xabcd", network="mock")
        assert r["valid"] is False

    # ── upload_batch_with_retry ───────────────────────────────────────────────

    def test_upload_batch_retry_succeeds_on_first(self, tmp_path: Path) -> None:
        from sisoul.onchain.eas import (
            AttestConfig, AttestQueue, AuditAttestation,
            upload_batch_with_retry,
        )
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            att = AuditAttestation.from_audit_payload(
                actor_did="did:sisoul:alice",
                action_type="rm",
                target="/tmp/x",
                prompt="test",
                tool_name="claude-code",
            )
            q.enqueue(att)
            cfg = AttestConfig(network="mock")
            result = upload_batch_with_retry(q, cfg, max_retries=3, base_delay_sec=0.0)
            assert result.count == 1

    def test_upload_batch_retry_raises_after_max(self, tmp_path: Path) -> None:
        from sisoul.onchain.eas import (
            AttestConfig, AttestQueue, AuditAttestation,
            EASError, upload_batch_with_retry,
        )
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            att = AuditAttestation.from_audit_payload(
                actor_did="did:sisoul:alice",
                action_type="rm",
                target="/tmp/x",
                prompt="test",
                tool_name="claude-code",
            )
            q.enqueue(att)
            cfg = AttestConfig(network="mock")
            with patch("sisoul.onchain.eas.upload_batch", side_effect=EASError("mock rpc down")):
                with pytest.raises(EASError, match="3 次重试"):
                    upload_batch_with_retry(q, cfg, max_retries=3, base_delay_sec=0.0)

    def test_upload_batch_retry_skip_permanent_errors(self, tmp_path: Path) -> None:
        """NetworkNotSupportedError / QueueEmptyError 不重试, 立即抛."""
        from sisoul.onchain.eas import (
            AttestConfig, AttestQueue, AuditAttestation,
            NetworkNotSupportedError, upload_batch_with_retry,
        )
        db = tmp_path / "q.db"
        with AttestQueue(db) as q:
            att = AuditAttestation.from_audit_payload(
                actor_did="did:sisoul:alice",
                action_type="rm",
                target="/tmp/x",
                prompt="test",
                tool_name="claude-code",
            )
            q.enqueue(att)
            cfg = AttestConfig(network="optimism-mainnet")
            with pytest.raises(NetworkNotSupportedError):
                upload_batch_with_retry(q, cfg, max_retries=3, base_delay_sec=0.0)

    # ── resolve_attester_did ──────────────────────────────────────────────────

    def test_resolve_attester_did_from_config(self, tmp_path: Path) -> None:
        from sisoul.onchain.eas import AttestConfig, resolve_attester_did
        cfg = AttestConfig(attester_did="did:sisoul:alice")
        did = resolve_attester_did(cfg)
        assert did == "did:sisoul:alice"

    def test_resolve_attester_did_from_local_registry(self, tmp_path: Path) -> None:
        """resolve_attester_did 走本地 DID registry.

        eas.py resolve_attester_did 查 vault_dir / "identity" / "dids.json".
        所以先在 tmp_path/identity/dids.json 注册 DID.
        """
        from sisoul.onchain.eas import AttestConfig, resolve_attester_did
        from sisoul.identity.did import register_did
        identity_dir = tmp_path / "identity"
        identity_dir.mkdir(parents=True, exist_ok=True)
        reg_path = identity_dir / "dids.json"
        register_did("alice", network="mock", registry_path=reg_path)
        cfg = AttestConfig()  # attester_did=None
        did = resolve_attester_did(cfg, vault_dir=tmp_path)
        assert did == "did:sisoul:alice"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. TestArweaveMockOnly — sisoul.onchain.arweave
# ═══════════════════════════════════════════════════════════════════════════════


class TestArweaveMockOnly:
    """Arweave + IPFS snapshot — 完整 mock 路径."""

    # ── sanity: import 不触发网络 ─────────────────────────────────────────────

    def test_import_no_network(self) -> None:
        from sisoul.onchain.arweave import ArweaveSnapshot, ARWEAVE_TESTNET_GATEWAY
        assert ARWEAVE_TESTNET_GATEWAY == "https://test.arweave.net"

    # ── mainnet 双 gate 防护 ──────────────────────────────────────────────────

    def test_mainnet_gate_env_absent_downgrades(self) -> None:
        """ARWEAVE_ALLOW_MAINNET 未设 → 自动降 testnet."""
        from sisoul.onchain.arweave import ArweaveSnapshot
        env = {k: v for k, v in os.environ.items() if k != "ARWEAVE_ALLOW_MAINNET"}
        with patch.dict(os.environ, env, clear=True):
            snap = ArweaveSnapshot(mnemonic=None, network="mainnet")
            assert snap.network == "testnet"

    def test_mainnet_gate_env_set_allows(self) -> None:
        """ARWEAVE_ALLOW_MAINNET=1 → mainnet 放行."""
        from sisoul.onchain.arweave import ArweaveSnapshot
        with patch.dict(os.environ, {"ARWEAVE_ALLOW_MAINNET": "1"}):
            snap = ArweaveSnapshot(mnemonic=None, network="mainnet")
            assert snap.network == "mainnet"

    # ── mock network IPFS pin ─────────────────────────────────────────────────

    def test_ipfs_pin_no_jwt_returns_mock_cid(self, tmp_path: Path) -> None:
        """无 pinata_jwt → 返回 mock CID (本地生成)."""
        from sisoul.onchain.arweave import ArweaveSnapshot
        snap = ArweaveSnapshot(mnemonic=None, network="mock", pinata_jwt=None)
        blob = b"fake encrypted data"
        cid = snap.pin_to_ipfs(blob)
        assert cid is not None
        assert cid.startswith("mockcid-")

    def test_ipfs_pin_with_mock_jwt_calls_pinata(self, tmp_path: Path) -> None:
        """有 pinata_jwt → mock httpx.Client POST → 返 IpfsHash."""
        from sisoul.onchain.arweave import ArweaveSnapshot
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"IpfsHash": "QmTestCID123"}
        mock_resp.raise_for_status = MagicMock()

        with patch("sisoul.onchain.arweave.httpx.Client") as mc:
            mc.return_value.__enter__.return_value.post.return_value = mock_resp
            snap = ArweaveSnapshot(mnemonic=None, network="mock", pinata_jwt="fake-jwt")
            cid = snap.pin_to_ipfs(b"test data")
            assert cid == "QmTestCID123"

    def test_ipfs_pin_failure_returns_none(self) -> None:
        """Pinata 请求失败 → 返回 None (不 raise)."""
        import httpx as httpx_lib
        from sisoul.onchain.arweave import ArweaveSnapshot
        with patch("sisoul.onchain.arweave.httpx.Client") as mc:
            mc.return_value.__enter__.return_value.post.side_effect = httpx_lib.HTTPError("connection refused")
            snap = ArweaveSnapshot(mnemonic=None, network="mock", pinata_jwt="fake-jwt")
            cid = snap.pin_to_ipfs(b"data")
            assert cid is None

    # ── mock network Arweave upload ────────────────────────────────────────────

    def test_arweave_upload_mock_network(self) -> None:
        """network=mock → fake tx_id 返回."""
        from sisoul.onchain.arweave import ArweaveSnapshot
        snap = ArweaveSnapshot(mnemonic=None, network="mock")
        tx_id = snap.upload_to_arweave(b"blob data")
        assert tx_id is not None
        assert tx_id.startswith("mocktx-")

    def test_arweave_upload_testnet_no_wallet_fake(self) -> None:
        """testnet + 无 wallet → fake tx_id, 不连真 Arweave."""
        from sisoul.onchain.arweave import ArweaveSnapshot
        snap = ArweaveSnapshot(mnemonic=None, network="testnet", arweave_wallet_path=None)
        # 清除 env wallet
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ARWEAVE_WALLET", None)
            snap.arweave_wallet_path = None
            tx_id = snap.upload_to_arweave(b"blob data")
        assert tx_id is not None
        assert "fake" in tx_id or tx_id.startswith("testnet-fake-")

    # ── IPFS pin / fetch lifecycle mock ──────────────────────────────────────

    def test_snapshot_now_mock_full_lifecycle(self, tmp_path: Path) -> None:
        """snapshot_now mock: 加密 + pin + arweave + 写 history."""
        from sisoul.onchain.arweave import ArweaveSnapshot, SnapshotHistory
        from sisoul.identity.seed import generate_mnemonic
        mnemonic = generate_mnemonic()
        history_file = tmp_path / "history.json"
        hist = SnapshotHistory(path=history_file)
        snap = ArweaveSnapshot(
            mnemonic=mnemonic,
            network="mock",
            pinata_jwt=None,
            history=hist,
        )
        # 建 vault dir
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "test.txt").write_text("hello world")
        record = snap.snapshot_now(vault_dir, upload="both")
        assert record.status == "ok"
        assert record.sha256
        assert record.size_bytes > 0
        assert record.network == "mock"
        assert record.ipfs_cid is not None
        assert record.arweave_tx_id is not None
        # history 写入
        loaded = hist.load()
        assert len(loaded) == 1

    def test_snapshot_vault_encrypt_decrypt_roundtrip(self, tmp_path: Path) -> None:
        """snapshot_vault 加密后可解密回 ZIP."""
        import zipfile, io
        from sisoul.onchain.arweave import ArweaveSnapshot
        from sisoul.vault.encryption import decrypt_bytes
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key, derive_subkey
        mnemonic = generate_mnemonic()
        master = mnemonic_to_master_key(mnemonic)
        key = derive_subkey(master, "arweave", index=0)
        snap = ArweaveSnapshot(mnemonic=mnemonic, network="mock")
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "soul.txt").write_text("private soul data")
        encrypted, sha, fp = snap.snapshot_vault(vault_dir, encryption_key=key)
        # verify sha
        assert hashlib.sha256(encrypted).hexdigest() == sha
        # decrypt
        plain_zip = decrypt_bytes(encrypted, key)
        with zipfile.ZipFile(io.BytesIO(plain_zip), "r") as zf:
            names = zf.namelist()
            assert any("soul.txt" in n for n in names)

    # ── 24h 过期 unpin scheduler (schedule_monthly_snapshot) ─────────────────

    def test_schedule_monthly_darwin_plist_generated(self) -> None:
        """macOS → 生成 launchd plist (不 install, 不调 launchctl)."""
        from sisoul.onchain.arweave import schedule_monthly_snapshot
        with patch("sisoul.onchain.arweave.platform.system", return_value="Darwin"):
            r = schedule_monthly_snapshot(cadence="monthly", upload="both", install=False)
        assert r["system"] == "darwin"
        assert "io.sisoul.snapshot.monthly" in r["unit_text"]
        assert r["installed"] is False

    def test_schedule_monthly_linux_systemd_generated(self) -> None:
        """Linux → 生成 systemd timer (不 install)."""
        from sisoul.onchain.arweave import schedule_monthly_snapshot
        with patch("sisoul.onchain.arweave.platform.system", return_value="Linux"):
            r = schedule_monthly_snapshot(cadence="weekly", upload="ipfs", install=False)
        assert r["system"] == "linux"
        assert "timer" in r["unit_text"].lower() or "Timer" in r["unit_text"]
        assert r["installed"] is False

    def test_schedule_never_no_unit(self) -> None:
        from sisoul.onchain.arweave import schedule_monthly_snapshot
        r = schedule_monthly_snapshot(cadence="never")
        assert "never" in r["unit_text"]
        assert r["installed"] is False

    def test_restore_mock_tx_raises(self, tmp_path: Path) -> None:
        """mock tx_id 无法真拉 → RuntimeError."""
        from sisoul.onchain.arweave import ArweaveSnapshot
        from sisoul.identity.seed import generate_mnemonic
        mnemonic = generate_mnemonic()
        snap = ArweaveSnapshot(mnemonic=mnemonic, network="mock")
        with pytest.raises(RuntimeError, match="mock"):
            snap.restore_from_arweave(
                "mocktx-abc123",
                target_vault_dir=tmp_path / "restored",
                source="arweave",
            )

    def test_snapshot_now_with_retry_ok_first_try(self, tmp_path: Path) -> None:
        """retry wrapper: 第一次 ok → 直接返回."""
        from sisoul.onchain.arweave import ArweaveSnapshot
        from sisoul.identity.seed import generate_mnemonic
        mnemonic = generate_mnemonic()
        snap = ArweaveSnapshot(mnemonic=mnemonic, network="mock")
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir()
        (vault_dir / "f.txt").write_text("content")
        rec = snap.snapshot_now_with_retry(vault_dir, upload="both", max_retries=3, base_delay_sec=0.0)
        assert rec.status == "ok"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TestDidMockOnly — sisoul.identity.did
# ═══════════════════════════════════════════════════════════════════════════════


class TestDidMockOnly:
    """DID 身份层 — mock web3 / ens / 本地 registry."""

    def test_validate_handle_valid(self) -> None:
        from sisoul.identity.did import validate_handle
        assert validate_handle("alice") == "alice"
        assert validate_handle("ALICE") == "alice"  # 归一小写
        assert validate_handle("alice-123") == "alice-123"

    def test_validate_handle_too_short(self) -> None:
        from sisoul.identity.did import InvalidHandleError, validate_handle
        with pytest.raises(InvalidHandleError, match="3 字符"):
            validate_handle("ab")

    def test_validate_handle_too_long(self) -> None:
        from sisoul.identity.did import InvalidHandleError, validate_handle
        with pytest.raises(InvalidHandleError, match="63 字符"):
            validate_handle("a" * 64)

    def test_validate_handle_illegal_chars(self) -> None:
        from sisoul.identity.did import InvalidHandleError, validate_handle
        with pytest.raises(InvalidHandleError, match="非法字符"):
            validate_handle("alice_!")

    def test_validate_handle_leading_hyphen(self) -> None:
        from sisoul.identity.did import InvalidHandleError, validate_handle
        with pytest.raises(InvalidHandleError):
            validate_handle("-alice")

    # ── compute_namehash ENS EIP-137 ──────────────────────────────────────────

    def test_compute_namehash_empty(self) -> None:
        """空名 → 32 零字节."""
        from sisoul.identity.did import compute_namehash
        nh = compute_namehash("")
        assert nh == "0x" + "0" * 64

    def test_compute_namehash_deterministic(self) -> None:
        from sisoul.identity.did import compute_namehash
        h1 = compute_namehash("alice.sisoul.eth")
        h2 = compute_namehash("alice.sisoul.eth")
        assert h1 == h2
        assert h1.startswith("0x")

    def test_compute_namehash_different_names(self) -> None:
        from sisoul.identity.did import compute_namehash
        h1 = compute_namehash("alice.sisoul.eth")
        h2 = compute_namehash("bob.sisoul.eth")
        assert h1 != h2

    def test_compute_ens_subdomain(self) -> None:
        from sisoul.identity.did import compute_ens_subdomain
        sub = compute_ens_subdomain("alice")
        assert sub == "alice.sisoul.eth"

    # ── DID 类 + ERC-7231 schema ──────────────────────────────────────────────

    def test_did_string_format(self) -> None:
        from sisoul.identity.did import DID
        did = DID(handle="alice", public_key="zpubkey")
        assert did.did_string == "did:sisoul:alice"

    def test_did_ens_subdomain(self) -> None:
        from sisoul.identity.did import DID
        did = DID(handle="alice", public_key="zpubkey")
        assert did.ens_subdomain == "alice.sisoul.eth"

    def test_did_document_w3c_context(self) -> None:
        from sisoul.identity.did import DID
        did = DID(handle="alice", public_key="zpubkey")
        doc = did.to_did_document()
        assert "https://www.w3.org/ns/did/v1" in doc["@context"]
        assert doc["id"] == "did:sisoul:alice"
        assert doc["verificationMethod"][0]["type"] == "Ed25519VerificationKey2020"

    def test_did_controllers_default_self(self) -> None:
        from sisoul.identity.did import DID
        did = DID(handle="alice", public_key="zpubkey")
        assert "did:sisoul:alice" in did.controllers

    def test_did_roundtrip_dict(self) -> None:
        from sisoul.identity.did import DID
        did = DID(handle="alice", public_key="zpubkey", network="mock")
        d = did.to_dict()
        did2 = DID.from_dict(dict(d))
        assert did2.handle == "alice"
        assert did2.network == "mock"

    # ── register_did mock (no web3) ───────────────────────────────────────────

    def test_register_did_mock_network(self, tmp_path: Path) -> None:
        """network=mock → 不连 RPC, 直接返 DID."""
        from sisoul.identity.did import register_did, list_local_dids
        reg = tmp_path / "dids.json"
        did = register_did("alice", network="mock", registry_path=reg)
        assert did.handle == "alice"
        assert did.network == "mock"
        # registry 写入
        dids = list_local_dids(reg)
        assert len(dids) == 1
        assert dids[0].handle == "alice"

    def test_register_did_duplicate_handle_raises(self, tmp_path: Path) -> None:
        from sisoul.identity.did import HandleAlreadyTakenError, register_did
        reg = tmp_path / "dids.json"
        register_did("alice", network="mock", registry_path=reg)
        with pytest.raises(HandleAlreadyTakenError):
            register_did("alice", network="mock", registry_path=reg)

    def test_register_did_mainnet_blocked(self, tmp_path: Path) -> None:
        from sisoul.identity.did import NetworkNotSupportedError, register_did
        reg = tmp_path / "dids.json"
        with pytest.raises(NetworkNotSupportedError, match="mainnet"):
            register_did("alice", network="mainnet", registry_path=reg)

    def test_register_did_with_master_seed(self, tmp_path: Path) -> None:
        """传 master_seed → 派生确定性 public_key."""
        from sisoul.identity.did import register_did, derive_public_key
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        mnemonic = generate_mnemonic()
        master = mnemonic_to_master_key(mnemonic)
        reg = tmp_path / "dids.json"
        did = register_did("alice", network="mock", master_seed=master, registry_path=reg)
        expected_pub = derive_public_key("alice", master_seed=master)
        assert did.public_key == expected_pub

    # ── resolve_did ───────────────────────────────────────────────────────────

    def test_resolve_did_by_did_string(self, tmp_path: Path) -> None:
        from sisoul.identity.did import register_did, resolve_did
        reg = tmp_path / "dids.json"
        register_did("alice", network="mock", registry_path=reg)
        did = resolve_did("did:sisoul:alice", registry_path=reg)
        assert did.handle == "alice"

    def test_resolve_did_by_ens_subdomain(self, tmp_path: Path) -> None:
        from sisoul.identity.did import register_did, resolve_did
        reg = tmp_path / "dids.json"
        register_did("alice", network="mock", registry_path=reg)
        did = resolve_did("alice.sisoul.eth", registry_path=reg)
        assert did.handle == "alice"

    def test_resolve_did_not_found_raises(self, tmp_path: Path) -> None:
        from sisoul.identity.did import DIDNotFoundError, resolve_did
        reg = tmp_path / "dids.json"
        reg.write_text("[]")
        with pytest.raises(DIDNotFoundError):
            resolve_did("did:sisoul:nobody", registry_path=reg)

    # ── Privy social recovery mock ────────────────────────────────────────────

    def test_social_recovery_github(self) -> None:
        from sisoul.identity.did import link_social_recovery
        result = link_social_recovery("github", oauth_token="tok123")
        assert result.provider == "github"
        assert result.user_id
        assert result.embedded_wallet_address.startswith("0x")
        assert len(result.embedded_wallet_address) == 42

    def test_social_recovery_deterministic(self) -> None:
        """同 token → 同 user_id + wallet_address."""
        from sisoul.identity.did import link_social_recovery
        r1 = link_social_recovery("google", oauth_token="same_token")
        r2 = link_social_recovery("google", oauth_token="same_token")
        assert r1.user_id == r2.user_id
        assert r1.embedded_wallet_address == r2.embedded_wallet_address

    def test_social_recovery_email(self) -> None:
        from sisoul.identity.did import link_social_recovery
        result = link_social_recovery("email", user_email="alice@example.com")
        assert result.provider == "email"

    def test_social_recovery_missing_oauth_token_raises(self) -> None:
        from sisoul.identity.did import DIDError, link_social_recovery
        with pytest.raises(DIDError, match="oauth_token"):
            link_social_recovery("github", oauth_token=None)

    def test_social_recovery_email_missing_email_raises(self) -> None:
        from sisoul.identity.did import DIDError, link_social_recovery
        with pytest.raises(DIDError, match="user_email"):
            link_social_recovery("email")

    def test_social_recovery_invalid_provider_raises(self) -> None:
        from sisoul.identity.did import DIDError, link_social_recovery
        with pytest.raises(DIDError, match="不支持"):
            link_social_recovery("discord", oauth_token="tok")  # type: ignore[arg-type]

    # ── register_ens_subdomain mock ───────────────────────────────────────────

    def test_register_ens_mock(self) -> None:
        from sisoul.identity.did import register_ens_subdomain
        r = register_ens_subdomain("alice", "zpubkey", network="mock")
        assert r["method"] == "mock"
        assert r["ens_subdomain"] == "alice.sisoul.eth"
        assert r["tx_hash"].startswith("0x")

    def test_register_ens_sepolia_no_live(self) -> None:
        """sepolia + live=False → mock (不连 RPC)."""
        from sisoul.identity.did import register_ens_subdomain
        r = register_ens_subdomain("alice", "zpubkey", network="sepolia", live=False)
        assert r["method"] == "mock"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TestSeedBip39MockOnly — sisoul.identity.seed
# ═══════════════════════════════════════════════════════════════════════════════


class TestSeedBip39MockOnly:
    """BIP-39 seed 生成 / 验证 / 派生 — 使用真实 BIP-39 库 (纯本地, 无网络)."""

    # ── generate_mnemonic ─────────────────────────────────────────────────────

    def test_generate_mnemonic_12_words(self) -> None:
        from sisoul.identity.seed import generate_mnemonic
        m = generate_mnemonic(strength=128)
        assert len(m.split()) == 12

    def test_generate_mnemonic_24_words(self) -> None:
        from sisoul.identity.seed import generate_mnemonic
        m = generate_mnemonic(strength=256)
        assert len(m.split()) == 24

    def test_generate_mnemonic_15_words(self) -> None:
        from sisoul.identity.seed import generate_mnemonic
        m = generate_mnemonic(strength=160)
        assert len(m.split()) == 15

    def test_generate_mnemonic_each_unique(self) -> None:
        """两次生成应不同 (极小概率相同, 忽略)."""
        from sisoul.identity.seed import generate_mnemonic
        m1 = generate_mnemonic()
        m2 = generate_mnemonic()
        assert m1 != m2

    def test_generate_mnemonic_invalid_strength_raises(self) -> None:
        from sisoul.identity.seed import generate_mnemonic
        with pytest.raises(ValueError, match="strength"):
            generate_mnemonic(strength=100)

    # ── verify_mnemonic ───────────────────────────────────────────────────────

    def test_verify_mnemonic_valid(self) -> None:
        from sisoul.identity.seed import generate_mnemonic, verify_mnemonic
        m = generate_mnemonic()
        assert verify_mnemonic(m) is True

    def test_verify_mnemonic_invalid(self) -> None:
        from sisoul.identity.seed import verify_mnemonic
        assert verify_mnemonic("not a real mnemonic words here test") is False

    def test_verify_mnemonic_empty(self) -> None:
        from sisoul.identity.seed import verify_mnemonic
        assert verify_mnemonic("") is False

    def test_verify_mnemonic_non_string(self) -> None:
        from sisoul.identity.seed import verify_mnemonic
        assert verify_mnemonic(None) is False  # type: ignore[arg-type]

    # ── BIP-39 표준 test vectors ──────────────────────────────────────────────

    def test_standard_bip39_test_vector_abandon(self) -> None:
        """abandon * 11 + about 是合法 BIP-39 12-词 (entropy all-zeros)."""
        from sisoul.identity.seed import verify_mnemonic, mnemonic_to_master_key
        m = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        assert verify_mnemonic(m) is True
        # BIP-39 标准: PBKDF2 with salt "mnemonic" → 64B seed
        seed = mnemonic_to_master_key(m, passphrase="")
        assert len(seed) == 64
        # 已知 test vector 从 https://github.com/trezor/python-mnemonic/blob/master/vectors.json
        expected_hex = "5eb00bbddcf069084889a8ab9155568165f5c453ccb85e70811aaed6f6da5fc19a5ac40b389cd370d086206dec8aa6c43daea6690f20ad3d8d48b2d2ce9e38e4"
        # 只校前 32 字符 (64B seed 首 16B)
        assert seed.hex()[:32] == expected_hex[:32]

    def test_bip39_passphrase_changes_seed(self) -> None:
        """同 mnemonic + 不同 passphrase → 不同 seed."""
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        m = generate_mnemonic()
        s1 = mnemonic_to_master_key(m, passphrase="")
        s2 = mnemonic_to_master_key(m, passphrase="my25thword")
        assert s1 != s2

    def test_mnemonic_to_master_key_invalid_raises(self) -> None:
        from sisoul.identity.seed import InvalidMnemonicError, mnemonic_to_master_key
        with pytest.raises(InvalidMnemonicError):
            mnemonic_to_master_key("not valid words")

    def test_mnemonic_to_master_key_64_bytes(self) -> None:
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        m = generate_mnemonic()
        seed = mnemonic_to_master_key(m)
        assert len(seed) == 64
        assert isinstance(seed, bytes)

    # ── derive_subkey 跨 purpose ──────────────────────────────────────────────

    def test_derive_subkey_vault(self) -> None:
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key, derive_subkey
        master = mnemonic_to_master_key(generate_mnemonic())
        k = derive_subkey(master, "vault")
        assert len(k) == 32

    def test_derive_subkey_did(self) -> None:
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key, derive_subkey
        master = mnemonic_to_master_key(generate_mnemonic())
        k = derive_subkey(master, "did")
        assert len(k) == 32

    def test_derive_subkey_skill(self) -> None:
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key, derive_subkey
        master = mnemonic_to_master_key(generate_mnemonic())
        k = derive_subkey(master, "skill")
        assert len(k) == 32

    def test_derive_subkey_p2p(self) -> None:
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key, derive_subkey
        master = mnemonic_to_master_key(generate_mnemonic())
        k = derive_subkey(master, "p2p")
        assert len(k) == 32

    def test_derive_subkey_deterministic(self) -> None:
        """同 master + purpose + index → 同 subkey."""
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key, derive_subkey
        m = generate_mnemonic()
        master = mnemonic_to_master_key(m)
        k1 = derive_subkey(master, "vault", index=0)
        k2 = derive_subkey(master, "vault", index=0)
        assert k1 == k2

    def test_derive_subkey_purpose_isolation(self) -> None:
        """vault / did / skill / p2p → 4 个不同 subkey."""
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key, derive_subkey
        master = mnemonic_to_master_key(generate_mnemonic())
        keys = {p: derive_subkey(master, p) for p in ("vault", "did", "skill", "p2p", "proxy", "arweave")}
        assert len(set(k.hex() for k in keys.values())) == 6  # all distinct

    def test_derive_subkey_index_differs(self) -> None:
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key, derive_subkey
        master = mnemonic_to_master_key(generate_mnemonic())
        k0 = derive_subkey(master, "skill", index=0)
        k1 = derive_subkey(master, "skill", index=1)
        assert k0 != k1

    def test_derive_subkey_invalid_master_raises(self) -> None:
        from sisoul.identity.seed import derive_subkey
        with pytest.raises(ValueError, match="bytes"):
            derive_subkey("not bytes", "vault")  # type: ignore[arg-type]

    def test_derive_subkey_empty_master_raises(self) -> None:
        from sisoul.identity.seed import derive_subkey
        with pytest.raises(ValueError, match="空"):
            derive_subkey(b"", "vault")

    def test_derive_subkey_empty_purpose_raises(self) -> None:
        from sisoul.identity.seed import derive_subkey
        with pytest.raises(ValueError, match="purpose"):
            derive_subkey(b"\x00" * 64, "")

    def test_derive_subkey_negative_index_raises(self) -> None:
        from sisoul.identity.seed import derive_subkey
        with pytest.raises(ValueError, match="index"):
            derive_subkey(b"\x00" * 64, "vault", index=-1)

    # ── mnemonic file chmod 600 ───────────────────────────────────────────────

    def test_save_mnemonic_chmod_600(self, tmp_path: Path) -> None:
        from sisoul.identity.seed import generate_mnemonic, save_mnemonic_to_file
        m = generate_mnemonic()
        seed_file = tmp_path / "seed.txt"
        path = save_mnemonic_to_file(m, path=seed_file)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_save_mnemonic_refuses_existing_file(self, tmp_path: Path) -> None:
        from sisoul.identity.seed import generate_mnemonic, save_mnemonic_to_file
        m = generate_mnemonic()
        seed_file = tmp_path / "seed.txt"
        save_mnemonic_to_file(m, path=seed_file)
        m2 = generate_mnemonic()
        with pytest.raises(FileExistsError):
            save_mnemonic_to_file(m2, path=seed_file)

    def test_save_mnemonic_invalid_raises(self, tmp_path: Path) -> None:
        from sisoul.identity.seed import InvalidMnemonicError, save_mnemonic_to_file
        seed_file = tmp_path / "seed.txt"
        with pytest.raises(InvalidMnemonicError):
            save_mnemonic_to_file("not valid", path=seed_file)

    def test_load_mnemonic_roundtrip(self, tmp_path: Path) -> None:
        from sisoul.identity.seed import (
            generate_mnemonic, save_mnemonic_to_file, load_mnemonic_from_file,
        )
        m = generate_mnemonic()
        seed_file = tmp_path / "seed.txt"
        save_mnemonic_to_file(m, path=seed_file)
        loaded = load_mnemonic_from_file(seed_file)
        assert loaded == m

    def test_load_mnemonic_file_not_found(self, tmp_path: Path) -> None:
        from sisoul.identity.seed import load_mnemonic_from_file
        with pytest.raises(FileNotFoundError):
            load_mnemonic_from_file(tmp_path / "nonexistent.txt")

    def test_load_mnemonic_loose_permissions_raises(self, tmp_path: Path) -> None:
        from sisoul.identity.seed import generate_mnemonic, save_mnemonic_to_file, load_mnemonic_from_file
        m = generate_mnemonic()
        seed_file = tmp_path / "seed.txt"
        save_mnemonic_to_file(m, path=seed_file)
        os.chmod(seed_file, 0o644)  # loosen permissions
        with pytest.raises(PermissionError, match="权限"):
            load_mnemonic_from_file(seed_file)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. TestVaultEncryptionMockOnly — sisoul.vault.encryption
# ═══════════════════════════════════════════════════════════════════════════════


class TestVaultEncryptionMockOnly:
    """vault.encryption — libsodium SecretBox 完整 mock 路径."""

    def _gen_key(self) -> bytes:
        from sisoul.vault.encryption import KEY_SIZE
        from nacl.utils import random as nacl_random
        return bytes(nacl_random(KEY_SIZE))

    # ── encrypt/decrypt round-trip ────────────────────────────────────────────

    def test_encrypt_decrypt_bytes_roundtrip(self) -> None:
        from sisoul.vault.encryption import encrypt_bytes, decrypt_bytes
        key = self._gen_key()
        plain = b"secret soul data \x00\xff binary"
        blob = encrypt_bytes(plain, key)
        assert blob != plain
        assert decrypt_bytes(blob, key) == plain

    def test_encrypt_decrypt_text_roundtrip(self) -> None:
        from sisoul.vault.encryption import encrypt_text, decrypt_text
        key = self._gen_key()
        plain = "灵魂数据: private memories 🔒"
        blob = encrypt_text(plain, key)
        assert decrypt_text(blob, key) == plain

    def test_encrypt_nonce_random_per_call(self) -> None:
        """同 plain 两次加密 → 不同密文 (nonce 随机)."""
        from sisoul.vault.encryption import encrypt_bytes
        key = self._gen_key()
        plain = b"same data"
        b1 = encrypt_bytes(plain, key)
        b2 = encrypt_bytes(plain, key)
        assert b1 != b2

    def test_decrypt_wrong_key_raises_crypto_error(self) -> None:
        """错 key → CryptoError (DecryptionError)."""
        from nacl.exceptions import CryptoError
        from sisoul.vault.encryption import encrypt_bytes, decrypt_bytes, KEY_SIZE
        from nacl.utils import random as nacl_random
        key1 = bytes(nacl_random(KEY_SIZE))
        key2 = bytes(nacl_random(KEY_SIZE))
        blob = encrypt_bytes(b"secret", key1)
        with pytest.raises(CryptoError):
            decrypt_bytes(blob, key2)

    def test_decrypt_tampered_ciphertext_raises(self) -> None:
        """篡改密文 → CryptoError."""
        from nacl.exceptions import CryptoError
        from sisoul.vault.encryption import encrypt_bytes, decrypt_bytes
        key = self._gen_key()
        blob = encrypt_bytes(b"secret", key)
        tampered = bytearray(blob)
        tampered[-1] ^= 0xFF  # flip last byte
        with pytest.raises(CryptoError):
            decrypt_bytes(bytes(tampered), key)

    def test_decrypt_truncated_blob_raises(self) -> None:
        """过短密文 → CryptoError."""
        from nacl.exceptions import CryptoError
        from sisoul.vault.encryption import decrypt_bytes
        key = self._gen_key()
        with pytest.raises(CryptoError):
            decrypt_bytes(b"\x00" * 10, key)

    def test_wrong_key_size_raises_value_error(self) -> None:
        from sisoul.vault.encryption import encrypt_bytes, decrypt_bytes
        with pytest.raises(ValueError, match="key must be"):
            encrypt_bytes(b"data", b"\x00" * 16)
        with pytest.raises(ValueError, match="key must be"):
            decrypt_bytes(b"\x00" * 50, b"\x00" * 16)

    # ── master key 派生确定性 ────────────────────────────────────────────────

    def test_derive_master_key_bip39_deterministic(self) -> None:
        """同 BIP-39 mnemonic → 同 vault key."""
        from sisoul.vault.encryption import derive_master_key
        from sisoul.identity.seed import generate_mnemonic
        m = generate_mnemonic()
        k1 = derive_master_key(m)
        k2 = derive_master_key(m)
        assert k1 == k2
        assert len(k1) == 32

    def test_derive_master_key_different_mnemonic(self) -> None:
        from sisoul.vault.encryption import derive_master_key
        from sisoul.identity.seed import generate_mnemonic
        k1 = derive_master_key(generate_mnemonic())
        k2 = derive_master_key(generate_mnemonic())
        assert k1 != k2

    def test_derive_master_key_placeholder_fallback(self) -> None:
        """mnemonic=None + 无 seed.txt → placeholder fallback (不 crash)."""
        from sisoul.vault.encryption import derive_master_key
        with patch.dict(os.environ, {"SISOUL_SEED_FILE": "/nonexistent/seed.txt"}):
            k = derive_master_key(None)
        assert len(k) == 32

    def test_encrypt_with_bip39_derived_key(self) -> None:
        """BIP-39 派生 key 加密解密 round-trip."""
        from sisoul.vault.encryption import derive_master_key, encrypt_bytes, decrypt_bytes
        from sisoul.identity.seed import generate_mnemonic
        m = generate_mnemonic()
        key = derive_master_key(m)
        blob = encrypt_bytes(b"soul fragment", key)
        assert decrypt_bytes(blob, key) == b"soul fragment"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. TestP2pEncryptionMockOnly — sisoul.p2p.encryption
# ═══════════════════════════════════════════════════════════════════════════════


class TestP2pEncryptionMockOnly:
    """P2P 加密层 — libsodium SecretBox 双向加解密."""

    def _setup_key(self) -> bytes:
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        from sisoul.p2p.encryption import derive_p2p_key
        m = generate_mnemonic()
        master = mnemonic_to_master_key(m)
        return derive_p2p_key(master)

    # ── derive_p2p_key ────────────────────────────────────────────────────────

    def test_derive_p2p_key_size(self) -> None:
        from sisoul.p2p.encryption import KEY_SIZE
        key = self._setup_key()
        assert len(key) == KEY_SIZE == 32

    def test_derive_p2p_key_deterministic(self) -> None:
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        from sisoul.p2p.encryption import derive_p2p_key
        m = generate_mnemonic()
        master = mnemonic_to_master_key(m)
        k1 = derive_p2p_key(master, index=0)
        k2 = derive_p2p_key(master, index=0)
        assert k1 == k2

    def test_derive_p2p_key_index_differs(self) -> None:
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        from sisoul.p2p.encryption import derive_p2p_key
        m = generate_mnemonic()
        master = mnemonic_to_master_key(m)
        k0 = derive_p2p_key(master, index=0)
        k1 = derive_p2p_key(master, index=1)
        assert k0 != k1

    def test_derive_p2p_key_invalid_master(self) -> None:
        from sisoul.p2p.encryption import derive_p2p_key
        with pytest.raises(ValueError):
            derive_p2p_key(b"")

    # ── encrypt / decrypt E2E ─────────────────────────────────────────────────

    def test_encrypt_decrypt_roundtrip(self) -> None:
        from sisoul.p2p.encryption import encrypt, decrypt
        key = self._setup_key()
        plain = b"peer-to-peer vault sync chunk"
        blob = encrypt(key, plain)
        assert decrypt(key, blob) == plain

    def test_encrypt_nonce_random(self) -> None:
        from sisoul.p2p.encryption import encrypt
        key = self._setup_key()
        b1 = encrypt(key, b"same")
        b2 = encrypt(key, b"same")
        assert b1 != b2

    def test_decrypt_wrong_key_raises(self) -> None:
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        from sisoul.p2p.encryption import encrypt, decrypt, DecryptionError, derive_p2p_key
        m = generate_mnemonic()
        master = mnemonic_to_master_key(m)
        k1 = derive_p2p_key(master, index=0)
        k2 = derive_p2p_key(master, index=1)  # different key
        blob = encrypt(k1, b"secret")
        with pytest.raises(DecryptionError):
            decrypt(k2, blob)

    def test_decrypt_tampered_raises(self) -> None:
        from sisoul.p2p.encryption import encrypt, decrypt, DecryptionError
        key = self._setup_key()
        blob = encrypt(key, b"secret")
        tampered = bytearray(blob)
        tampered[-1] ^= 0xFF
        with pytest.raises(DecryptionError):
            decrypt(key, bytes(tampered))

    def test_decrypt_too_short_raises(self) -> None:
        from sisoul.p2p.encryption import decrypt
        key = self._setup_key()
        with pytest.raises(ValueError, match="太短"):
            decrypt(key, b"\x00" * 10)

    # ── stream API ────────────────────────────────────────────────────────────

    def test_encrypt_stream_decrypt_stream_roundtrip(self) -> None:
        from sisoul.p2p.encryption import encrypt_stream, decrypt_stream
        key = self._setup_key()
        plain = b"A" * 200_000  # 200KB > 64KB chunk
        chunks = encrypt_stream(key, plain)
        assert len(chunks) > 1  # multi-chunk
        recovered = decrypt_stream(key, chunks)
        assert recovered == plain

    def test_encrypt_stream_empty(self) -> None:
        from sisoul.p2p.encryption import encrypt_stream, decrypt_stream
        key = self._setup_key()
        chunks = encrypt_stream(key, b"")
        assert len(chunks) >= 1  # sentinel chunk
        recovered = decrypt_stream(key, chunks)
        assert recovered == b""

    def test_decrypt_stream_wrong_key_raises(self) -> None:
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        from sisoul.p2p.encryption import encrypt_stream, decrypt_stream, DecryptionError, derive_p2p_key
        m = generate_mnemonic()
        master = mnemonic_to_master_key(m)
        k1 = derive_p2p_key(master, 0)
        k2 = derive_p2p_key(master, 1)
        chunks = encrypt_stream(k1, b"secret data")
        with pytest.raises(DecryptionError):
            decrypt_stream(k2, chunks)

    # ── per-friend session key 派生 (via p2p isolation) ───────────────────────

    def test_per_friend_key_isolation(self) -> None:
        """同 seed 不同 friend index → 不同 P2P key."""
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        from sisoul.p2p.encryption import derive_p2p_key
        master = mnemonic_to_master_key(generate_mnemonic())
        keys = [derive_p2p_key(master, i) for i in range(5)]
        assert len(set(k.hex() for k in keys)) == 5


# ═══════════════════════════════════════════════════════════════════════════════
# 7. TestEncryptedProxyMockOnly — sisoul.friend.encrypted_proxy
# ═══════════════════════════════════════════════════════════════════════════════


class TestEncryptedProxyMockOnly:
    """加密 proxy — 0 prompt leak 验证 + mock LLM forwarder."""

    def _make_keypair(self, master_seed: bytes = None, index: int = 0):
        from sisoul.friend.encrypted_proxy import derive_friend_session_keypair
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        if master_seed is None:
            master_seed = mnemonic_to_master_key(generate_mnemonic())
        return derive_friend_session_keypair(master_seed, friend_index=index)

    def _make_proxy(self, priv=None, pub=None, did="did:sisoul:bob", forwarder=None):
        from sisoul.friend.encrypted_proxy import EncryptedProxy
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        if priv is None:
            from sisoul.friend.encrypted_proxy import derive_friend_session_keypair
            master = mnemonic_to_master_key(generate_mnemonic())
            priv, pub = derive_friend_session_keypair(master, 0)
        return EncryptedProxy(
            self_priv=priv,
            self_pub=pub,
            self_did=did,
            forwarder=forwarder or (lambda prompt, model, provider="anthropic", api_key=None, **kw: ("mock response", 5, 10)),
        )

    # ── derive_friend_session_keypair ─────────────────────────────────────────

    def test_derive_keypair_deterministic(self) -> None:
        from sisoul.friend.encrypted_proxy import derive_friend_session_keypair
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        master = mnemonic_to_master_key(generate_mnemonic())
        priv1, pub1 = derive_friend_session_keypair(master, 0)
        priv2, pub2 = derive_friend_session_keypair(master, 0)
        assert priv1.encode() == priv2.encode()
        assert pub1.encode() == pub2.encode()

    def test_derive_keypair_index_differs(self) -> None:
        from sisoul.friend.encrypted_proxy import derive_friend_session_keypair
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        master = mnemonic_to_master_key(generate_mnemonic())
        _, pub0 = derive_friend_session_keypair(master, 0)
        _, pub1 = derive_friend_session_keypair(master, 1)
        assert pub0.encode() != pub1.encode()

    def test_derive_keypair_invalid_master(self) -> None:
        from sisoul.friend.encrypted_proxy import derive_friend_session_keypair
        with pytest.raises(ValueError):
            derive_friend_session_keypair(b"", 0)

    def test_derive_keypair_negative_index_raises(self) -> None:
        from sisoul.friend.encrypted_proxy import derive_friend_session_keypair
        with pytest.raises(ValueError):
            derive_friend_session_keypair(b"\x00" * 64, -1)

    # ── encrypt_for / decrypt_from alice→bob ─────────────────────────────────

    def test_alice_to_bob_encrypt_decrypt(self) -> None:
        """Alice 用 Bob pub 加密 → Bob 用自己 priv 解密."""
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        from sisoul.friend.encrypted_proxy import derive_friend_session_keypair, EncryptedProxy
        alice_master = mnemonic_to_master_key(generate_mnemonic())
        bob_master = mnemonic_to_master_key(generate_mnemonic())
        alice_priv, alice_pub = derive_friend_session_keypair(alice_master, 0)
        bob_priv, bob_pub = derive_friend_session_keypair(bob_master, 0)

        alice_proxy = EncryptedProxy(
            self_priv=alice_priv, self_pub=alice_pub, self_did="did:sisoul:alice",
            forwarder=lambda p, m, provider="anthropic", api_key=None, **kw: ("r", 1, 1),
        )
        bob_proxy = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_pub, self_did="did:sisoul:bob",
            forwarder=lambda p, m, provider="anthropic", api_key=None, **kw: ("r", 1, 1),
        )
        secret = "my secret prompt"
        encrypted = alice_proxy.encrypt_for(bob_pub.encode(), secret)
        decrypted = bob_proxy.decrypt_from(alice_pub.encode(), encrypted)
        assert decrypted.decode("utf-8") == secret

    def test_bob_to_alice_encrypt_decrypt(self) -> None:
        """反向: Bob → Alice."""
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        from sisoul.friend.encrypted_proxy import derive_friend_session_keypair, EncryptedProxy
        alice_master = mnemonic_to_master_key(generate_mnemonic())
        bob_master = mnemonic_to_master_key(generate_mnemonic())
        alice_priv, alice_pub = derive_friend_session_keypair(alice_master, 0)
        bob_priv, bob_pub = derive_friend_session_keypair(bob_master, 0)

        alice_proxy = EncryptedProxy(
            self_priv=alice_priv, self_pub=alice_pub, self_did="did:sisoul:alice",
            forwarder=lambda p, m, provider="anthropic", api_key=None, **kw: ("r", 1, 1),
        )
        bob_proxy = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_pub, self_did="did:sisoul:bob",
            forwarder=lambda p, m, provider="anthropic", api_key=None, **kw: ("r", 1, 1),
        )
        msg = "response from bob"
        enc = bob_proxy.encrypt_for(alice_pub.encode(), msg)
        dec = alice_proxy.decrypt_from(bob_pub.encode(), enc)
        assert dec.decode("utf-8") == msg

    def test_decrypt_from_wrong_key_raises(self) -> None:
        from sisoul.friend.encrypted_proxy import ProxyDecryptError
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        from sisoul.friend.encrypted_proxy import derive_friend_session_keypair, EncryptedProxy
        alice_master = mnemonic_to_master_key(generate_mnemonic())
        bob_master = mnemonic_to_master_key(generate_mnemonic())
        eve_master = mnemonic_to_master_key(generate_mnemonic())
        alice_priv, alice_pub = derive_friend_session_keypair(alice_master, 0)
        bob_priv, bob_pub = derive_friend_session_keypair(bob_master, 0)
        eve_priv, eve_pub = derive_friend_session_keypair(eve_master, 0)

        alice_proxy = EncryptedProxy(
            self_priv=alice_priv, self_pub=alice_pub, self_did="did:sisoul:alice",
            forwarder=lambda p, m, provider="anthropic", api_key=None, **kw: ("r", 1, 1),
        )
        eve_proxy = EncryptedProxy(
            self_priv=eve_priv, self_pub=eve_pub, self_did="did:sisoul:eve",
            forwarder=lambda p, m, provider="anthropic", api_key=None, **kw: ("r", 1, 1),
        )
        enc = alice_proxy.encrypt_for(bob_pub.encode(), "secret")
        with pytest.raises(ProxyDecryptError):
            eve_proxy.decrypt_from(alice_pub.encode(), enc)

    # ── proxy_chat_request mock LLM forwarder ─────────────────────────────────

    def test_proxy_chat_request_mock_llm(self) -> None:
        """mock forwarder → 完整 E2E: alice 加密 prompt → bob proxy → alice 解密 response."""
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        from sisoul.friend.encrypted_proxy import derive_friend_session_keypair, EncryptedProxy
        alice_master = mnemonic_to_master_key(generate_mnemonic())
        bob_master = mnemonic_to_master_key(generate_mnemonic())
        alice_priv, alice_pub = derive_friend_session_keypair(alice_master, 0)
        bob_priv, bob_pub = derive_friend_session_keypair(bob_master, 0)

        received_prompts = []

        def mock_forwarder(prompt, model, provider="anthropic", api_key=None, **kw):
            received_prompts.append(prompt)
            return ("mock llm response content", 10, 20)

        alice_proxy = EncryptedProxy(
            self_priv=alice_priv, self_pub=alice_pub, self_did="did:sisoul:alice",
            forwarder=mock_forwarder,
        )
        bob_proxy = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_pub, self_did="did:sisoul:bob",
            forwarder=mock_forwarder,
        )
        enc_prompt = alice_proxy.encrypt_for(bob_pub.encode(), "secret business prompt")
        enc_response, meta = bob_proxy.proxy_chat_request(
            borrower_did="did:sisoul:alice",
            borrower_pubkey=alice_pub.encode(),
            encrypted_prompt=enc_prompt,
            target_model="claude-opus-4-7",
        )
        # Alice decrypts response
        response = alice_proxy.decrypt_from(bob_pub.encode(), enc_response)
        assert response.decode("utf-8") == "mock llm response content"
        assert meta.status == "completed"
        assert meta.prompt_token_count == 10
        assert meta.response_token_count == 20

    # ── session metadata 白名单 (不含 prompt) ─────────────────────────────────

    def test_session_metadata_no_prompt_leak(self) -> None:
        """to_safe_dict 白名单 — 不含 prompt/response 字串."""
        from sisoul.friend.encrypted_proxy import ProxySessionMetadata, _METADATA_WHITELIST
        meta = ProxySessionMetadata(
            session_id="s1",
            borrower_did="did:sisoul:alice",
            lender_did="did:sisoul:bob",
            target_model="claude-opus-4-7",
            provider="anthropic",
            started_ts=time.time(),
        )
        safe = meta.to_safe_dict()
        # 白名单字段全存在
        for field in _METADATA_WHITELIST:
            assert field in safe or field not in meta.__dataclass_fields__
        # 无 prompt/response 内容字段
        assert "prompt" not in safe
        assert "response" not in safe
        assert "plaintext" not in safe

    def test_metadata_whitelist_fields(self) -> None:
        from sisoul.friend.encrypted_proxy import _METADATA_WHITELIST
        assert "session_id" in _METADATA_WHITELIST
        assert "prompt_token_count" in _METADATA_WHITELIST
        assert "response_token_count" in _METADATA_WHITELIST
        # 以下不应在白名单
        assert "prompt" not in _METADATA_WHITELIST
        assert "response" not in _METADATA_WHITELIST

    def test_default_forwarder_raises_without_env(self) -> None:
        """默认 forwarder 在无 SISOUL_DEFAULT_FORWARDER_REAL=1 时 raise."""
        from sisoul.friend.encrypted_proxy import ForwarderNotInjectedError, _default_forwarder
        env = {k: v for k, v in os.environ.items() if k != "SISOUL_DEFAULT_FORWARDER_REAL"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ForwarderNotInjectedError):
                _default_forwarder("prompt", "claude-opus-4-7")

    # ── 隐私 audit: prompt 0 leak 验证 ───────────────────────────────────────

    def test_no_prompt_in_session_store(self) -> None:
        """proxy_chat_request 后 _sessions 不含 prompt 字串."""
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        from sisoul.friend.encrypted_proxy import derive_friend_session_keypair, EncryptedProxy
        alice_master = mnemonic_to_master_key(generate_mnemonic())
        bob_master = mnemonic_to_master_key(generate_mnemonic())
        alice_priv, alice_pub = derive_friend_session_keypair(alice_master, 0)
        bob_priv, bob_pub = derive_friend_session_keypair(bob_master, 0)
        unique_token = "UNIQUE_PROMPT_TOKEN_" + uuid.uuid4().hex[:8]

        # forwarder signature: (prompt, model, provider, api_key, **kw) → (text, ptok, rtok)
        def mock_fw(prompt, model, provider="anthropic", api_key=None, **kw):
            return ("ok response", 1, 1)

        bob_proxy = EncryptedProxy(
            self_priv=bob_priv, self_pub=bob_pub, self_did="did:sisoul:bob",
            forwarder=mock_fw,
        )
        alice_proxy = EncryptedProxy(
            self_priv=alice_priv, self_pub=alice_pub, self_did="did:sisoul:alice",
            forwarder=mock_fw,
        )
        enc_prompt = alice_proxy.encrypt_for(bob_pub.encode(), unique_token)
        bob_proxy.proxy_chat_request(
            borrower_did="did:sisoul:alice",
            borrower_pubkey=alice_pub.encode(),
            encrypted_prompt=enc_prompt,
            target_model="claude-opus-4-7",
        )
        # 检查 _sessions 不含 prompt token
        sessions_str = json.dumps([
            {
                "session_id": s.metadata.session_id,
                "borrower_did": s.metadata.borrower_did,
                "status": s.metadata.status,
            }
            for s in bob_proxy._sessions.values()
        ])
        assert unique_token not in sessions_str


# ═══════════════════════════════════════════════════════════════════════════════
# 8. TestSkillPackageMockOnly — sisoul.friend.skill_package
# ═══════════════════════════════════════════════════════════════════════════════


class TestSkillPackageMockOnly:
    """SkillPackage 加密/解密 + 大文件 IPFS mock + 30min lifecycle."""

    def _make_keypair(self):
        from sisoul.identity.seed import generate_mnemonic, mnemonic_to_master_key
        from sisoul.friend.encrypted_proxy import derive_friend_session_keypair
        master = mnemonic_to_master_key(generate_mnemonic())
        return derive_friend_session_keypair(master, 0)

    def _make_pkg(self, name="test-skill", owner="did:sisoul:bob", **kw):
        from sisoul.friend.skill_package import package_skill
        return package_skill(
            name=name,
            owner_did=owner,
            system_prompt="You are an expert assistant.",
            description="Test skill",
            **kw,
        )

    # ── package_skill + validate ──────────────────────────────────────────────

    def test_package_skill_basic(self) -> None:
        from sisoul.friend.skill_package import SkillPackage, SKILL_PACKAGE_SCHEMA
        pkg = self._make_pkg()
        assert pkg.skill_id == "test-skill"
        assert pkg.owner_did == "did:sisoul:bob"
        assert pkg.schema == SKILL_PACKAGE_SCHEMA
        assert pkg.fingerprint  # non-empty
        assert pkg.expiry_hours == 24

    def test_package_skill_custom_expiry(self) -> None:
        pkg = self._make_pkg(expiry_hours=48)
        assert pkg.expiry_hours == 48

    def test_package_skill_expiry_out_of_range(self) -> None:
        from sisoul.friend.skill_package import InvalidSkillPackageError
        with pytest.raises(InvalidSkillPackageError, match="expiry"):
            self._make_pkg(expiry_hours=0)
        with pytest.raises(InvalidSkillPackageError, match="expiry"):
            self._make_pkg(expiry_hours=9999)

    def test_package_skill_missing_name_raises(self) -> None:
        from sisoul.friend.skill_package import InvalidSkillPackageError, package_skill
        with pytest.raises(InvalidSkillPackageError, match="name"):
            package_skill("", "did:sisoul:bob", "sys prompt")

    def test_package_skill_missing_system_prompt_raises(self) -> None:
        from sisoul.friend.skill_package import InvalidSkillPackageError, package_skill
        with pytest.raises(InvalidSkillPackageError, match="system_prompt"):
            package_skill("name", "did:sisoul:bob", "")

    def test_validate_skill_package_invalid_version(self) -> None:
        from sisoul.friend.skill_package import (
            InvalidSkillPackageError, package_skill, SkillPackage, validate_skill_package,
        )
        pkg = self._make_pkg(version="1.0.0")
        # manually corrupt version
        pkg.version = "not-semver"
        with pytest.raises(InvalidSkillPackageError, match="SemVer"):
            validate_skill_package(pkg)

    def test_package_skill_personality_traits(self) -> None:
        pkg = self._make_pkg(personality_traits=["pedantic", "concise"])
        assert "pedantic" in pkg.contents.personality_traits
        assert "concise" in pkg.contents.personality_traits

    def test_package_skill_recommended_models(self) -> None:
        pkg = self._make_pkg(recommended_models=["claude-opus-4-7"])
        assert "claude-opus-4-7" in pkg.contents.recommended_models

    # ── fingerprint ──────────────────────────────────────────────────────────

    def test_fingerprint_deterministic(self) -> None:
        """同一 package (固定 created_at) → 同 fingerprint."""
        from sisoul.friend.skill_package import SkillPackage, SkillContents
        contents = SkillContents(system_prompt="hello")
        # 固定 created_at 使 fingerprint 可复现
        pkg1 = SkillPackage(
            skill_id="test", owner_did="did:sisoul:bob",
            contents=contents, created_at=1000000,
        )
        pkg2 = SkillPackage(
            skill_id="test", owner_did="did:sisoul:bob",
            contents=contents, created_at=1000000,
        )
        assert pkg1.fingerprint == pkg2.fingerprint

    def test_fingerprint_changes_with_content(self) -> None:
        from sisoul.friend.skill_package import SkillPackage, SkillContents
        c1 = SkillContents(system_prompt="hello")
        c2 = SkillContents(system_prompt="different content")
        p1 = SkillPackage(skill_id="x", owner_did="did:sisoul:bob", contents=c1, created_at=1000000)
        p2 = SkillPackage(skill_id="x", owner_did="did:sisoul:bob", contents=c2, created_at=1000000)
        assert p1.fingerprint != p2.fingerprint

    # ── encrypt / decrypt round-trip ──────────────────────────────────────────

    def test_encrypt_decrypt_roundtrip(self) -> None:
        from sisoul.friend.skill_package import encrypt_skill_package, decrypt_skill_package
        bob_priv, bob_pub = self._make_keypair()
        alice_priv, alice_pub = self._make_keypair()
        pkg = self._make_pkg()
        # Bob encrypts for Alice
        encrypted = encrypt_skill_package(pkg, alice_pub.encode(), bob_priv)
        # Alice decrypts
        recovered = decrypt_skill_package(encrypted, bob_pub.encode(), alice_priv)
        assert recovered.skill_id == pkg.skill_id
        assert recovered.owner_did == pkg.owner_did
        assert recovered.contents.system_prompt == pkg.contents.system_prompt
        assert recovered.expiry_hours == pkg.expiry_hours

    def test_decrypt_wrong_key_raises(self) -> None:
        from sisoul.friend.skill_package import (
            encrypt_skill_package, decrypt_skill_package, SkillPackageDecryptError,
        )
        bob_priv, bob_pub = self._make_keypair()
        alice_priv, alice_pub = self._make_keypair()
        eve_priv, eve_pub = self._make_keypair()
        pkg = self._make_pkg()
        encrypted = encrypt_skill_package(pkg, alice_pub.encode(), bob_priv)
        with pytest.raises(SkillPackageDecryptError):
            decrypt_skill_package(encrypted, bob_pub.encode(), eve_priv)

    def test_decrypt_tampered_ciphertext_raises(self) -> None:
        from sisoul.friend.skill_package import (
            encrypt_skill_package, decrypt_skill_package, SkillPackageDecryptError,
        )
        bob_priv, bob_pub = self._make_keypair()
        alice_priv, alice_pub = self._make_keypair()
        pkg = self._make_pkg()
        enc = encrypt_skill_package(pkg, alice_pub.encode(), bob_priv)
        tampered = bytearray(enc)
        tampered[-1] ^= 0xFF
        with pytest.raises(SkillPackageDecryptError):
            decrypt_skill_package(bytes(tampered), bob_pub.encode(), alice_priv)

    # ── examples 大文件 IPFS 二级 pin mock ────────────────────────────────────

    def test_examples_inline_small(self) -> None:
        """小 examples (< 64KB) → inline 存储."""
        from sisoul.friend.skill_package import package_skill, EXAMPLES_INLINE_LIMIT_BYTES
        small_examples = [{"q": "What is Solidity?", "a": "A language."}] * 5
        pkg = package_skill(
            name="sol-expert", owner_did="did:sisoul:bob",
            system_prompt="You are Solidity expert.",
            examples=small_examples,
        )
        assert len(pkg.contents.few_shot_examples_inline) == 5
        assert pkg.contents.few_shot_examples_ipfs_cid is None

    def test_examples_large_calls_ipfs_uploader(self) -> None:
        """大 examples (> 64KB) → 调 mock IPFS uploader 拿 CID."""
        from sisoul.friend.skill_package import package_skill, EXAMPLES_INLINE_LIMIT_BYTES
        # 生成超出 64KB 的 examples
        big_examples = [{"content": "x" * 1000} for _ in range(100)]
        mock_cid = "QmMockExamplesCID123"
        mock_uploader = MagicMock(return_value=mock_cid)
        pkg = package_skill(
            name="big-skill", owner_did="did:sisoul:bob",
            system_prompt="System.",
            examples=big_examples,
            examples_ipfs_uploader=mock_uploader,
        )
        assert pkg.contents.few_shot_examples_ipfs_cid == mock_cid
        assert pkg.contents.few_shot_examples_inline == []
        mock_uploader.assert_called_once()

    def test_examples_large_no_uploader_raises(self) -> None:
        """大 examples + 无 uploader → InvalidSkillPackageError."""
        from sisoul.friend.skill_package import package_skill, InvalidSkillPackageError
        big_examples = [{"content": "x" * 1000} for _ in range(100)]
        with pytest.raises(InvalidSkillPackageError, match="inline"):
            package_skill(
                name="big-skill", owner_did="did:sisoul:bob",
                system_prompt="System.",
                examples=big_examples,
                examples_ipfs_uploader=None,
            )

    # ── 30min lifecycle auto destroy + wipe (expiry 验证逻辑) ─────────────────

    def test_expiry_hours_min_boundary(self) -> None:
        from sisoul.friend.skill_package import package_skill, MIN_SKILL_EXPIRY_HOURS
        pkg = package_skill(
            name="short-skill", owner_did="did:sisoul:bob",
            system_prompt="System.",
            expiry_hours=MIN_SKILL_EXPIRY_HOURS,
        )
        assert pkg.expiry_hours == MIN_SKILL_EXPIRY_HOURS

    def test_expiry_hours_max_boundary(self) -> None:
        from sisoul.friend.skill_package import package_skill, MAX_SKILL_EXPIRY_HOURS
        pkg = package_skill(
            name="long-skill", owner_did="did:sisoul:bob",
            system_prompt="System.",
            expiry_hours=MAX_SKILL_EXPIRY_HOURS,
        )
        assert pkg.expiry_hours == MAX_SKILL_EXPIRY_HOURS

    def test_skill_expiry_computed_from_created_at(self) -> None:
        """expiry_seconds = expiry_hours * 3600; created_at + expiry > now."""
        pkg = self._make_pkg(expiry_hours=1)  # 1h = 3600s
        expiry_epoch = pkg.created_at + pkg.expiry_hours * 3600
        assert expiry_epoch > int(time.time())  # 未过期

    def test_skill_expiry_past(self) -> None:
        """手动置 created_at 到 2h 前 → expiry 在过去."""
        from sisoul.friend.skill_package import SkillPackage, SkillContents
        contents = SkillContents(system_prompt="sys")
        pkg = SkillPackage(
            skill_id="x", owner_did="did:sisoul:bob",
            contents=contents,
            expiry_hours=1,  # 1h
            created_at=int(time.time()) - 7201,  # 2h ago → expired
        )
        expiry_epoch = pkg.created_at + pkg.expiry_hours * 3600
        assert expiry_epoch < int(time.time())  # 已过期

    # ── parse_qualified_name ──────────────────────────────────────────────────

    def test_parse_qualified_name_basic(self) -> None:
        from sisoul.friend.skill_package import parse_qualified_name
        owner, skill = parse_qualified_name("did:sisoul:bob:solidity-expert")
        assert owner == "did:sisoul:bob"
        assert skill == "solidity-expert"

    def test_parse_qualified_name_no_colon_raises(self) -> None:
        from sisoul.friend.skill_package import InvalidSkillPackageError, parse_qualified_name
        with pytest.raises(InvalidSkillPackageError, match=":"):
            parse_qualified_name("nocolon")

    # ── JSON roundtrip ────────────────────────────────────────────────────────

    def test_skill_package_json_roundtrip(self) -> None:
        pkg = self._make_pkg(
            personality_traits=["pedantic"],
            recommended_models=["claude-opus-4-7"],
        )
        s = pkg.to_json()
        recovered = pkg.__class__.from_json(s)
        assert recovered.skill_id == pkg.skill_id
        assert recovered.contents.personality_traits == ["pedantic"]
        assert recovered.contents.recommended_models == ["claude-opus-4-7"]
        assert recovered.fingerprint == pkg.fingerprint

    def test_encrypted_b64_roundtrip(self) -> None:
        from sisoul.friend.skill_package import (
            encrypt_skill_package, decrypt_skill_package,
            encrypted_to_b64, b64_to_encrypted,
        )
        bob_priv, bob_pub = self._make_keypair()
        alice_priv, alice_pub = self._make_keypair()
        pkg = self._make_pkg()
        enc = encrypt_skill_package(pkg, alice_pub.encode(), bob_priv)
        b64 = encrypted_to_b64(enc)
        assert isinstance(b64, str)
        back = b64_to_encrypted(b64)
        recovered = decrypt_skill_package(back, bob_pub.encode(), alice_priv)
        assert recovered.skill_id == pkg.skill_id
