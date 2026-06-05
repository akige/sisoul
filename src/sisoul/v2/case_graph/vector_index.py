"""Vector embedding index for case retrieval.

Two implementations:
- TfIdfIndex: pure-Python TF-IDF (zero deps, deterministic, CI-safe).
- ChromaIndex: ChromaDB + sentence-transformers (real semantic search).

Factory `make_index(prefer="auto"|"tfidf"|"chroma")` picks based on env + dep availability.
"""
from __future__ import annotations
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Optional, Protocol

from .schema import Case


class CaseIndex(Protocol):
    def add(self, case: Case) -> None: ...
    def remove(self, case_id: str) -> None: ...
    def search(self, query: str, top_k: int = 5, threshold: float = 0.0) -> list[tuple[str, float]]: ...
    def size(self) -> int: ...


class TfIdfIndex:
    """TF-IDF foundation. Zero deps, deterministic."""

    _word_re = re.compile(r"\b[a-zA-Z_][a-zA-Z_0-9]+\b", re.U)

    def __init__(self):
        self._docs: dict[str, dict[str, int]] = {}
        self._df: Counter = Counter()
        self._N: int = 0

    @classmethod
    def _tokenize(cls, text: str) -> list[str]:
        return [m.group(0).lower() for m in cls._word_re.finditer(text or "")]

    def add(self, case: Case) -> None:
        tokens = self._tokenize(case.question + " " + case.answer + " " + " ".join(case.tags))
        if not tokens:
            return
        tf = Counter(tokens)
        self._docs[case.id] = dict(tf)
        for term in tf.keys():
            self._df[term] += 1
        self._N += 1

    def remove(self, case_id: str) -> None:
        if case_id not in self._docs:
            return
        for term in self._docs[case_id]:
            self._df[term] -= 1
            if self._df[term] <= 0:
                del self._df[term]
        del self._docs[case_id]
        self._N -= 1

    def _idf(self, term: str) -> float:
        if not self._N or self._df.get(term, 0) == 0:
            return 0.0
        return math.log((self._N + 1) / (self._df[term] + 1)) + 1.0

    def _vec(self, tokens: list[str]) -> dict[str, float]:
        tf = Counter(tokens)
        return {t: tf[t] * self._idf(t) for t in tf}

    def _cosine(self, va: dict[str, float], vb: dict[str, float]) -> float:
        common = set(va) & set(vb)
        if not common:
            return 0.0
        dot = sum(va[t] * vb[t] for t in common)
        na = math.sqrt(sum(v * v for v in va.values()))
        nb = math.sqrt(sum(v * v for v in vb.values()))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    def search(self, query: str, top_k: int = 5, threshold: float = 0.0) -> list[tuple[str, float]]:
        q_tokens = self._tokenize(query)
        if not q_tokens:
            return []
        q_vec = self._vec(q_tokens)
        scored = []
        for cid, tf in self._docs.items():
            d_vec = {t: tf[t] * self._idf(t) for t in tf}
            score = self._cosine(q_vec, d_vec)
            if score > threshold:
                scored.append((cid, score))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]

    def size(self) -> int:
        return self._N


class ChromaIndex:
    """ChromaDB + sentence-transformers semantic index.

    Lazy-imports chromadb + sentence_transformers — they may not be installed
    in CI / minimal envs (chromadb is ~200MB with all-MiniLM-L6-v2 model).
    """

    DEFAULT_MODEL = "all-MiniLM-L6-v2"  # 80MB, 384-dim, MTEB ok-tier
    COLLECTION_NAME = "sisoul_cases_v1"

    def __init__(
        self,
        persist_dir: Optional[Path] = None,
        model_name: str = DEFAULT_MODEL,
    ):
        import chromadb
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

        self.persist_dir = persist_dir or Path.home() / ".sisoul" / "chroma"
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self._client = chromadb.PersistentClient(path=str(self.persist_dir))
        self._embed_fn = SentenceTransformerEmbeddingFunction(model_name=model_name)
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            embedding_function=self._embed_fn,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _doc_text(case: Case) -> str:
        return (case.question or "") + "\n" + (case.answer or "") + "\n" + " ".join(case.tags or [])

    def add(self, case: Case) -> None:
        text = self._doc_text(case)
        if not text.strip():
            return
        self._collection.upsert(
            ids=[case.id],
            documents=[text],
            metadatas=[{"tags": ",".join(case.tags or [])}],
        )

    def remove(self, case_id: str) -> None:
        try:
            self._collection.delete(ids=[case_id])
        except Exception:
            pass

    def search(self, query: str, top_k: int = 5, threshold: float = 0.0) -> list[tuple[str, float]]:
        if not query.strip():
            return []
        n = self._collection.count()
        if n == 0:
            return []
        result = self._collection.query(
            query_texts=[query],
            n_results=min(top_k, n),
        )
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        # ChromaDB cosine distance → similarity
        out = []
        for cid, dist in zip(ids, distances):
            sim = max(0.0, 1.0 - float(dist))
            if sim > threshold:
                out.append((cid, sim))
        return out

    def size(self) -> int:
        return self._collection.count()


def make_index(prefer: str = "auto") -> CaseIndex:
    """Pick the best available index.

    prefer:
    - "tfidf": always TfIdfIndex (CI / deterministic).
    - "chroma": ChromaIndex; raises ImportError if chromadb not installed.
    - "auto" (default): respect SISOUL_VECTOR_BACKEND env; else try chroma, fallback tfidf.
    """
    env = os.environ.get("SISOUL_VECTOR_BACKEND", "").lower()
    if prefer == "tfidf" or env == "tfidf":
        return TfIdfIndex()
    if prefer == "chroma" or env == "chroma":
        return ChromaIndex()
    # auto
    try:
        return ChromaIndex()
    except (ImportError, ModuleNotFoundError):
        return TfIdfIndex()


__all__ = ["TfIdfIndex", "ChromaIndex", "CaseIndex", "make_index"]
