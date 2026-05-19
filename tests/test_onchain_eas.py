"""tests for sisoul.onchain.eas — Phase 3 W37-W40 (波 4 dev-B).

覆盖:
- AuditAttestation 数据结构 + prompt_hash 归一
- AttestQueue SQLite enqueue / pending / mark_batched / stats / should_flush
- AttestConfig load_config / save_config
- upload_batch mock + live-readonly (mock RPC 通) + mainnet 禁
- verify_attestation_local recompute (valid + tampered → invalid)
- verify_attestation_onchain mock 返 not-found
- compute_attestation_uid 确定性
- resolve_attester_did 走本地 DID registry
- encode_attestation_data canonical
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from sisoul.onchain.eas import (
    AttestConfig,
    AttestQueue,
    AttestationNotFoundError,
    AuditAttestation,
    BatchResult,
    ConfigError,
    EASError,
    MOCK_SCHEMA_UID,
    NetworkNotSupportedError,
    OPTIMISM_SEPOLIA_CHAIN_ID,
    QueueEmptyError,
    SISOUL_AUDIT_SCHEMA,
    compute_attestation_uid,
    encode_attestation_data,
    list_history_local,
    list_history_onchain,
    load_config,
    resolve_attester_did,
    save_config,
    upload_batch,
    verify_attestation_local,
    verify_attestation_onchain,
)


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "queue.db"


@pytest.fixture
def tmp_cfg(tmp_path: Path) -> Path:
    return tmp_path / "attest_config.json"


@pytest.fixture
def sample_att() -> AuditAttestation:
    return AuditAttestation.from_audit_payload(
        actor_did="did:sisoul:alice",
        action_type="rm",
        target="/tmp/sensitive.txt",
        prompt="rm -rf /tmp/sensitive.txt",
        tool_name="claude-code",
    )


# ── AuditAttestation ─────────────────────────────────────────────────────────


class TestAuditAttestation:
    def test_from_audit_payload_sets_fields(self) -> None:
        att = AuditAttestation.from_audit_payload(
            actor_did="did:sisoul:alice",
            action_type="git-push",
            target="origin/main",
            prompt="git push --force",
            tool_name="claude-code",
        )
        assert att.actor_did == "did:sisoul:alice"
        assert att.action_type == "git-push"
        assert att.target == "origin/main"
        assert att.tool_name == "claude-code"
        assert att.prompt_hash.startswith("0x")
        # sha256("git push --force") 长度校验
        assert len(att.prompt_hash) == 66  # 0x + 64 hex
        assert att.timestamp > 0
        assert att.queue_id  # UUID
        assert att.queued_at
        assert att.status == "pending"

    def test_prompt_hash_normalization_short(self) -> None:
        att = AuditAttestation(
            actor_did="x",
            action_type="x",
            target="x",
            prompt_hash="abc",
            timestamp=0,
            tool_name="x",
        )
        # 短 hash 左 pad
        assert att.prompt_hash == "0x" + "abc".zfill(64)

    def test_prompt_hash_normalization_long(self) -> None:
        long_hex = "f" * 80
        att = AuditAttestation(
            actor_did="x",
            action_type="x",
            target="x",
            prompt_hash=long_hex,
            timestamp=0,
            tool_name="x",
        )
        # 长 hash 截到 64
        assert att.prompt_hash == "0x" + "f" * 64

    def test_to_dict_from_dict_roundtrip(self, sample_att: AuditAttestation) -> None:
        d = sample_att.to_dict()
        restored = AuditAttestation.from_dict(d)
        assert restored.queue_id == sample_att.queue_id
        assert restored.prompt_hash == sample_att.prompt_hash
        assert restored.actor_did == sample_att.actor_did

    def test_unique_queue_id(self) -> None:
        a = AuditAttestation.from_audit_payload("a", "b", "c", "d", "e")
        b = AuditAttestation.from_audit_payload("a", "b", "c", "d", "e")
        assert a.queue_id != b.queue_id


# ── AttestQueue ──────────────────────────────────────────────────────────────


class TestAttestQueue:
    def test_enqueue_and_pending(self, tmp_db: Path, sample_att: AuditAttestation) -> None:
        with AttestQueue(db_path=tmp_db) as q:
            qid = q.enqueue(sample_att)
            assert qid == sample_att.queue_id
            pending = q.pending()
            assert len(pending) == 1
            assert pending[0].queue_id == qid
            assert pending[0].status == "pending"

    def test_db_file_created(self, tmp_db: Path, sample_att: AuditAttestation) -> None:
        with AttestQueue(db_path=tmp_db) as q:
            q.enqueue(sample_att)
        assert tmp_db.exists()

    def test_stats(self, tmp_db: Path) -> None:
        with AttestQueue(db_path=tmp_db) as q:
            for i in range(3):
                q.enqueue(AuditAttestation.from_audit_payload(
                    "did:x", "rm", f"/p{i}", "rm", "cli"
                ))
            stats = q.stats()
            assert stats["pending"] == 3
            assert stats["confirmed"] == 0
            assert stats["batches"] == 0

    def test_should_flush_below_threshold_no_history(self, tmp_db: Path) -> None:
        with AttestQueue(db_path=tmp_db) as q:
            for i in range(3):
                q.enqueue(AuditAttestation.from_audit_payload(
                    "did:x", "rm", f"/p{i}", "rm", "cli"
                ))
            # 3 条 < 10 + 从未 flush → 不 flush (避免首次空闲触发)
            assert q.should_flush(batch_size=10, timeout_sec=3600) is False

    def test_should_flush_at_threshold(self, tmp_db: Path) -> None:
        with AttestQueue(db_path=tmp_db) as q:
            for i in range(10):
                q.enqueue(AuditAttestation.from_audit_payload(
                    "did:x", "rm", f"/p{i}", "rm", "cli"
                ))
            assert q.should_flush(batch_size=10, timeout_sec=3600) is True

    def test_should_flush_timeout(self, tmp_db: Path) -> None:
        with AttestQueue(db_path=tmp_db) as q:
            q.enqueue(AuditAttestation.from_audit_payload(
                "did:x", "rm", "/p", "rm", "cli"
            ))
            # 模拟很早的 last_flush_ts
            q._set_meta("last_flush_ts", str(int(time.time()) - 7200))
            q._conn.commit()
            assert q.should_flush(batch_size=10, timeout_sec=3600) is True

    def test_should_flush_empty_queue(self, tmp_db: Path) -> None:
        with AttestQueue(db_path=tmp_db) as q:
            assert q.should_flush(batch_size=10, timeout_sec=1) is False

    def test_mark_batched_updates_status(
        self, tmp_db: Path, sample_att: AuditAttestation
    ) -> None:
        with AttestQueue(db_path=tmp_db) as q:
            q.enqueue(sample_att)
            q.mark_batched(
                queue_ids=[sample_att.queue_id],
                batch_uid="batch-1",
                tx_hash="0xdeadbeef",
                attestation_uids=["0xabc"],
            )
            items = q.all_items(status="confirmed")
            assert len(items) == 1
            assert items[0].batch_uid == "batch-1"
            assert items[0].attestation_uid == "0xabc"
            assert items[0].tx_hash == "0xdeadbeef"

    def test_mark_batched_length_mismatch(
        self, tmp_db: Path, sample_att: AuditAttestation
    ) -> None:
        with AttestQueue(db_path=tmp_db) as q:
            q.enqueue(sample_att)
            with pytest.raises(EASError, match="长度不等于"):
                q.mark_batched(
                    queue_ids=[sample_att.queue_id],
                    batch_uid="b",
                    tx_hash="t",
                    attestation_uids=[],
                )

    def test_all_items_filter(self, tmp_db: Path) -> None:
        with AttestQueue(db_path=tmp_db) as q:
            a = AuditAttestation.from_audit_payload("did:x", "rm", "/a", "p", "cli")
            b = AuditAttestation.from_audit_payload("did:x", "rm", "/b", "p", "cli")
            q.enqueue(a)
            q.enqueue(b)
            q.mark_batched([a.queue_id], "B1", "tx1", ["uid1"])
            assert len(q.all_items(status="pending")) == 1
            assert len(q.all_items(status="confirmed")) == 1
            assert len(q.all_items(status=None)) == 2

    def test_find_by_attestation_uid(
        self, tmp_db: Path, sample_att: AuditAttestation
    ) -> None:
        with AttestQueue(db_path=tmp_db) as q:
            q.enqueue(sample_att)
            q.mark_batched([sample_att.queue_id], "B1", "tx1", ["0xMYUID"])
            found = q.find_by_attestation_uid("0xMYUID")
            assert found is not None
            assert found.queue_id == sample_att.queue_id
            assert q.find_by_attestation_uid("0xNOPE") is None


# ── AttestConfig ─────────────────────────────────────────────────────────────


class TestAttestConfig:
    def test_load_config_defaults_when_missing(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path / "nonexistent.json")
        assert cfg.network == "optimism-sepolia"
        assert cfg.batch_size == 10
        assert cfg.batch_timeout_sec == 3600
        assert cfg.schema_uid == MOCK_SCHEMA_UID

    def test_save_load_roundtrip(self, tmp_cfg: Path) -> None:
        cfg = AttestConfig(
            network="mock",
            rpc_url="http://localhost:8545",
            batch_size=5,
            batch_timeout_sec=60,
            attester_did="did:sisoul:bob",
        )
        save_config(cfg, tmp_cfg)
        assert tmp_cfg.exists()
        loaded = load_config(tmp_cfg)
        assert loaded.network == "mock"
        assert loaded.batch_size == 5
        assert loaded.attester_did == "did:sisoul:bob"

    def test_load_config_invalid_json(self, tmp_cfg: Path) -> None:
        tmp_cfg.parent.mkdir(parents=True, exist_ok=True)
        tmp_cfg.write_text("{not json")
        with pytest.raises(ConfigError, match="读 attest_config"):
            load_config(tmp_cfg)

    def test_from_dict_ignores_unknown_keys(self) -> None:
        cfg = AttestConfig.from_dict({"network": "mock", "unknown_key": "val"})
        assert cfg.network == "mock"


# ── encode + uid ─────────────────────────────────────────────────────────────


class TestEncoding:
    def test_encode_canonical_sorted(self, sample_att: AuditAttestation) -> None:
        data = encode_attestation_data(sample_att)
        parsed = json.loads(data.decode("utf-8"))
        # canonical: sort_keys
        assert list(parsed.keys()) == sorted(parsed.keys())

    def test_compute_attestation_uid_deterministic(
        self, sample_att: AuditAttestation
    ) -> None:
        uid1 = compute_attestation_uid(sample_att, MOCK_SCHEMA_UID, "batch-1")
        uid2 = compute_attestation_uid(sample_att, MOCK_SCHEMA_UID, "batch-1")
        assert uid1 == uid2
        assert uid1.startswith("0x")
        assert len(uid1) == 66

    def test_compute_attestation_uid_differs_on_batch(
        self, sample_att: AuditAttestation
    ) -> None:
        u1 = compute_attestation_uid(sample_att, MOCK_SCHEMA_UID, "B1")
        u2 = compute_attestation_uid(sample_att, MOCK_SCHEMA_UID, "B2")
        assert u1 != u2

    def test_compute_attestation_uid_differs_on_schema(
        self, sample_att: AuditAttestation
    ) -> None:
        u1 = compute_attestation_uid(sample_att, "0xSCHEMA1", "B1")
        u2 = compute_attestation_uid(sample_att, "0xSCHEMA2", "B1")
        assert u1 != u2


# ── upload_batch ─────────────────────────────────────────────────────────────


class TestUploadBatch:
    def test_mock_upload_success(self, tmp_db: Path) -> None:
        cfg = AttestConfig(network="mock", batch_size=3)
        with AttestQueue(db_path=tmp_db) as q:
            for i in range(3):
                q.enqueue(AuditAttestation.from_audit_payload(
                    "did:sisoul:alice", "rm", f"/f{i}", f"prompt{i}", "claude-code"
                ))
            result = upload_batch(q, cfg)

            assert result.count == 3
            assert result.network == "mock"
            assert result.method == "mock"
            assert result.tx_hash.startswith("0x")
            assert len(result.attestation_uids) == 3
            assert result.gas_used_estimate > 0
            # pending 应该清零
            assert q.stats()["pending"] == 0
            assert q.stats()["confirmed"] == 3

    def test_mainnet_rejected(self, tmp_db: Path, sample_att: AuditAttestation) -> None:
        cfg = AttestConfig(network="optimism-mainnet")
        with AttestQueue(db_path=tmp_db) as q:
            q.enqueue(sample_att)
            with pytest.raises(NetworkNotSupportedError, match="mainnet"):
                upload_batch(q, cfg)

    def test_empty_queue(self, tmp_db: Path) -> None:
        cfg = AttestConfig(network="mock")
        with AttestQueue(db_path=tmp_db) as q:
            with pytest.raises(QueueEmptyError):
                upload_batch(q, cfg)

    def test_batch_respects_batch_size(self, tmp_db: Path) -> None:
        cfg = AttestConfig(network="mock", batch_size=3)
        with AttestQueue(db_path=tmp_db) as q:
            for i in range(7):
                q.enqueue(AuditAttestation.from_audit_payload(
                    "did:x", "rm", f"/f{i}", "p", "cli"
                ))
            r = upload_batch(q, cfg)
            assert r.count == 3
            assert q.stats()["pending"] == 4

    def test_force_takes_all(self, tmp_db: Path) -> None:
        cfg = AttestConfig(network="mock", batch_size=3)
        with AttestQueue(db_path=tmp_db) as q:
            for i in range(7):
                q.enqueue(AuditAttestation.from_audit_payload(
                    "did:x", "rm", f"/f{i}", "p", "cli"
                ))
            r = upload_batch(q, cfg, force=True)
            assert r.count == 7
            assert q.stats()["pending"] == 0

    def test_max_items_override(self, tmp_db: Path) -> None:
        cfg = AttestConfig(network="mock", batch_size=10)
        with AttestQueue(db_path=tmp_db) as q:
            for i in range(5):
                q.enqueue(AuditAttestation.from_audit_payload(
                    "did:x", "rm", f"/f{i}", "p", "cli"
                ))
            r = upload_batch(q, cfg, force=True, max_items=2)
            assert r.count == 2

    def test_live_readonly_rpc_success(
        self, tmp_db: Path, sample_att: AuditAttestation
    ) -> None:
        """mock httpx, 返 valid chain_id, 应进入 live-readonly 路径."""
        cfg = AttestConfig(
            network="optimism-sepolia",
            rpc_url="https://sepolia.optimism.io",
            batch_size=10,
        )
        with AttestQueue(db_path=tmp_db) as q:
            q.enqueue(sample_att)
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "result": hex(OPTIMISM_SEPOLIA_CHAIN_ID)
            }
            mock_response.raise_for_status = MagicMock()
            with patch("httpx.post", return_value=mock_response) as mp:
                r = upload_batch(q, cfg)
                mp.assert_called_once()
            assert r.method == "live-readonly"
            assert r.count == 1
            assert r.network == "optimism-sepolia"

    def test_live_readonly_rpc_chain_mismatch(
        self, tmp_db: Path, sample_att: AuditAttestation
    ) -> None:
        """RPC 返错 chain_id → fail-open 退到 mock."""
        cfg = AttestConfig(network="optimism-sepolia", rpc_url="https://x")
        with AttestQueue(db_path=tmp_db) as q:
            q.enqueue(sample_att)
            bad = MagicMock()
            bad.json.return_value = {"result": hex(1)}  # mainnet chain id, 不匹配
            bad.raise_for_status = MagicMock()
            with patch("httpx.post", return_value=bad):
                r = upload_batch(q, cfg)
            assert r.method == "mock"  # fail-open
            assert r.network == "optimism-sepolia"

    def test_live_tx_path_aborts_safely(self, tmp_path: Path, tmp_db: Path) -> None:
        """配 private_key_path 触发 live-tx 路径; 本 wave 故意 abort 防误花钱."""
        pk_file = tmp_path / "pk.hex"
        pk_file.write_text("0x" + "1" * 64)
        cfg = AttestConfig(
            network="optimism-sepolia",
            rpc_url="https://sepolia.optimism.io",
            private_key_path=str(pk_file),
        )
        with AttestQueue(db_path=tmp_db) as q:
            q.enqueue(AuditAttestation.from_audit_payload(
                "did:x", "rm", "/f", "p", "cli"
            ))
            # web3 / eth_account 可能未装也行: ImportError 或 abort EASError 都接受
            with pytest.raises(EASError):
                upload_batch(q, cfg)


# ── verify ───────────────────────────────────────────────────────────────────


class TestVerifyLocal:
    def test_verify_found_and_valid(
        self, tmp_db: Path, sample_att: AuditAttestation
    ) -> None:
        cfg = AttestConfig(network="mock", batch_size=1)
        with AttestQueue(db_path=tmp_db) as q:
            q.enqueue(sample_att)
            r = upload_batch(q, cfg)
            uid = r.attestation_uids[0]
            v = verify_attestation_local(q, uid)
            assert v["valid"] is True
            assert v["method"] == "local-recompute"
            assert v["attestation"]["queue_id"] == sample_att.queue_id

    def test_verify_not_found(self, tmp_db: Path) -> None:
        with AttestQueue(db_path=tmp_db) as q:
            with pytest.raises(AttestationNotFoundError):
                verify_attestation_local(q, "0xNOPE")

    def test_verify_tampered_detected(
        self, tmp_db: Path, sample_att: AuditAttestation
    ) -> None:
        """直接改 DB 里 target → 重算 UID 不再等于原值 → invalid."""
        cfg = AttestConfig(network="mock", batch_size=1)
        with AttestQueue(db_path=tmp_db) as q:
            q.enqueue(sample_att)
            r = upload_batch(q, cfg)
            uid = r.attestation_uids[0]

            # 篡改: 把 target 改成别的
            q._conn.execute(
                "UPDATE attest_queue SET target='/tmp/tampered' WHERE queue_id=?",
                (sample_att.queue_id,),
            )
            q._conn.commit()
            v = verify_attestation_local(q, uid)
            assert v["valid"] is False
            assert v["expected_uid"] != v["given_uid"]


class TestVerifyOnchain:
    def test_onchain_mock_returns_invalid(self) -> None:
        r = verify_attestation_onchain("0xabc", network="mock")
        assert r["valid"] is False
        assert "mock" in r["reason"]

    def test_onchain_mainnet_rejected(self) -> None:
        with pytest.raises(NetworkNotSupportedError):
            verify_attestation_onchain("0xabc", network="optimism-mainnet")

    def test_onchain_graphql_not_found(self) -> None:
        """mock GraphQL 返空 attestation."""
        bad = MagicMock()
        bad.json.return_value = {"data": {"attestation": None}}
        bad.raise_for_status = MagicMock()
        with patch("httpx.post", return_value=bad):
            r = verify_attestation_onchain("0xabc", network="optimism-sepolia")
            assert r["valid"] is False
            assert "未上链" in r["reason"]

    def test_onchain_graphql_found(self) -> None:
        good = MagicMock()
        good.json.return_value = {
            "data": {
                "attestation": {
                    "id": "0xabc",
                    "attester": "0xdead",
                    "recipient": "0xbeef",
                    "schemaId": "0xschema",
                    "time": 1234567890,
                    "data": "0xdata",
                }
            }
        }
        good.raise_for_status = MagicMock()
        with patch("httpx.post", return_value=good):
            r = verify_attestation_onchain("0xabc", network="optimism-sepolia")
            assert r["valid"] is True
            assert r["data"]["id"] == "0xabc"

    def test_onchain_graphql_http_error(self) -> None:
        with patch("httpx.post", side_effect=RuntimeError("boom")):
            r = verify_attestation_onchain("0xabc", network="optimism-sepolia")
            assert r["valid"] is False
            assert "GraphQL" in r["reason"]


# ── history ──────────────────────────────────────────────────────────────────


class TestHistory:
    def test_history_local_empty(self, tmp_db: Path) -> None:
        with AttestQueue(db_path=tmp_db) as q:
            assert list_history_local(q) == []

    def test_history_local_after_batch(self, tmp_db: Path) -> None:
        cfg = AttestConfig(network="mock", batch_size=2)
        with AttestQueue(db_path=tmp_db) as q:
            for i in range(2):
                q.enqueue(AuditAttestation.from_audit_payload(
                    "did:x", "rm", f"/f{i}", "p", "cli"
                ))
            upload_batch(q, cfg)
            batches = list_history_local(q)
            assert len(batches) == 1
            assert batches[0].count == 2

    def test_history_onchain_mock_returns_empty(self) -> None:
        assert list_history_onchain(network="mock") == []

    def test_history_onchain_mainnet_rejected(self) -> None:
        with pytest.raises(NetworkNotSupportedError):
            list_history_onchain(network="optimism-mainnet")

    def test_history_onchain_graphql(self) -> None:
        good = MagicMock()
        good.json.return_value = {
            "data": {
                "attestations": [
                    {"id": "0x1", "attester": "0xA", "recipient": "0xB",
                     "schemaId": "0xS", "time": 100},
                ]
            }
        }
        good.raise_for_status = MagicMock()
        with patch("httpx.post", return_value=good):
            atts = list_history_onchain(
                attester="0xA", network="optimism-sepolia", limit=5
            )
            assert len(atts) == 1
            assert atts[0]["id"] == "0x1"


# ── DID 集成 ─────────────────────────────────────────────────────────────────


class TestResolveAttester:
    def test_resolve_explicit_config(self) -> None:
        cfg = AttestConfig(attester_did="did:sisoul:bob")
        assert resolve_attester_did(cfg) == "did:sisoul:bob"

    def test_resolve_from_did_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """vault_dir 下有 dids.json → resolve_attester_did 走 list_local_dids."""
        cfg = AttestConfig(attester_did=None)
        # 用 did 模块写一个真 registry
        from sisoul.identity.did import register_did
        register_did(
            "alice",
            network="mock",
            registry_path=tmp_path / "identity" / "dids.json",
        )
        did = resolve_attester_did(cfg, vault_dir=tmp_path)
        assert did == "did:sisoul:alice"

    def test_resolve_no_did_raises(self, tmp_path: Path) -> None:
        cfg = AttestConfig(attester_did=None)
        with pytest.raises(EASError, match="本地无 DID"):
            resolve_attester_did(cfg, vault_dir=tmp_path)


# ── schema 元数据 ────────────────────────────────────────────────────────────


def test_schema_uid_stable() -> None:
    """MOCK_SCHEMA_UID 必须确定性, 改 schema 字段会变."""
    assert MOCK_SCHEMA_UID.startswith("0x")
    assert len(MOCK_SCHEMA_UID) == 66
    # SISOUL_AUDIT_SCHEMA 包含 6 字段
    assert "actor_did" in SISOUL_AUDIT_SCHEMA
    assert "action_type" in SISOUL_AUDIT_SCHEMA
    assert "prompt_hash" in SISOUL_AUDIT_SCHEMA
    assert "bytes32" in SISOUL_AUDIT_SCHEMA
    assert "uint64" in SISOUL_AUDIT_SCHEMA
