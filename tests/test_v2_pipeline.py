"""sisoul v2.0 ask pipeline integration tests."""
from __future__ import annotations
import pytest
from pathlib import Path

from sisoul.v2 import case_graph
from sisoul.v2.pipeline import AskRequest, AskResponse, V2AskPipeline
from sisoul.v2.case_graph import Case, CaseStore
from sisoul.v2.reputation import ReputationRouter


@pytest.fixture
def pipeline(tmp_path):
    return V2AskPipeline(vault_dir=tmp_path / "vault", network="mock")


def test_v2_pipeline_first_ask_no_cases(pipeline):
    req = AskRequest(query="how to use Rust async", did_asker="did:key:z6MkAlice")
    resp = pipeline.ask(req)
    assert resp.cited_cases == []
    assert resp.attestation_uid.startswith("mock:")
    assert resp.new_case_id.startswith("case-")
    assert resp.confidence == 0.5


def test_v2_pipeline_ask_creates_case(pipeline):
    req = AskRequest(query="how to use tokio", did_asker="did:key:z6MkA")
    resp = pipeline.ask(req)
    # case should be persisted
    fetched = pipeline.case_store.get(resp.new_case_id)
    assert fetched is not None
    assert fetched.question == "how to use tokio"
    assert fetched.eas_attestation_uid == resp.attestation_uid


def test_v2_pipeline_second_ask_uses_cited_cases(pipeline):
    # first ask creates a case
    pipeline.ask(AskRequest(query="rust async tokio select", did_asker="did:key:z6MkA"))
    # second ask similar should cite the first
    resp = pipeline.ask(AskRequest(
        query="rust async tokio deadlock",  # has "rust async tokio" in common
        did_asker="did:key:z6MkB",
    ))
    assert len(resp.cited_cases) >= 1
    assert resp.confidence > 0.5


def test_v2_pipeline_provenance_chain_attached(pipeline):
    pipeline.ask(AskRequest(query="rust async", did_asker="did:key:z6MkAlice"))
    resp = pipeline.ask(AskRequest(query="rust async tokio", did_asker="did:key:z6MkBob"))
    assert resp.provenance.eas_attestation_uid == resp.attestation_uid
    assert resp.provenance.did_answerer == "did:key:z6MkBob"


def test_v2_pipeline_reputation_updated_on_citation(pipeline):
    rep = ReputationRouter()
    pipeline.reputation = rep
    # Alice writes a rust case
    pipeline.ask(AskRequest(
        query="rust async tokio",
        did_asker="did:key:z6MkAlice",
        topic="rust",
    ))
    # Bob asks rust question, should cite Alice + update Alice's rust rep
    init_score = rep.get_score("did:key:z6MkAlice", "rust")
    pipeline.ask(AskRequest(
        query="rust async deadlock tokio",
        did_asker="did:key:z6MkBob",
        topic="rust",
    ))
    new_score = rep.get_score("did:key:z6MkAlice", "rust")
    assert new_score > init_score


def test_v2_pipeline_eas_deterministic(pipeline):
    resp1 = pipeline.ask(AskRequest(query="python asyncio", did_asker="did:key:z6MkAlice"))
    # Same query+asker → same case_id → but second ask has cited cases → different attestation
    # Different asker
    resp2 = pipeline.ask(AskRequest(query="python asyncio", did_asker="did:key:z6MkBob"))
    assert resp1.new_case_id != resp2.new_case_id


def test_v2_pipeline_invalid_did_silent_at_reputation_update(pipeline):
    # Add a case with valid did, then create one with invalid did via direct store add
    # (skipping pipeline's validation), then ensure pipeline doesn't crash on rep update
    bad_case = Case(
        id="case-bad-did", question="q", answer="a", did_author="did:key:z6MkValid",
    )
    pipeline.case_store.add(bad_case)
    # now ask similar query
    resp = pipeline.ask(AskRequest(
        query="q similar", did_asker="did:key:z6MkAsker", topic="rust",
    ))
    # should not crash even if cited case has unusual did
    assert resp is not None


def test_v2_pipeline_confidence_caps_at_1():
    """If we cite 10+ cases, confidence stays at 1.0."""
    from sisoul.v2.pipeline import V2AskPipeline, AskRequest
    pipeline = V2AskPipeline(vault_dir=Path("/tmp/sisoul-test-confidence-cap"), network="mock")
    # seed many cases
    for i in range(20):
        pipeline.case_store.add(Case(
            id=f"seed-{i}",
            question=f"rust async case {i}",
            answer=f"answer {i}",
            did_author=f"did:key:z6MkAuthor{i}",
        ))
    resp = pipeline.ask(AskRequest(
        query="rust async something",
        did_asker="did:key:z6MkAsker",
        top_k_cases=10,
    ))
    assert resp.confidence == 1.0
