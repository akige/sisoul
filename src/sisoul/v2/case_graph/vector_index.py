"""Vector embedding index for case retrieval.

Foundation: pure Python TF-IDF fallback (zero deps).
Future (v2.0 ship): swap to ChromaDB / FAISS / sentence-transformers.
"""
from __future__ import annotations
import math
import re
from collections import Counter
from typing import Optional

from .schema import Case


class TfIdfIndex:
    """Tiny TF-IDF index. Foundation impl, full impl swaps to embedding model.

    Why TF-IDF foundation: zero deps, works in CI, deterministic.
    """

    _word_re = re.compile(r"\b[a-zA-Z_][a-zA-Z_0-9]+\b", re.U)

    def __init__(self):
        self._docs: dict[str, dict[str, int]] = {}  # case_id → {term: tf}
        self._df: Counter = Counter()  # term → doc count
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


__all__ = ["TfIdfIndex"]
