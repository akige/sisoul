"""Case schema for v2.0 Case-Based Reasoning Graph."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Case:
    id: str
    question: str
    answer: str
    did_author: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    sources: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    eas_attestation_uid: Optional[str] = None
    embedding_vector_id: Optional[str] = None

    def validate(self) -> bool:
        if not self.id or not self.question or not self.answer or not self.did_author:
            return False
        if not self.did_author.startswith("did:key:"):
            return False
        return True


@dataclass
class CaseRetrieval:
    query: str
    cases: list[Case]
    top_k: int = 5
    similarity_threshold: float = 0.6

    def is_hit(self) -> bool:
        return len(self.cases) > 0


def derive_case_id(question: str, did_author: str) -> str:
    import hashlib
    h = hashlib.sha256(f"{did_author}|{question}".encode("utf-8")).hexdigest()
    return f"case-{h[:12]}"
