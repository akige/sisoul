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
    assert v2.__version__.endswith("-foundation")


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


# ──────────────────────────────────────────────────────────────────────────────
# CaseStore tests
# ──────────────────────────────────────────────────────────────────────────────


def test_case_store_add_and_get(tmp_path):
    from sisoul.v2.case_graph import Case, CaseStore
    store = CaseStore(tmp_path / "vault")
    case = Case(
        id="case-test-1",
        question="how to use tokio select",
        answer="use unwrap_or_else",
        did_author="did:key:z6MkAlice",
        tags=["rust", "async"],
    )
    path = store.add(case)
    assert path.exists()

    fetched = store.get("case-test-1")
    assert fetched is not None
    assert fetched.question == "how to use tokio select"
    assert fetched.did_author == "did:key:z6MkAlice"


def test_case_store_search_naive(tmp_path):
    from sisoul.v2.case_graph import Case, CaseStore
    store = CaseStore(tmp_path / "vault")
    store.add(Case(id="c1", question="rust async tokio", answer="solution 1", did_author="did:key:z6Mk1"))
    store.add(Case(id="c2", question="python asyncio", answer="solution 2", did_author="did:key:z6Mk2"))
    store.add(Case(id="c3", question="rust borrow checker", answer="solution 3", did_author="did:key:z6Mk3"))

    ret = store.search("rust", top_k=5)
    assert ret.is_hit()
    assert len(ret.cases) == 2
    assert all("rust" in c.question.lower() for c in ret.cases)


def test_case_store_list_all(tmp_path):
    from sisoul.v2.case_graph import Case, CaseStore
    store = CaseStore(tmp_path / "vault")
    for i in range(5):
        store.add(Case(id=f"c{i}", question=f"q{i}", answer=f"a{i}", did_author="did:key:z6MkX"))
    cases = store.list_all()
    assert len(cases) == 5


def test_case_store_index_persisted(tmp_path):
    from sisoul.v2.case_graph import Case, CaseStore
    import json
    store = CaseStore(tmp_path / "vault")
    store.add(Case(id="cX", question="qX", answer="aX", did_author="did:key:z6MkX", tags=["tag1"]))
    idx = json.loads(store.index_file.read_text())
    assert "cX" in idx
    assert idx["cX"]["tags"] == ["tag1"]


def test_case_store_invalid_rejects(tmp_path):
    from sisoul.v2.case_graph import Case, CaseStore
    store = CaseStore(tmp_path / "vault")
    bad = Case(id="", question="", answer="", did_author="")
    import pytest
    with pytest.raises(ValueError):
        store.add(bad)


# ──────────────────────────────────────────────────────────────────────────────
# PersonalLoRATrainer skeleton tests
# ──────────────────────────────────────────────────────────────────────────────


def test_lora_trainer_init_valid():
    from sisoul.v2.personal_lora import PersonalLoRATrainer, TrainingConfig
    cfg = TrainingConfig()
    t = PersonalLoRATrainer(cfg, did_owner="did:key:z6MkAlice")
    assert t.did_owner == "did:key:z6MkAlice"


def test_lora_trainer_init_invalid_did():
    from sisoul.v2.personal_lora import PersonalLoRATrainer, TrainingConfig
    import pytest
    with pytest.raises(ValueError):
        PersonalLoRATrainer(TrainingConfig(), did_owner="not-a-did")


def test_lora_trainer_train_stub(tmp_path):
    from sisoul.v2.personal_lora import PersonalLoRATrainer, TrainingConfig
    cfg = TrainingConfig(epochs=2, rank=8)
    t = PersonalLoRATrainer(cfg, did_owner="did:key:z6MkAlice")
    out = tmp_path / "lora.safetensors"
    result = t.train(out)
    assert result.adapter.rank == 8
    assert result.epoch_count == 2
    assert out.exists()


def test_lora_trainer_collect_dataset_empty(tmp_path):
    from sisoul.v2.personal_lora import PersonalLoRATrainer, TrainingConfig
    t = PersonalLoRATrainer(TrainingConfig(), did_owner="did:key:z6MkAlice")
    count = t.collect_dataset(tmp_path / "nonexistent")
    assert count == 0


# ──────────────────────────────────────────────────────────────────────────────
# ProvenanceAttester tests
# ──────────────────────────────────────────────────────────────────────────────


def test_attester_init_invalid_did():
    from sisoul.v2.provenance import ProvenanceAttester
    import pytest
    with pytest.raises(ValueError):
        ProvenanceAttester(did_attester="not-a-did")


def test_attester_attest_deterministic():
    from sisoul.v2.provenance import ProvenanceAttester, build_chain
    a = ProvenanceAttester(did_attester="did:key:z6MkAlice")
    chain1 = build_chain("r1", "q", "a", "did:key:z6MkAlice",
                         cited_cases=[("c1", "did:key:z6MkBob"), ("c2", "did:key:z6MkCarol")])
    uid1 = a.attest(chain1)
    chain2 = build_chain("r1", "q", "a", "did:key:z6MkAlice",
                         cited_cases=[("c1", "did:key:z6MkBob"), ("c2", "did:key:z6MkCarol")])
    uid2 = a.attest(chain2)
    assert uid1 == uid2
    assert uid1.startswith("0x")
    assert len(uid1) == 66  # 0x + 64 hex chars


def test_attester_estimate_micropay():
    from sisoul.v2.provenance import ProvenanceAttester, build_chain
    a = ProvenanceAttester(did_attester="did:key:z6MkAlice")
    chain = build_chain("r", "q", "a", "did:key:z6MkAlice",
                        cited_cases=[("c1", "did:key:z6MkBob")] * 3)
    pay = a.estimate_micropay(chain, rate_per_citation=0.05)
    import pytest
    assert pay == pytest.approx(0.15)


# ──────────────────────────────────────────────────────────────────────────────
# SkillInstaller tests
# ──────────────────────────────────────────────────────────────────────────────


def test_skill_installer_install_valid(tmp_path):
    from sisoul.v2.skill_marketplace import SkillInstaller, SkillManifest
    installer = SkillInstaller(tmp_path / "skills")
    m = SkillManifest(
        name="my-skill",
        version="0.1.0",
        entry="main.py",
        runtime="python",
        ipfs_cid="bafyabcdef",
        author_did="did:key:z6MkBob",
        sigstore_sig="sig123",
    )
    r = installer.install(m, skip_sigstore=True)
    assert r.success is True
    assert "my-skill" in r.install_path


def test_skill_installer_install_invalid_cid(tmp_path):
    from sisoul.v2.skill_marketplace import SkillInstaller, SkillManifest
    installer = SkillInstaller(tmp_path / "skills")
    m = SkillManifest(
        name="bad", version="0.1.0", entry="m.py", runtime="python",
        ipfs_cid="not-cid", author_did="did:key:z6Mk", sigstore_sig="",
    )
    r = installer.install(m)
    assert r.success is False
    assert "CID" in (r.error or "")


def test_skill_installer_list_and_uninstall(tmp_path):
    from sisoul.v2.skill_marketplace import SkillInstaller, SkillManifest
    installer = SkillInstaller(tmp_path / "skills")
    for i in range(3):
        installer.install(SkillManifest(
            name=f"skill-{i}", version="0.1", entry="m.py", runtime="python",
            ipfs_cid="bafyX", author_did="did:key:z6MkX", sigstore_sig="s",
        ), skip_sigstore=True)
    assert sorted(installer.list_installed()) == ["skill-0", "skill-1", "skill-2"]
    assert installer.uninstall("skill-1") is True
    assert "skill-1" not in installer.list_installed()
    assert installer.uninstall("nonexistent") is False


# ──────────────────────────────────────────────────────────────────────────────
# Federated LoRA tests (v3.0 §62 §2.2 作用 2)
# ──────────────────────────────────────────────────────────────────────────────


def test_federated_aggregator_init_valid():
    from sisoul.v2.personal_lora import FederatedLoRAAggregator, FederatedLoRAConfig
    agg = FederatedLoRAAggregator(FederatedLoRAConfig(), aggregator_did="did:key:z6MkA")
    assert agg.aggregator_did == "did:key:z6MkA"


def test_federated_aggregator_invalid_did():
    from sisoul.v2.personal_lora import FederatedLoRAAggregator, FederatedLoRAConfig
    import pytest
    with pytest.raises(ValueError):
        FederatedLoRAAggregator(FederatedLoRAConfig(), aggregator_did="not-a-did")


def test_federated_min_participants_enforced():
    from sisoul.v2.personal_lora import (
        FederatedLoRAAggregator, FederatedLoRAConfig, LoRAAdapter,
    )
    import pytest
    agg = FederatedLoRAAggregator(
        FederatedLoRAConfig(min_participants=3), aggregator_did="did:key:z6MkA"
    )
    base = LoRAAdapter(
        name="base", version="0.1", base_model="Llama-3.1-8B", rank=16,
        file_path="/tmp/base.safetensors", size_bytes=20_000_000,
        did_owner="did:key:z6MkA", trained_at="2026-06-04",
    )
    with pytest.raises(ValueError):
        agg.federate(base, peer_deltas=[{"did": "did:key:z6MkB"}], round_id=1)  # only 1


def test_federated_aggregation_returns_new_adapter():
    from sisoul.v2.personal_lora import (
        FederatedLoRAAggregator, FederatedLoRAConfig, LoRAAdapter,
    )
    agg = FederatedLoRAAggregator(
        FederatedLoRAConfig(min_participants=2), aggregator_did="did:key:z6MkA"
    )
    base = LoRAAdapter(
        name="base", version="0.1", base_model="Llama-3.1-8B", rank=16,
        file_path="/tmp/base.safetensors", size_bytes=20_000_000,
        did_owner="did:key:z6MkA", trained_at="2026-06-04",
    )
    deltas = [
        {"did": "did:key:z6MkB", "delta": "<stub>"},
        {"did": "did:key:z6MkC", "delta": "<stub>"},
    ]
    new_adapter, fed_round = agg.federate(base, deltas, round_id=1)
    assert new_adapter.name == "base-fed-r1"
    assert fed_round.delta_count == 2
    assert "did:key:z6MkB" in fed_round.participants


# ──────────────────────────────────────────────────────────────────────────────
# Multi-Agent Debate tests (v3.0 §62 §1)
# ──────────────────────────────────────────────────────────────────────────────


def test_debate_init_requires_2_agents():
    from sisoul.v2.debate import MultiAgentDebate, DebateAgent
    import pytest
    with pytest.raises(ValueError):
        MultiAgentDebate([DebateAgent(did="did:key:z6MkA")])


def test_debate_picks_highest_rep_synthesizer():
    from sisoul.v2.debate import MultiAgentDebate, DebateAgent
    agents = [
        DebateAgent(did="did:key:z6MkA", petname="Alice", topic_reputation=0.5),
        DebateAgent(did="did:key:z6MkB", petname="Bob", topic_reputation=0.9),
        DebateAgent(did="did:key:z6MkC", petname="Carol", topic_reputation=0.7),
    ]
    d = MultiAgentDebate(agents)
    syn = d.select_synthesizer()
    assert syn.did == "did:key:z6MkB"
    assert syn.topic_reputation == 0.9


def test_debate_runs_3_rounds_default():
    from sisoul.v2.debate import MultiAgentDebate, DebateAgent
    agents = [
        DebateAgent(did="did:key:z6MkA", topic_reputation=0.5),
        DebateAgent(did="did:key:z6MkB", topic_reputation=0.7),
    ]
    d = MultiAgentDebate(agents)
    result = d.debate("How to fix Rust async deadlock?")
    # 3 rounds × 2 agents = 6 rounds in total
    assert len(result.rounds) == 6
    assert result.query == "How to fix Rust async deadlock?"


def test_debate_result_has_synthesized_answer():
    from sisoul.v2.debate import MultiAgentDebate, DebateAgent
    agents = [
        DebateAgent(did="did:key:z6MkA", topic_reputation=0.6),
        DebateAgent(did="did:key:z6MkB", topic_reputation=0.8),
    ]
    d = MultiAgentDebate(agents)
    result = d.debate("Q")
    assert "stub synthesized" in result.final_answer
    assert result.final_confidence == 0.8  # synthesizer = B (rep 0.8)


# ──────────────────────────────────────────────────────────────────────────────
# Reputation Routing tests (v3.0 §4.3)
# ──────────────────────────────────────────────────────────────────────────────


def test_reputation_router_init():
    from sisoul.v2.reputation import ReputationRouter
    r = ReputationRouter()
    assert r is not None


def test_reputation_update_and_score():
    from sisoul.v2.reputation import ReputationRouter
    r = ReputationRouter()
    r.update("did:key:z6MkA", "rust", +0.3)
    r.update("did:key:z6MkA", "rust", +0.2)
    assert r.get_score("did:key:z6MkA", "rust") == 1.0  # 0.5 + 0.3 + 0.2 = clamped to 1.0


def test_reputation_invalid_did():
    from sisoul.v2.reputation import ReputationRouter
    import pytest
    r = ReputationRouter()
    with pytest.raises(ValueError):
        r.update("not-a-did", "rust", 0.1)


def test_reputation_select_top_k():
    from sisoul.v2.reputation import ReputationRouter, RoutingRequest
    r = ReputationRouter()
    r.update("did:key:z6MkA", "rust", +0.4)  # 0.9
    r.update("did:key:z6MkB", "rust", -0.2)  # 0.3
    r.update("did:key:z6MkC", "rust", +0.2)  # 0.7
    req = RoutingRequest(query="how", topic="rust", top_k=2)
    picked = r.select_top_k(req, ["did:key:z6MkA", "did:key:z6MkB", "did:key:z6MkC", "did:key:z6MkD"])
    assert picked[0] == "did:key:z6MkA"  # 0.9 highest
    assert "did:key:z6MkB" not in picked  # min_rep 0.3 borderline


def test_reputation_default_score():
    from sisoul.v2.reputation import ReputationRouter
    r = ReputationRouter()
    # no history → default 0.5
    assert r.get_score("did:key:z6MkX", "ml") == 0.5


# ──────────────────────────────────────────────────────────────────────────────
# Memory Compaction tests (v2.0 §4.8)
# ──────────────────────────────────────────────────────────────────────────────


def test_memory_compactor_init():
    from sisoul.v2.memory_compaction import MemoryCompactor, CompactionConfig
    mc = MemoryCompactor(CompactionConfig(), did_owner="did:key:z6MkA")
    assert mc.did_owner == "did:key:z6MkA"


def test_memory_compactor_invalid_did():
    from sisoul.v2.memory_compaction import MemoryCompactor, CompactionConfig
    import pytest
    with pytest.raises(ValueError):
        MemoryCompactor(CompactionConfig(), did_owner="not-did")


def test_memory_compactor_distill():
    from sisoul.v2.memory_compaction import MemoryCompactor, CompactionConfig
    mc = MemoryCompactor(CompactionConfig(), did_owner="did:key:z6MkA")
    lesson = mc.distill(["case1", "case2", "case3"], topic="rust")
    assert lesson.validate() is True
    assert len(lesson.source_case_ids) == 3
    assert lesson.id.startswith("lesson-")


def test_memory_compactor_distill_min_cases():
    from sisoul.v2.memory_compaction import MemoryCompactor, CompactionConfig
    import pytest
    mc = MemoryCompactor(CompactionConfig(), did_owner="did:key:z6MkA")
    with pytest.raises(ValueError):
        mc.distill(["only-one"])


def test_memory_compactor_should_compact():
    from sisoul.v2.memory_compaction import MemoryCompactor, CompactionConfig
    mc = MemoryCompactor(
        CompactionConfig(cases_per_lesson=10, arweave_threshold_size_bytes=1024),
        did_owner="did:key:z6MkA",
    )
    assert mc.should_compact(case_count=15, case_total_bytes=2048) is True
    assert mc.should_compact(case_count=5, case_total_bytes=2048) is False
    assert mc.should_compact(case_count=15, case_total_bytes=500) is False


# ──────────────────────────────────────────────────────────────────────────────
# Growth Logger tests (v2.0 §4.6)
# ──────────────────────────────────────────────────────────────────────────────


def test_growth_logger_write_and_read(tmp_path):
    from sisoul.v2.growth import GrowthLogger, DailyGrowthSnapshot
    gl = GrowthLogger(tmp_path / "vault")
    snap = DailyGrowthSnapshot(date="2026-06-04", cases_added=5, skills_used=3)
    p = gl.write(snap)
    assert p.exists()
    read = gl.read("2026-06-04")
    assert read.cases_added == 5
    assert read.skills_used == 3


def test_growth_logger_last_n_days(tmp_path):
    from sisoul.v2.growth import GrowthLogger, DailyGrowthSnapshot
    gl = GrowthLogger(tmp_path / "vault")
    for i, day in enumerate(["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]):
        gl.write(DailyGrowthSnapshot(date=day, cases_added=i + 1, chats_sent=i * 2))
    trend = gl.last_n_days(3)
    assert len(trend.snapshots) == 3
    assert trend.total_cases() == 2 + 3 + 4  # last 3
    assert trend.avg_chats_per_day() == (2 + 4 + 6) / 3


def test_growth_logger_empty(tmp_path):
    from sisoul.v2.growth import GrowthLogger
    gl = GrowthLogger(tmp_path / "vault")
    trend = gl.last_n_days(7)
    assert trend.total_cases() == 0
    assert trend.avg_chats_per_day() == 0.0
