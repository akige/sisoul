"""RSI iteration pipeline — wires GodelAgent / AlphaEvolveLoop / DSPyOptimizer
to LLM adapter + Evaluator + history store (vault/rsi/history.jsonl).

Used by daemon /v3/rsi/iterate endpoint + `sisoul rsi iterate` CLI.
"""
from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

from sisoul.v3.rsi.evaluator import Evaluator


@dataclass
class IterationRecord:
    iteration_id: str
    mode: str
    started_at: str
    finished_at: str
    accepted: bool
    fitness: Optional[float]
    candidate_count: int
    reason: str
    dry_run: bool


def _vault_dir() -> Path:
    return Path(os.environ.get("SISOUL_VAULT", "~/.sisoul")).expanduser()


def _history_file() -> Path:
    return _vault_dir() / "rsi" / "history.jsonl"


def _ensure_history_dir() -> None:
    _history_file().parent.mkdir(parents=True, exist_ok=True)


def append_history(record: IterationRecord) -> None:
    _ensure_history_dir()
    f = _history_file()
    with f.open("a") as fp:
        fp.write(json.dumps(asdict(record)) + "\n")


def load_history(limit: int = 100) -> list[IterationRecord]:
    f = _history_file()
    if not f.exists():
        return []
    out: list[IterationRecord] = []
    lines = f.read_text().splitlines()
    for line in lines[-limit:]:
        try:
            obj = json.loads(line)
            out.append(IterationRecord(**obj))
        except Exception:
            continue
    return out


def get_llm_adapter(provider: Optional[str] = None) -> Any:
    """Resolve LLMAdapter from sisoul.llm.{provider}.

    Priority: explicit `provider` arg → SISOUL_RSI_PROVIDER env → 'openrouter'.
    Returns None if no provider importable / API key missing (caller mock-falls-back).
    """
    name = provider or os.environ.get("SISOUL_RSI_PROVIDER") or "openrouter"
    try:
        mod = __import__(f"sisoul.llm.{name}", fromlist=["adapter"])
    except ImportError:
        return None
    # Adapter class: <Name>Adapter, e.g. OpenRouterAdapter
    cls_name = name.title().replace("_", "") + "Adapter"
    cls = getattr(mod, cls_name, None)
    if cls is None:
        return None
    try:
        return cls()
    except Exception:
        return None  # missing API key / config


def run_iteration(
    mode: str,
    target_module: Optional[str] = None,
    dry_run: bool = True,
    llm_adapter: Any = None,
    pytest_root: Optional[Path] = None,
) -> IterationRecord:
    """Run one RSI iteration in given mode.

    Modes:
    - godel: GodelAgent prompt mutation
    - alpha_evolve: AlphaEvolveLoop code mutation (needs target_module)
    - dspy: DSPyOptimizer placeholder

    Returns IterationRecord (also appended to history).
    """
    iter_id = f"rsi-{int(time.time() * 1000)}"
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if mode not in {"godel", "alpha_evolve", "dspy"}:
        rec = IterationRecord(
            iteration_id=iter_id, mode=mode,
            started_at=started, finished_at=started,
            accepted=False, fitness=None, candidate_count=0,
            reason=f"unknown mode: {mode}", dry_run=dry_run,
        )
        append_history(rec)
        return rec

    # Resolve LLM adapter if not provided
    if llm_adapter is None:
        llm_adapter = get_llm_adapter()

    if llm_adapter is None:
        finished = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rec = IterationRecord(
            iteration_id=iter_id, mode=mode,
            started_at=started, finished_at=finished,
            accepted=False, fitness=None, candidate_count=0,
            reason="no LLM adapter (set SISOUL_RSI_PROVIDER + provider API key)",
            dry_run=dry_run,
        )
        append_history(rec)
        return rec

    evaluator = Evaluator(pytest_root=pytest_root or Path.cwd())

    try:
        if mode == "godel":
            from sisoul.v3.rsi.godel_agent import GodelAgent

            agent = GodelAgent(
                daemon_ref=None,
                llm_adapter=llm_adapter,
                evaluator=evaluator,
            )
            result = agent.run_iteration(dry_run=dry_run)
        elif mode == "alpha_evolve":
            if not target_module:
                raise ValueError("alpha_evolve requires target_module")
            from sisoul.v3.rsi.alpha_evolve import AlphaEvolveLoop

            loop = AlphaEvolveLoop(
                target_module=target_module,
                evaluator=evaluator,
                n_candidates=5,
            )
            seed = Path(target_module).read_text() if Path(target_module).exists() else ""
            result = loop.iterate(seed=seed, max_iter=1)
        elif mode == "dspy":
            from sisoul.v3.rsi.dspy_optimize import DSPyOptimizer

            opt = DSPyOptimizer(
                metric_fn=lambda x, y: 1.0,
                train_examples=[],
            )
            result = {"accepted": False, "fitness": None, "candidates": 0, "reason": "DSPy skeleton"}
        else:
            result = {"accepted": False, "fitness": None, "candidates": 0, "reason": "unreachable"}
    except Exception as e:
        finished = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        rec = IterationRecord(
            iteration_id=iter_id, mode=mode,
            started_at=started, finished_at=finished,
            accepted=False, fitness=None, candidate_count=0,
            reason=f"exception: {type(e).__name__}: {e}",
            dry_run=dry_run,
        )
        append_history(rec)
        return rec

    finished = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    accepted = bool(result.get("accepted", False)) if isinstance(result, dict) else False
    fitness = result.get("fitness") if isinstance(result, dict) else None
    candidates = int(result.get("candidates", 0) or result.get("candidate_count", 0)) if isinstance(result, dict) else 0
    reason = result.get("reason", "ok") if isinstance(result, dict) else "ok"

    rec = IterationRecord(
        iteration_id=iter_id, mode=mode,
        started_at=started, finished_at=finished,
        accepted=accepted, fitness=fitness, candidate_count=candidates,
        reason=reason, dry_run=dry_run,
    )
    append_history(rec)
    return rec


__all__ = ["IterationRecord", "append_history", "load_history", "get_llm_adapter", "run_iteration"]
