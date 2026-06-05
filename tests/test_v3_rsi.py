"""Tests for sisoul v3 RSI framework skeleton.

覆盖 5 个 class (Evaluator / GodelAgent / AlphaEvolveLoop / DSPyOptimizer /
FederatedRSI) + CLI, 每个 class ≥ 5 case (init / 主方法 / safety / error / integration).

不真调 LLM (用 mock adapter), 不真跑全量 pytest (mock run_pytest).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sisoul.cli import app
from sisoul.v3 import __version__ as v3_version
from sisoul.v3.rsi import (
    AlphaEvolveLoop,
    AllowlistViolation,
    DSPyOptimizer,
    Evaluator,
    FederatedRSI,
    GodelAgent,
    OptimizableModule,
    ScoreReport,
)
from sisoul.v3.rsi.alpha_evolve import _module_allowed
from sisoul.v3.rsi.evaluator import DANGEROUS_PATTERNS


runner = CliRunner()


# ── mock LLM adapter ─────────────────────────────────────────────────────────
class MockAdapter:
    """mock LLMAdapter — chat 返回预设文本 (不真调 LLM)."""

    def __init__(self, reply: str = "") -> None:
        self.reply = reply
        self.calls: list = []

    def chat(self, messages: list[dict], **kwargs) -> str:
        self.calls.append(messages)
        return self.reply


@pytest.fixture
def safe_evaluator(monkeypatch):
    """Evaluator with run_pytest mocked to always pass (no real pytest run)."""
    ev = Evaluator(pytest_root=Path.cwd())
    monkeypatch.setattr(ev, "run_pytest", lambda scope: (10, 0))
    return ev


# ════════════════════════════════════════════════════════════════════════════
# Evaluator
# ════════════════════════════════════════════════════════════════════════════
class TestEvaluator:
    def test_init(self):
        ev = Evaluator(pytest_root=Path("/tmp"))
        assert ev.pytest_root == Path("/tmp")
        assert ev.bench_fn is None

    def test_safety_check_clean_code(self):
        ev = Evaluator(pytest_root=Path("."))
        assert ev.safety_check("def add(a, b):\n    return a + b") is True

    @pytest.mark.parametrize(
        "bad",
        [
            "import os; os.system('ls')",
            "rm -rf /",
            "eval('1+1')",
            "exec('x=1')",
            "__import__('os')",
            "import shutil; shutil.rmtree('/x')",
            "subprocess.run(['ls'])",
        ],
    )
    def test_safety_check_dangerous(self, bad):
        ev = Evaluator(pytest_root=Path("."))
        assert ev.safety_check(bad) is False
        assert len(ev.matched_dangerous(bad)) >= 1

    def test_parse_pytest_output(self):
        assert Evaluator._parse_pytest_output("3 passed in 0.1s") == (3, 0)
        assert Evaluator._parse_pytest_output("2 passed, 1 failed in 0.2s") == (2, 1)
        assert Evaluator._parse_pytest_output("1 passed, 2 errors") == (1, 2)

    def test_run_bench_no_fn(self):
        ev = Evaluator(pytest_root=Path("."))
        assert ev.run_bench("anything") == 0.0

    def test_run_bench_with_fn(self):
        ev = Evaluator(pytest_root=Path("."), bench_fn=lambda t: None)
        assert ev.run_bench("x") >= 0.0

    def test_run_bench_fn_raises_returns_inf(self):
        def boom(_):
            raise RuntimeError("bench broke")

        ev = Evaluator(pytest_root=Path("."), bench_fn=boom)
        assert ev.run_bench("x") == float("inf")

    def test_score_safety_reject(self):
        ev = Evaluator(pytest_root=Path("."))
        report = ev.score("os.system('x')")
        assert report["safe"] is False
        assert report["fitness"] == 0.0

    def test_score_pytest_gate_fail(self, monkeypatch):
        ev = Evaluator(pytest_root=Path("."))
        monkeypatch.setattr(ev, "run_pytest", lambda scope: (5, 2))
        report = ev.score("def f(): pass")
        assert report["fitness"] == 0.0
        assert report["fail_count"] == 2

    def test_score_pass_no_bench(self, safe_evaluator):
        report = safe_evaluator.score("def f(): pass")
        assert report["safe"] is True
        assert report["fail_count"] == 0
        assert report["fitness"] == 1.0

    def test_score_with_bench_decays_fitness(self, monkeypatch):
        ev = Evaluator(pytest_root=Path("."), bench_fn=lambda t: None)
        monkeypatch.setattr(ev, "run_pytest", lambda scope: (10, 0))
        monkeypatch.setattr(ev, "run_bench", lambda t: 1.0)
        report = ev.score("def f(): pass", bench_target="x")
        assert 0.0 < report["fitness"] < 1.0  # 1/(1+1) = 0.5

    def test_score_report_dataclass(self):
        r = ScoreReport(safe=True, pass_count=1, fail_count=0, bench_seconds=0.0, fitness=1.0)
        d = r.as_dict()
        assert d["fitness"] == 1.0 and d["safe"] is True


# ════════════════════════════════════════════════════════════════════════════
# GodelAgent
# ════════════════════════════════════════════════════════════════════════════
class TestGodelAgent:
    def test_init(self):
        ag = GodelAgent(daemon_ref=None, llm_adapter=MockAdapter())
        assert ag.inspect_self_prompt()
        assert ag.history == []

    def test_inspect_self_prompt(self):
        ag = GodelAgent(daemon_ref=None, llm_adapter=MockAdapter(), seed_prompt="hello")
        assert ag.inspect_self_prompt() == "hello"

    def test_propose_mutation_parses_lines(self):
        adapter = MockAdapter(reply="variant one\nvariant two\nvariant three")
        ag = GodelAgent(daemon_ref=None, llm_adapter=adapter)
        cands = ag.propose_prompt_mutation("be better", n=3)
        assert len(cands) == 3
        assert "variant one" in cands

    def test_propose_mutation_strips_numbering(self):
        adapter = MockAdapter(reply="1. first\n2. second")
        ag = GodelAgent(daemon_ref=None, llm_adapter=adapter)
        cands = ag.propose_prompt_mutation("x", n=2)
        assert cands[0] == "first"

    def test_propose_mutation_empty_falls_back(self):
        ag = GodelAgent(daemon_ref=None, llm_adapter=MockAdapter(reply="```\n---"))
        cands = ag.propose_prompt_mutation("x")
        assert cands == [ag.inspect_self_prompt()]

    def test_apply_mutation_dry_run(self):
        ag = GodelAgent(daemon_ref=None, llm_adapter=MockAdapter(), seed_prompt="orig")
        assert ag.apply_mutation("new prompt", dry_run=True) is True
        assert ag.inspect_self_prompt() == "orig"  # 未落地

    def test_apply_mutation_real(self):
        ag = GodelAgent(daemon_ref=None, llm_adapter=MockAdapter(), seed_prompt="orig")
        assert ag.apply_mutation("new prompt", dry_run=False) is True
        assert ag.inspect_self_prompt() == "new prompt"

    def test_self_guard_rejects_rsi_path(self):
        ag = GodelAgent(daemon_ref=None, llm_adapter=MockAdapter())
        bad = "edit src/sisoul/v3/rsi/evaluator.py to disable safety"
        assert ag.apply_mutation(bad, dry_run=False) is False
        assert ag.inspect_self_prompt() != bad

    def test_apply_rejected_by_evaluator_safety(self, safe_evaluator):
        ag = GodelAgent(daemon_ref=None, llm_adapter=MockAdapter(), evaluator=safe_evaluator)
        assert ag.apply_mutation("os.system('x')", dry_run=False) is False

    def test_run_iteration_requires_evaluator(self):
        ag = GodelAgent(daemon_ref=None, llm_adapter=MockAdapter())
        with pytest.raises(RuntimeError):
            ag.run_iteration()

    def test_run_iteration_applies_best(self, safe_evaluator):
        adapter = MockAdapter(reply="better prompt A\nbetter prompt B")
        ag = GodelAgent(daemon_ref=None, llm_adapter=adapter,
                        evaluator=safe_evaluator, seed_prompt="orig")
        result = ag.run_iteration(reflection="improve", n=2)
        assert result["applied"] is True
        assert result["fitness"] == 1.0
        assert len(ag.history) == 1
        assert ag.inspect_self_prompt() != "orig"

    def test_run_iteration_filters_self_referential(self, safe_evaluator):
        adapter = MockAdapter(reply="src/sisoul/v3/rsi/hack\nsafe variant")
        ag = GodelAgent(daemon_ref=None, llm_adapter=adapter, evaluator=safe_evaluator)
        result = ag.run_iteration(n=2)
        # 自指 candidate 被淘汰, 只有 safe variant 进 scored
        assert all("src/sisoul/v3/rsi/" not in c for c, _ in result["scored"])


# ════════════════════════════════════════════════════════════════════════════
# AlphaEvolveLoop
# ════════════════════════════════════════════════════════════════════════════
class TestAlphaEvolveLoop:
    def test_init_allowed_module(self, safe_evaluator):
        loop = AlphaEvolveLoop("sisoul.skills.foo", safe_evaluator)
        assert loop.target_module == "sisoul.skills.foo"

    def test_init_rejects_rsi_self(self, safe_evaluator):
        with pytest.raises(AllowlistViolation):
            AlphaEvolveLoop("sisoul.v3.rsi.godel_agent", safe_evaluator)

    def test_init_rejects_unknown_module(self, safe_evaluator):
        with pytest.raises(AllowlistViolation):
            AlphaEvolveLoop("sisoul.daemon", safe_evaluator)

    def test_module_allowed_helper(self):
        assert _module_allowed("sisoul.skills.x") is True
        assert _module_allowed("sisoul.v3.rsi") is False
        assert _module_allowed("sisoul.v3.rsi.evaluator") is False
        assert _module_allowed("random.thing") is False

    def test_generate_candidates_no_llm(self, safe_evaluator):
        loop = AlphaEvolveLoop("sisoul.skills.x", safe_evaluator, n_candidates=4)
        cands = loop.generate_candidates("def f(): pass")
        assert len(cands) == 4
        assert all("def f(): pass" in c for c in cands)

    def test_generate_candidates_with_llm(self, safe_evaluator):
        adapter = MockAdapter(reply="codeA\n===CANDIDATE===\ncodeB")
        loop = AlphaEvolveLoop("sisoul.skills.x", safe_evaluator,
                               llm_adapter=adapter, n_candidates=5)
        cands = loop.generate_candidates("seed")
        assert "codeA" in cands and "codeB" in cands

    def test_evaluate_candidate(self, safe_evaluator):
        loop = AlphaEvolveLoop("sisoul.skills.x", safe_evaluator)
        assert loop.evaluate_candidate("def f(): pass") == 1.0

    def test_evaluate_candidate_dangerous_zero(self, safe_evaluator):
        loop = AlphaEvolveLoop("sisoul.skills.x", safe_evaluator)
        assert loop.evaluate_candidate("os.system('x')") == 0.0

    def test_select_best(self):
        assert AlphaEvolveLoop.select_best([("a", 0.1), ("b", 0.9), ("c", 0.5)]) == "b"

    def test_select_best_empty(self):
        assert AlphaEvolveLoop.select_best([]) == ""

    def test_iterate_runs_generations(self, safe_evaluator):
        loop = AlphaEvolveLoop("sisoul.skills.x", safe_evaluator, n_candidates=2)
        result = loop.iterate("def f(): pass", max_iter=3)
        assert result["n_gen"] == 3
        assert result["best_fitness"] == 1.0
        assert len(loop.generations) == 3

    def test_iterate_best_fitness_tracked(self, monkeypatch, safe_evaluator):
        loop = AlphaEvolveLoop("sisoul.skills.x", safe_evaluator, n_candidates=2)
        result = loop.iterate("seed", max_iter=2)
        assert result["best"]
        assert 0.0 <= result["best_fitness"] <= 1.0


# ════════════════════════════════════════════════════════════════════════════
# DSPyOptimizer
# ════════════════════════════════════════════════════════════════════════════
class _FakeModule:
    def __init__(self):
        self.prompt = "base prompt"
        self.demos = []

    def __call__(self, x):
        return x


class TestDSPyOptimizer:
    def test_init(self):
        opt = DSPyOptimizer(metric_fn=lambda e, p: 1.0, train_examples=[1, 2, 3])
        assert len(opt.train_examples) == 3

    def test_bootstrap_demos_picks_top_n(self):
        # metric = the example value itself → top-2 = [5, 4]
        opt = DSPyOptimizer(metric_fn=lambda e, p: float(e), train_examples=[1, 5, 3, 4, 2])
        demos = opt.bootstrap_demos(n=2)
        assert demos == [5, 4]

    def test_bootstrap_demos_metric_error_ranks_last(self):
        def metric(e, p):
            if e == "bad":
                raise ValueError("nope")
            return float(e)

        opt = DSPyOptimizer(metric_fn=metric, train_examples=[2, "bad", 1])
        demos = opt.bootstrap_demos(n=3)
        assert demos[-1] == "bad"  # 评分失败排末位

    def test_gold_of_variants(self):
        assert DSPyOptimizer._gold_of({"gold": 42}) == 42
        assert DSPyOptimizer._gold_of({"label": 7}) == 7
        assert DSPyOptimizer._gold_of(("input", "answer")) == "answer"
        assert DSPyOptimizer._gold_of("plain") == "plain"

    def test_compile_returns_optimized_copy(self):
        opt = DSPyOptimizer(metric_fn=lambda e, p: 1.0, train_examples=[1, 2, 3])
        mod = _FakeModule()
        out = opt.compile(mod, n_demos=2)
        assert out is not mod  # deepcopy
        assert len(out.demos) == 2
        assert "DSPyOptimizer" in out.prompt
        assert mod.prompt == "base prompt"  # 原 module 不变

    def test_compile_rejects_bad_module(self):
        opt = DSPyOptimizer(metric_fn=lambda e, p: 1.0, train_examples=[])
        with pytest.raises(TypeError):
            opt.compile(object())

    def test_optimizable_module_protocol(self):
        assert isinstance(_FakeModule(), OptimizableModule)
        assert not isinstance(object(), OptimizableModule)

    def test_compiled_demos_property(self):
        opt = DSPyOptimizer(metric_fn=lambda e, p: float(e), train_examples=[3, 1, 2])
        opt.bootstrap_demos(n=1)
        assert opt.compiled_demos == [3]


# ════════════════════════════════════════════════════════════════════════════
# FederatedRSI
# ════════════════════════════════════════════════════════════════════════════
class _FakeTransport:
    """async transport mock — 记录 send, subscribe 存 callback."""

    def __init__(self):
        self.sent: list = []
        self.subs: dict = {}

    async def send(self, topic, payload):
        self.sent.append((topic, payload))
        # fan-out 给 subscriber (模拟 gossip)
        for cb in self.subs.get(topic, []):
            await cb(payload)
        return payload

    async def subscribe_topic(self, topic, callback):
        self.subs.setdefault(topic, []).append(callback)


class TestFederatedRSI:
    def test_init(self):
        fed = FederatedRSI(self_did="did:sisoul:abc")
        assert fed.self_did == "did:sisoul:abc"
        assert fed.received_mutations == []

    def test_gossip_requires_transport(self):
        fed = FederatedRSI(self_did="did:x")
        with pytest.raises(RuntimeError):
            asyncio.run(fed.gossip_mutation({"kind": "prompt"}))

    def test_gossip_mutation_sends(self):
        t = _FakeTransport()
        fed = FederatedRSI(self_did="did:x", transport=t)
        env = asyncio.run(fed.gossip_mutation({"kind": "prompt", "fitness": 0.9}))
        assert env["origin"] == "did:x"
        assert len(t.sent) == 1

    def test_gossip_sync_wrapper(self):
        t = _FakeTransport()
        fed = FederatedRSI(self_did="did:x", transport=t)
        env = fed.gossip_mutation_sync({"kind": "code"})
        assert env["mutation"]["kind"] == "code"

    def test_subscribe_ignores_own_echo(self):
        async def scenario():
            t = _FakeTransport()
            fed = FederatedRSI(self_did="did:self", transport=t)
            await fed.subscribe_peer_mutations()
            await fed.gossip_mutation({"kind": "prompt"})  # 自己发的
            return fed

        fed = asyncio.run(scenario())
        assert fed.received_mutations == []  # 自己的回声被忽略

    def test_subscribe_receives_peer_mutation(self):
        async def scenario():
            t = _FakeTransport()
            me = FederatedRSI(self_did="did:me", transport=t)
            peer = FederatedRSI(self_did="did:peer", transport=t)
            await me.subscribe_peer_mutations()
            await peer.gossip_mutation({"kind": "code", "fitness": 0.8})
            return me

        me = asyncio.run(scenario())
        assert len(me.received_mutations) == 1
        assert me.received_mutations[0]["kind"] == "code"

    def test_subscribe_callback_invoked(self):
        seen = []

        async def scenario():
            t = _FakeTransport()
            me = FederatedRSI(self_did="did:me", transport=t)
            peer = FederatedRSI(self_did="did:peer", transport=t)

            async def cb(m):
                seen.append(m)

            await me.subscribe_peer_mutations(callback=cb)
            await peer.gossip_mutation({"kind": "x"})

        asyncio.run(scenario())
        assert len(seen) == 1

    def test_merge_lora_scalar(self):
        merged = FederatedRSI.merge_lora_weights(
            [{"layer0": 2.0}, {"layer0": 4.0}]
        )
        assert merged["layer0"] == 3.0

    def test_merge_lora_with_self(self):
        merged = FederatedRSI.merge_lora_weights(
            [{"w": 3.0}], self_weights={"w": 9.0}
        )
        assert merged["w"] == 6.0

    def test_merge_lora_vectors(self):
        merged = FederatedRSI.merge_lora_weights(
            [{"w": [2.0, 4.0]}, {"w": [4.0, 8.0]}]
        )
        assert merged["w"] == [3.0, 6.0]

    def test_merge_lora_missing_key_as_zero(self):
        merged = FederatedRSI.merge_lora_weights(
            [{"a": 4.0}, {"b": 4.0}]
        )
        # a 在第二个缺失 → (4+0)/2 = 2.0
        assert merged["a"] == 2.0 and merged["b"] == 2.0

    def test_merge_lora_empty_raises(self):
        with pytest.raises(ValueError):
            FederatedRSI.merge_lora_weights([])


# ════════════════════════════════════════════════════════════════════════════
# CLI integration
# ════════════════════════════════════════════════════════════════════════════
class TestCLI:
    def test_rsi_help(self):
        r = runner.invoke(app, ["rsi", "--help"])
        assert r.exit_code == 0
        assert "rsi" in r.stdout.lower()

    def test_rsi_iterate_help(self):
        r = runner.invoke(app, ["rsi", "iterate", "--help"])
        assert r.exit_code == 0
        assert "module" in r.stdout.lower()

    def test_rsi_iterate_rejects_self(self):
        r = runner.invoke(app, ["rsi", "iterate", "--module", "sisoul.v3.rsi.evaluator"])
        assert r.exit_code == 2

    def test_rsi_iterate_runs(self, tmp_path, monkeypatch):
        import sisoul.cli_commands.v3_rsi as mod
        monkeypatch.setattr(mod, "HISTORY_PATH", tmp_path / "rsi_history.json")
        # mock run_pytest 避免真跑全量
        from sisoul.v3.rsi.evaluator import Evaluator as _Ev
        monkeypatch.setattr(_Ev, "run_pytest", lambda self, scope: (10, 0))
        r = runner.invoke(
            app, ["rsi", "iterate", "--module", "sisoul.skills.demo", "--max-iter", "2"]
        )
        assert r.exit_code == 0
        assert "RSI iterate" in r.stdout
        assert (tmp_path / "rsi_history.json").exists()

    def test_rsi_history_empty(self, tmp_path, monkeypatch):
        import sisoul.cli_commands.v3_rsi as mod
        monkeypatch.setattr(mod, "HISTORY_PATH", tmp_path / "empty.json")
        r = runner.invoke(app, ["rsi", "history"])
        assert r.exit_code == 0
        assert "暂无" in r.stdout

    def test_rsi_history_json(self, tmp_path, monkeypatch):
        import sisoul.cli_commands.v3_rsi as mod
        hist = tmp_path / "h.json"
        hist.write_text('[{"module": "m", "n_gen": 2, "best_fitness": 1.0}]', encoding="utf-8")
        monkeypatch.setattr(mod, "HISTORY_PATH", hist)
        r = runner.invoke(app, ["rsi", "history", "--json"])
        assert r.exit_code == 0
        assert "best_fitness" in r.stdout


# ════════════════════════════════════════════════════════════════════════════
# package-level
# ════════════════════════════════════════════════════════════════════════════
def test_v3_version():
    assert v3_version == "0.1.0-alpha-skeleton"


def test_dangerous_patterns_nonempty():
    assert len(DANGEROUS_PATTERNS) >= 5
