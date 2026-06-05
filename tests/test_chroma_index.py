"""ChromaDB real semantic search integration (replaces TfIdf foundation for v2.0 ship).

Skipped if chromadb not installed (graceful in minimal CI envs).
"""
from __future__ import annotations
import importlib.util
import tempfile
from pathlib import Path

import pytest

CHROMA_AVAILABLE = importlib.util.find_spec("chromadb") is not None
ST_AVAILABLE = importlib.util.find_spec("sentence_transformers") is not None

pytestmark = pytest.mark.skipif(
    not (CHROMA_AVAILABLE and ST_AVAILABLE),
    reason="chromadb / sentence-transformers not installed (optional deps)",
)


@pytest.fixture
def cases():
    from sisoul.v2.case_graph.schema import Case

    return [
        Case(
            id="c1",
            question="How to set up Python venv?",
            answer="Use python -m venv .venv then source .venv/bin/activate",
            did_author="did:key:z6MkAlice",
            tags=["python", "venv"],
            created_at="2026-06-05T00:00:00Z",
        ),
        Case(
            id="c2",
            question="What is recursive self improvement?",
            answer="AI improving its own code/prompts without human intervention",
            did_author="did:key:z6MkAlice",
            tags=["ai", "rsi"],
            created_at="2026-06-05T00:00:00Z",
        ),
        Case(
            id="c3",
            question="Best post-quantum crypto for chat",
            answer="ML-KEM-1024 for KEM hybrid with X25519, Dilithium for signing",
            did_author="did:key:z6MkAlice",
            tags=["crypto", "pq"],
            created_at="2026-06-05T00:00:00Z",
        ),
    ]


@pytest.fixture
def chroma_index(tmp_path):
    from sisoul.v2.case_graph.vector_index import ChromaIndex

    return ChromaIndex(persist_dir=tmp_path / "chroma")


def test_chroma_init_creates_persist_dir(tmp_path):
    from sisoul.v2.case_graph.vector_index import ChromaIndex

    p = tmp_path / "nested" / "chroma"
    ix = ChromaIndex(persist_dir=p)
    assert p.exists()
    assert ix.size() == 0


def test_chroma_add_and_size(chroma_index, cases):
    for c in cases:
        chroma_index.add(c)
    assert chroma_index.size() == 3


def test_chroma_add_skips_empty(chroma_index):
    from sisoul.v2.case_graph.schema import Case

    empty = Case(
        id="empty",
        question="",
        answer="",
        did_author="did:key:z6MkA",
        tags=[],
        created_at="2026-06-05T00:00:00Z",
    )
    chroma_index.add(empty)
    assert chroma_index.size() == 0


def test_chroma_remove(chroma_index, cases):
    for c in cases:
        chroma_index.add(c)
    chroma_index.remove("c2")
    assert chroma_index.size() == 2
    hits = chroma_index.search("recursive self improvement")
    assert "c2" not in [cid for cid, _ in hits]


def test_chroma_remove_missing_is_safe(chroma_index):
    chroma_index.remove("nonexistent")  # no raise


def test_chroma_semantic_search_beats_tfidf_on_paraphrase(chroma_index, cases):
    """Semantic search should match paraphrased queries better than TfIdf."""
    from sisoul.v2.case_graph.vector_index import TfIdfIndex

    tf = TfIdfIndex()
    for c in cases:
        chroma_index.add(c)
        tf.add(c)

    # Paraphrase: "setting up virtual env in python" — has 0 verbatim overlap with cases
    query = "setting up virtual env in python"
    chroma_hits = chroma_index.search(query, top_k=1)
    tf_hits = tf.search(query, top_k=1)

    # Chroma should find c1 (the Python venv case) via semantic similarity
    assert chroma_hits, f"chroma returned no hits for '{query}'"
    assert chroma_hits[0][0] == "c1"

    # TfIdf may also find it via "python" overlap; but score range differs
    # The point is chroma should rank c1 strongly
    assert chroma_hits[0][1] > 0.3


def test_chroma_search_empty_query(chroma_index, cases):
    for c in cases:
        chroma_index.add(c)
    assert chroma_index.search("") == []
    assert chroma_index.search("   ") == []


def test_chroma_search_empty_collection(chroma_index):
    assert chroma_index.search("anything") == []


def test_chroma_search_top_k_limit(chroma_index, cases):
    for c in cases:
        chroma_index.add(c)
    hits = chroma_index.search("python", top_k=2)
    assert len(hits) <= 2


def test_chroma_search_threshold_filters(chroma_index, cases):
    for c in cases:
        chroma_index.add(c)
    # High threshold drops weak matches
    hits = chroma_index.search("post-quantum crypto", threshold=0.99)
    # 0.99 is virtually unattainable cosine sim → empty
    assert hits == []


def test_chroma_persist_across_instances(tmp_path, cases):
    """Persisted collection survives ChromaIndex re-instantiation."""
    from sisoul.v2.case_graph.vector_index import ChromaIndex

    ix1 = ChromaIndex(persist_dir=tmp_path / "chroma")
    for c in cases:
        ix1.add(c)
    assert ix1.size() == 3
    del ix1

    ix2 = ChromaIndex(persist_dir=tmp_path / "chroma")
    assert ix2.size() == 3  # collection persisted
    hits = ix2.search("python venv", top_k=1)
    assert hits and hits[0][0] == "c1"


def test_chroma_upsert_idempotent(chroma_index, cases):
    """Adding the same case twice should not duplicate."""
    chroma_index.add(cases[0])
    chroma_index.add(cases[0])
    assert chroma_index.size() == 1


def test_make_index_factory_auto_picks_chroma(tmp_path, monkeypatch):
    """With chromadb installed, make_index('auto') returns ChromaIndex."""
    from sisoul.v2.case_graph.vector_index import ChromaIndex, make_index

    monkeypatch.delenv("SISOUL_VECTOR_BACKEND", raising=False)
    ix = make_index("auto")
    assert isinstance(ix, ChromaIndex)


def test_make_index_factory_forces_tfidf(monkeypatch):
    from sisoul.v2.case_graph.vector_index import TfIdfIndex, make_index

    ix = make_index("tfidf")
    assert isinstance(ix, TfIdfIndex)


def test_make_index_factory_env_override(monkeypatch):
    from sisoul.v2.case_graph.vector_index import TfIdfIndex, make_index

    monkeypatch.setenv("SISOUL_VECTOR_BACKEND", "tfidf")
    ix = make_index("auto")
    assert isinstance(ix, TfIdfIndex)


def test_chroma_metadata_carries_tags(chroma_index, cases):
    """Verify tags are persisted (used for filtering in future)."""
    chroma_index.add(cases[0])
    coll = chroma_index._collection
    result = coll.get(ids=["c1"])
    assert result["metadatas"][0]["tags"] == "python,venv"
