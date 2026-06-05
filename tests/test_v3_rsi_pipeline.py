"""tests for v3 RSI pipeline (history store + LLM adapter wire + run_iteration)."""
from __future__ import annotations
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("SISOUL_VAULT", str(tmp_path))
    return tmp_path


def test_history_file_default_location(monkeypatch, tmp_path):
    monkeypatch.setenv("SISOUL_VAULT", str(tmp_path))
    from sisoul.v3.rsi.pipeline import _history_file

    assert _history_file() == tmp_path / "rsi" / "history.jsonl"


def test_append_load_history_roundtrip(vault):
    from sisoul.v3.rsi.pipeline import IterationRecord, append_history, load_history

    rec = IterationRecord(
        iteration_id="rsi-test-1",
        mode="godel",
        started_at="2026-06-05T00:00:00Z",
        finished_at="2026-06-05T00:00:01Z",
        accepted=True,
        fitness=0.92,
        candidate_count=5,
        reason="ok",
        dry_run=True,
    )
    append_history(rec)
    loaded = load_history()
    assert len(loaded) == 1
    assert loaded[0].iteration_id == "rsi-test-1"
    assert loaded[0].accepted is True
    assert loaded[0].fitness == 0.92


def test_load_history_skips_malformed(vault):
    from sisoul.v3.rsi.pipeline import _history_file, load_history

    f = _history_file()
    f.parent.mkdir(parents=True)
    f.write_text(
        '{"iteration_id":"rsi-1","mode":"godel","started_at":"x","finished_at":"y","accepted":true,"fitness":0.5,"candidate_count":3,"reason":"ok","dry_run":true}\n'
        "NOT_JSON\n"
        '{"iteration_id":"rsi-2","mode":"dspy","started_at":"a","finished_at":"b","accepted":false,"fitness":null,"candidate_count":0,"reason":"x","dry_run":true}\n'
    )
    loaded = load_history()
    assert len(loaded) == 2


def test_load_history_limit(vault):
    from sisoul.v3.rsi.pipeline import IterationRecord, append_history, load_history

    for i in range(50):
        rec = IterationRecord(
            iteration_id=f"rsi-{i}",
            mode="godel",
            started_at="x", finished_at="y",
            accepted=False, fitness=None,
            candidate_count=0, reason="", dry_run=True,
        )
        append_history(rec)
    last_10 = load_history(limit=10)
    assert len(last_10) == 10
    # Last 10 records: rsi-40 .. rsi-49 (last 10 appended)
    assert last_10[0].iteration_id == "rsi-40"
    assert last_10[-1].iteration_id == "rsi-49"


def test_get_llm_adapter_unknown_provider_returns_none(monkeypatch):
    monkeypatch.setenv("SISOUL_RSI_PROVIDER", "nonexistent_xyz")
    from sisoul.v3.rsi.pipeline import get_llm_adapter

    assert get_llm_adapter() is None


def test_run_iteration_unknown_mode(vault):
    from sisoul.v3.rsi.pipeline import run_iteration

    rec = run_iteration(mode="bogus")
    assert rec.accepted is False
    assert "unknown mode" in rec.reason
    # persisted
    f = vault / "rsi" / "history.jsonl"
    assert f.exists()
    rows = f.read_text().splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["mode"] == "bogus"


def test_run_iteration_no_llm_adapter_reports_reason(vault, monkeypatch):
    monkeypatch.setenv("SISOUL_RSI_PROVIDER", "nonexistent_provider_xyz")
    from sisoul.v3.rsi.pipeline import run_iteration

    rec = run_iteration(mode="godel", dry_run=True)
    assert rec.accepted is False
    assert "LLM adapter" in rec.reason or "adapter" in rec.reason
    assert rec.mode == "godel"


def test_run_iteration_history_persisted(vault, monkeypatch):
    monkeypatch.setenv("SISOUL_RSI_PROVIDER", "nonexistent_xyz")
    from sisoul.v3.rsi.pipeline import run_iteration, load_history

    rec1 = run_iteration(mode="godel")
    rec2 = run_iteration(mode="alpha_evolve", target_module="src/sisoul/v3/__init__.py")
    rec3 = run_iteration(mode="dspy")

    loaded = load_history()
    assert len(loaded) == 3
    assert {r.mode for r in loaded} == {"godel", "alpha_evolve", "dspy"}


def test_run_iteration_alpha_evolve_no_target(vault, monkeypatch):
    """alpha_evolve without target_module → caught as exception."""
    class StubAdapter:
        def chat(self, messages, **kw):
            return "candidate-output"

    monkeypatch.setattr(
        "sisoul.v3.rsi.pipeline.get_llm_adapter",
        lambda *a, **kw: StubAdapter(),
    )
    from sisoul.v3.rsi.pipeline import run_iteration

    rec = run_iteration(mode="alpha_evolve", target_module=None)
    assert rec.accepted is False
    assert "target_module" in rec.reason or "exception" in rec.reason
