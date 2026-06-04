"""sisoul v2.0 foundation tests (case_graph / personal_lora / provenance / skill_marketplace).

骨架级测试: 验证 4 模块 schema 可 import + dataclass 字段正确.
完整 impl 在 v2.0 ship (T+8-12m).
"""

from __future__ import annotations

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# Module imports
# ──────────────────────────────────────────────────────────────────────────────


def test_v2_imports():
    """4 个 v2.0 模块可 import."""
    from sisoul.v2 import case_graph, personal_lora, provenance, skill_marketplace
    assert all([case_graph, personal_lora, provenance, skill_marketplace])


def test_v2_version():
    """v2 foundation version 0.1.0."""
    from sisoul import v2
    assert v2.__version__ == "0.1.0-foundation"


# ──────────────────────────────────────────────────────────────────────────────
# Case schema
# ──────────────────────────────────────────────────────────────────────────────


def test_case_schema_valid():
    from sisoul.v2.case_graph import Case
    case = Case(
        id="case-abc123",
        question="Rust async tokio::select! 死锁怎么修",
        answer="用 unwrap_or_else 替 .unwrap() ...",
        did_author="did:key:z6MkAlice",
        tags=["rust", "async"],
    )
    assert case.validate() is True
    assert case.eas_attestation_uid is None


def test_case_schema_invalid_did():
    from sisoul.v2.case_graph import Case
    case = Case(
        id="case-abc",
        question="Q",
        answer="A",
        did_author="not-a-did",
    )
    assert case.validate() is False


def test_case_id_deterministic():
    from sisoul.v2.case_graph.schema import derive_case_id
    cid1 = derive_case_id("question 1", "did:key:z6MkAlice")
    cid2 = derive_case_id("question 1", "did:key:z6MkAlice")
    cid3 = derive_case_id("question 2", "did:key:z6MkAlice")
    assert cid1 == cid2
    assert cid1 != cid3
    assert cid1.startswith("case-")


def test_case_retrieval_hit():
    from sisoul.v2.case_graph import Case, CaseRetrieval
    cases = [Case(id="c1", question="q1", answer="a1", did_author="did:key:z6Mk1")]
    ret = CaseRetrieval(query="q", cases=cases)
    assert ret.is_hit() is True


def test_case_retrieval_miss():
    from sisoul.v2.case_graph import CaseRetrieval
    ret = CaseRetrieval(query="q", cases=[])
    assert ret.is_hit() is False


# ──────────────────────────────────────────────────────────────────────────────
# Personal LoRA schema
# ──────────────────────────────────────────────────────────────────────────────


def test_lora_training_config_default():
    from sisoul.v2.personal_lora import TrainingConfig
    cfg = TrainingConfig()
    assert cfg.rank == 16
    assert cfg.alpha == 32
    assert cfg.base_model == "meta-llama/Llama-3.1-8B"
    assert cfg.min_conversations == 1000


def test_lora_adapter_schema():
    from sisoul.v2.personal_lora import LoRAAdapter
    adapter = LoRAAdapter(
        name="alice-personal-v1",
        version="0.1.0",
        base_model="meta-llama/Llama-3.1-8B",
        rank=16,
        file_path="~/.sisoul/lora/personal-v3.safetensors",
        size_bytes=20_000_000,
        did_owner="did:key:z6MkAlice",
        trained_at="2026-06-04T00:00:00Z",
    )
    assert adapter.name == "alice-personal-v1"
    assert adapter.size_bytes == 20_000_000


# ──────────────────────────────────────────────────────────────────────────────
# Provenance schema
# ──────────────────────────────────────────────────────────────────────────────


def test_citation_schema():
    from sisoul.v2.provenance import Citation
    c = Citation(
        source_type="case",
        source_id="case-7c",
        did_author="did:key:z6MkBob",
        confidence=0.9,
        snippet="Bob 2026-03 解过相同问题",
        micropayment_sis=0.1,
    )
    assert c.source_type == "case"
    assert c.micropayment_sis == 0.1


def test_provenance_chain_total_micropayment():
    from sisoul.v2.provenance import Citation, ProvenanceChain
    chain = ProvenanceChain(
        response_id="resp-1",
        query="Q",
        answer="A",
        did_answerer="did:key:z6MkAlice",
        citations=[
            Citation(source_type="case", source_id="c1", did_author="did:key:z6MkBob", micropayment_sis=0.1),
            Citation(source_type="case", source_id="c2", did_author="did:key:z6MkCharlie", micropayment_sis=0.2),
        ],
    )
    assert chain.total_micropayment() == pytest.approx(0.3)


# ──────────────────────────────────────────────────────────────────────────────
# Skill Marketplace schema
# ──────────────────────────────────────────────────────────────────────────────


def test_skill_manifest_schema():
    from sisoul.v2.skill_marketplace import SkillManifest
    m = SkillManifest(
        name="rust-async-expert",
        version="0.1.0",
        entry="main.py",
        runtime="python",
        ipfs_cid="bafyreigh2akiscaildcqabsyg3dfr6chu3fgpregiymsck7e7aqa4s52zi",
        author_did="did:key:z6MkBob",
        sigstore_sig="<base64-sig>",
        description="Rust async expert skill",
        sis_price_per_call=0.01,
    )
    assert m.runtime == "python"
    assert m.sis_price_per_call == 0.01


def test_skill_install_result_schema():
    from sisoul.v2.skill_marketplace import SkillInstallResult
    r = SkillInstallResult(
        skill_name="rust-async-expert",
        success=True,
        install_path="~/.sisoul/skills/rust-async-expert/",
        sigstore_verified=True,
        hot_loaded=True,
    )
    assert r.success is True
    assert r.sigstore_verified is True
