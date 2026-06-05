"""Founder vault — persona + cases + lessons + eval_prompts loader.

Vault layout (under SISOUL_VAULT/founder/):
    system_prompt.md   — persona seed (RSI mutates a live copy here)
    cases/*.json       — design decisions, sprint history, design rationale
    lessons/*.json     — distilled principles
    eval_prompts.json  — RSI evaluator's prompt set
    rsi/history.jsonl  — every RSI iteration recorded
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


@dataclass(frozen=True)
class CaseEntry:
    id: str
    question: str
    answer: str
    did_author: str
    tags: list[str]
    created_at: str
    source: Optional[str] = None


@dataclass(frozen=True)
class LessonEntry:
    id: str
    principle: str
    context: str
    applies_to: list[str]
    established_at: str
    source: Optional[str] = None


def vault_root() -> Path:
    return Path(os.environ.get("SISOUL_VAULT", "~/.sisoul")).expanduser()


def founder_dir() -> Path:
    return vault_root() / "founder"


class FounderVault:
    """In-memory founder vault — loads system_prompt + cases + lessons."""

    def __init__(self, root: Optional[Path] = None):
        self.root = root or founder_dir()
        self._system_prompt: Optional[str] = None
        self._cases: dict[str, CaseEntry] = {}
        self._lessons: dict[str, LessonEntry] = {}
        self._eval_prompts: list[dict] = []
        if self.root.exists():
            self.reload()

    def reload(self) -> None:
        sp = self.root / "system_prompt.md"
        if sp.exists():
            self._system_prompt = sp.read_text()
        self._cases = {}
        for path in (self.root / "cases").glob("*.json") if (self.root / "cases").exists() else []:
            try:
                obj = json.loads(path.read_text())
                entry = CaseEntry(
                    id=obj["id"],
                    question=obj["question"],
                    answer=obj["answer"],
                    did_author=obj.get("did_author", ""),
                    tags=obj.get("tags", []),
                    created_at=obj.get("created_at", ""),
                    source=obj.get("source"),
                )
                self._cases[entry.id] = entry
            except Exception:
                continue
        self._lessons = {}
        for path in (self.root / "lessons").glob("*.json") if (self.root / "lessons").exists() else []:
            try:
                obj = json.loads(path.read_text())
                entry = LessonEntry(
                    id=obj["id"],
                    principle=obj["principle"],
                    context=obj.get("context", ""),
                    applies_to=obj.get("applies_to", []),
                    established_at=obj.get("established_at", ""),
                    source=obj.get("source"),
                )
                self._lessons[entry.id] = entry
            except Exception:
                continue
        ev = self.root / "eval_prompts.json"
        if ev.exists():
            try:
                self._eval_prompts = json.loads(ev.read_text()).get("prompts", [])
            except Exception:
                self._eval_prompts = []

    # ── accessors ─────────────────────────────────────────────────────────────

    @property
    def system_prompt(self) -> str:
        return self._system_prompt or ""

    def case(self, case_id: str) -> Optional[CaseEntry]:
        return self._cases.get(case_id)

    def lesson(self, lesson_id: str) -> Optional[LessonEntry]:
        return self._lessons.get(lesson_id)

    def all_cases(self) -> list[CaseEntry]:
        return list(self._cases.values())

    def all_lessons(self) -> list[LessonEntry]:
        return list(self._lessons.values())

    def eval_prompts(self) -> list[dict]:
        return list(self._eval_prompts)

    def size(self) -> dict:
        return {
            "cases": len(self._cases),
            "lessons": len(self._lessons),
            "eval_prompts": len(self._eval_prompts),
            "has_system_prompt": bool(self._system_prompt),
        }

    # ── retrieval (TF-IDF style, no chromadb dep for now) ────────────────────

    def recall(self, query: str, top_k: int = 3) -> list[tuple[CaseEntry, float]]:
        """Naive token-overlap recall. Replace with chromadb in v2."""
        q_terms = set(_tokenize(query))
        if not q_terms:
            return []
        scored: list[tuple[CaseEntry, float]] = []
        for entry in self._cases.values():
            doc_terms = set(
                _tokenize(entry.question + " " + entry.answer + " " + " ".join(entry.tags))
            )
            if not doc_terms:
                continue
            overlap = len(q_terms & doc_terms)
            score = overlap / max(1, len(q_terms))
            if score > 0:
                scored.append((entry, score))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]


def _tokenize(text: str) -> list[str]:
    """Hybrid tokenizer: ASCII word tokens + CJK char-bigrams.

    English words use \\b-bounded word tokens. CJK uses char-bigrams so that
    short Chinese queries ("不发币" → ["不发","发币"]) overlap with Chinese
    cases without requiring a heavy segmenter dependency like jieba.
    """
    import re

    if not text:
        return []
    tokens = [m.group(0).lower() for m in re.finditer(r"\b[a-zA-Z_][a-zA-Z_0-9]+\b", text)]
    for run in re.findall(r"[一-鿿㐀-䶿]+", text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


__all__ = ["FounderVault", "CaseEntry", "LessonEntry", "vault_root", "founder_dir"]
