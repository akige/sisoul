"""pytest conftest · 兜底注入 src/ 到 sys.path.

editable install (.pth) 在 CJK 路径 + C locale 下偶发失效 (并行 agent 同时改 site-packages
触发 'Resource deadlock avoided'). conftest 兜底用 PYTHONPATH 逻辑直接注入 src/, 保证测试稳定.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# Common fixtures usable across test modules.
import pytest


@pytest.fixture
def tmp_vault(tmp_path, monkeypatch):
    """Set up isolated SISOUL_VAULT with minimal dna.json + petnames.json."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "dna.json").write_text('{"sisoul_version": "1.0.0-alpha", "schema_version": 2}')
    (vault / "petnames.json").write_text("{}")
    monkeypatch.setenv("SISOUL_VAULT", str(vault))
    return vault


@pytest.fixture
def tmp_skills_dir(tmp_path, monkeypatch):
    """Isolated SISOUL_SKILLS_DIR."""
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("SISOUL_SKILLS_DIR", str(skills))
    return skills


@pytest.fixture
def sample_case_data():
    """Common Case payload for v2 tests."""
    return {
        "question": "How to fix Rust async tokio deadlock?",
        "answer": "use unwrap_or_else + Drop impl + cancellation token",
        "did_author": "did:key:z6MkSampleAuthor",
        "tags": ["rust", "async"],
    }


@pytest.fixture
def sample_did():
    """Standard test DID."""
    return "did:key:z6MkTestUser0123456789abcdef"
