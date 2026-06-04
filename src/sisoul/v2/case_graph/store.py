"""Case store — vault/cases/ 文件 + ChromaDB embed index.

Foundation skeleton — full impl 在 v2.0 ship.
"""
from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .schema import Case, CaseRetrieval, derive_case_id


class CaseStore:
    """Persist cases to vault/cases/ + maintain index."""

    def __init__(self, vault_dir: Path):
        self.vault_dir = Path(vault_dir).expanduser()
        self.cases_dir = self.vault_dir / "cases"
        self.cases_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = self.vault_dir / "cases_index.json"
        from .vector_index import TfIdfIndex
        self._vec = TfIdfIndex()
        # rebuild vector index from existing cases
        for c in self.list_all():
            self._vec.add(c)

    def add(self, case: Case) -> Path:
        """Add a case to store. Returns path."""
        if not case.validate():
            raise ValueError(f"invalid case {case.id}")
        path = self.cases_dir / f"{case.id}.json"
        path.write_text(json.dumps(asdict(case), ensure_ascii=False, indent=2))
        self._update_index(case)
        self._vec.add(case)
        return path

    def get(self, case_id: str) -> Optional[Case]:
        path = self.cases_dir / f"{case_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        return Case(**data)

    def list_all(self) -> list[Case]:
        cases = []
        for path in self.cases_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                cases.append(Case(**data))
            except Exception:
                continue
        return cases

    def search(self, query: str, top_k: int = 5) -> CaseRetrieval:
        """TF-IDF vector search (foundation). Full impl uses ChromaDB embed."""
        hits = []
        scored = self._vec.search(query, top_k=top_k, threshold=0.0)
        for cid, score in scored:
            c = self.get(cid)
            if c is not None:
                hits.append(c)
        # fallback: if vector index miss, naive substring
        if not hits:
            cases = self.list_all()
            q_lower = query.lower()
            hits = [c for c in cases if q_lower in c.question.lower() or q_lower in c.answer.lower()]
            hits = hits[:top_k]
        return CaseRetrieval(query=query, cases=hits, top_k=top_k)

    def _update_index(self, case: Case) -> None:
        """Append to lightweight index (full impl maintains vector index)."""
        index = {}
        if self.index_file.exists():
            try:
                index = json.loads(self.index_file.read_text())
            except Exception:
                index = {}
        index[case.id] = {
            "question": case.question,
            "did_author": case.did_author,
            "tags": case.tags,
            "created_at": case.created_at,
        }
        self.index_file.write_text(json.dumps(index, ensure_ascii=False, indent=2))


__all__ = ["CaseStore"]
