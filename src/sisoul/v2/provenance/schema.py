"""Provenance Chain schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Citation:
    """Single citation pointing to a source case or skill."""

    source_type: str  # "case" | "skill" | "lora" | "external"
    source_id: str  # case_id / skill_cid / lora_name / URL
    did_author: str
    confidence: float = 1.0
    snippet: Optional[str] = None
    micropayment_sis: float = 0.0  # SIS auto-pay to author


@dataclass
class ProvenanceChain:
    """A response with provenance chain (citations + EAS attest)."""

    response_id: str
    query: str
    answer: str
    did_answerer: str  # who generated the answer
    citations: list[Citation] = field(default_factory=list)
    eas_attestation_uid: Optional[str] = None
    sis_total_paid: float = 0.0
    created_at: str = ""

    def total_micropayment(self) -> float:
        return sum(c.micropayment_sis for c in self.citations)
