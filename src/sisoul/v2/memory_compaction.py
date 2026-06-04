"""Memory Compaction (§59 §4.8, v2.0 ship T+11m).

LLM 自反思 + lesson 提取 + Arweave archive (> 30d cases).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional


@dataclass
class Lesson:
    """A compacted lesson (distilled from 10-100 cases)."""

    id: str
    title: str
    body: str
    source_case_ids: list[str] = field(default_factory=list)
    did_author: str = ""
    distilled_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    arweave_tx_id: Optional[str] = None  # set after archive
    embedding_vector_id: Optional[str] = None

    def validate(self) -> bool:
        return bool(self.id and self.title and self.body and self.source_case_ids)


@dataclass
class CompactionConfig:
    cases_per_lesson: int = 10  # 10-100 cases distill to 1 lesson
    min_age_days: int = 30  # only compact cases older than 30 days
    arweave_threshold_size_bytes: int = 1024 * 1024  # 1MB+ trigger archive
    batch_size: int = 50  # max cases per compaction run


class MemoryCompactor:
    """Compact old cases into lessons. Stub for v2.0 ship."""

    def __init__(self, config: CompactionConfig, did_owner: str):
        if not did_owner.startswith("did:key:"):
            raise ValueError(f"invalid did_owner: {did_owner}")
        self.config = config
        self.did_owner = did_owner

    def should_compact(self, case_count: int, case_total_bytes: int) -> bool:
        """Decide if compaction worth running."""
        return (
            case_count >= self.config.cases_per_lesson and
            case_total_bytes >= self.config.arweave_threshold_size_bytes
        )

    def distill(self, case_ids: list[str], topic: str = "") -> Lesson:
        """Distill cases into a lesson.

        Foundation: returns stub. Full impl uses LLM to summarize.
        """
        if len(case_ids) < 2:
            raise ValueError(f"need ≥2 cases to distill, got {len(case_ids)}")
        import hashlib
        h = hashlib.sha256(":".join(sorted(case_ids)).encode()).hexdigest()[:12]
        return Lesson(
            id=f"lesson-{h}",
            title=f"[stub] Distilled lesson from {len(case_ids)} cases" + (f" on {topic}" if topic else ""),
            body=f"[stub] Synthesized lesson body. Full impl uses LLM to summarize {case_ids}.",
            source_case_ids=case_ids,
            did_author=self.did_owner,
        )

    def is_archive_candidate(self, case_created_at: str) -> bool:
        """True if case is old enough to archive to Arweave."""
        try:
            created = datetime.fromisoformat(case_created_at.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - created
            return age >= timedelta(days=self.config.min_age_days)
        except Exception:
            return False


__all__ = ["Lesson", "CompactionConfig", "MemoryCompactor"]
