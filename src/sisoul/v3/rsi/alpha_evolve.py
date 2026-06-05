"""sisoul v3 RSI · AlphaEvolveLoop — L3 code 进化 (evolutionary codegen).

参考 AlphaEvolve (DeepMind 2025-05): LLM 生成代码变异 → 自动 evaluator 打分 →
保留最优 → 迭代多代. 本 skeleton 实现完整进化主循环, LLM 调用走注入的 adapter.

一代 ``iterate`` step:
    generate_candidates(seed) → 每个 evaluate_candidate (Evaluator.score 算 fitness)
        → select_best → 作为下一代 seed

🔴 安全红线 (本模块强制):
- ``target_module`` 必须在 ``MODULE_ALLOWLIST`` — 禁进化 rsi/* 自己.
- 每个 candidate 过 ``Evaluator.score`` (含 safety_check + pytest gate); fitness<=0 淘汰.
- 进化只在内存里产生 candidate code, 不自动写文件系统 (落地需调用方显式做 + review).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sisoul.v3.rsi.evaluator import Evaluator


# 允许被 AlphaEvolve 进化的模块前缀 (白名单). 禁止 rsi/ 自身.
MODULE_ALLOWLIST: tuple[str, ...] = (
    "sisoul.skills.",
    "sisoul.goal.",
    "sisoul.rag.",
    "sisoul.chat.",
)

# 显式拒绝前缀 (即便 allowlist 漏配也兜底).
MODULE_DENYLIST: tuple[str, ...] = (
    "sisoul.v3.rsi",
)


class AllowlistViolation(ValueError):
    """target_module 不在 allowlist / 命中 denylist 时抛."""


@dataclass
class Generation:
    """一代进化的结果."""

    gen: int
    seed: str
    candidates: list[tuple[str, float]] = field(default_factory=list)
    best: str = ""
    best_fitness: float = 0.0

    def as_dict(self) -> dict:
        return {
            "gen": self.gen,
            "seed": self.seed,
            "candidates": self.candidates,
            "best": self.best,
            "best_fitness": self.best_fitness,
        }


def _module_allowed(target_module: str) -> bool:
    """target_module 是否被允许进化."""
    if any(target_module == d or target_module.startswith(d) for d in MODULE_DENYLIST):
        return False
    return any(target_module.startswith(p) for p in MODULE_ALLOWLIST)


class AlphaEvolveLoop:
    """L3 evolutionary code 进化主循环 (skeleton)."""

    def __init__(
        self,
        target_module: str,
        evaluator: Evaluator,
        llm_adapter: Optional[Any] = None,
        n_candidates: int = 5,
    ) -> None:
        """初始化进化循环.

        Args:
            target_module: 被进化的模块 dotted-path; 必须过 allowlist.
            evaluator: ``Evaluator`` (算 fitness).
            llm_adapter: ``LLMAdapter`` 兼容对象 (生 candidate); None → 退化为
                         seed 的简单确定性变形 (skeleton 自测用, 不真改语义).
            n_candidates: 每代生成的 candidate 数.

        Raises:
            AllowlistViolation: target_module 不被允许.
        """
        if not _module_allowed(target_module):
            raise AllowlistViolation(
                f"target_module={target_module!r} 不在 allowlist {MODULE_ALLOWLIST} "
                f"或命中 denylist {MODULE_DENYLIST} (禁进化 rsi/ 自身)"
            )
        self.target_module = target_module
        self.evaluator = evaluator
        self.llm_adapter = llm_adapter
        self.n_candidates = n_candidates
        self.generations: list[Generation] = []

    # ── generate ─────────────────────────────────────────────────────────────
    def generate_candidates(self, seed_code: str) -> list[str]:
        """生成 N 个 code 变异 candidate.

        有 llm_adapter → 让 LLM 出变异; 无 → 确定性占位变形 (附注释, 不改语义),
        保证 skeleton 在无 LLM 时也能跑完进化循环.

        Args:
            seed_code: 种子代码.

        Returns:
            n_candidates 个候选代码字符串.
        """
        if self.llm_adapter is not None:
            prompt = (
                f"Improve this code for module {self.target_module}. "
                f"Produce {self.n_candidates} variants separated by a line '===CANDIDATE==='. "
                f"Keep behavior correct (must pass pytest).\n\n{seed_code}"
            )
            raw = str(self.llm_adapter.chat([{"role": "user", "content": prompt}]))
            parts = [p.strip() for p in raw.split("===CANDIDATE===") if p.strip()]
            if parts:
                return parts[: self.n_candidates]
        # fallback: 确定性变形 (加变体标记注释), 语义不变
        return [
            f"{seed_code}\n# alpha-evolve variant {i} (skeleton no-op mutation)"
            for i in range(self.n_candidates)
        ]

    # ── evaluate ─────────────────────────────────────────────────────────────
    def evaluate_candidate(self, code: str, scope: str = "tests") -> float:
        """跑 evaluator 算单 candidate fitness.

        Args:
            code: candidate 代码.
            scope: pytest scope 传给 evaluator.

        Returns:
            fitness ∈ [0, 1]. safety / pytest gate 失败 → 0.0.
        """
        report = self.evaluator.score(code, scope=scope)
        return float(report["fitness"])

    # ── select ───────────────────────────────────────────────────────────────
    @staticmethod
    def select_best(candidates: list[tuple[str, float]]) -> str:
        """从 (code, fitness) 列表选 fitness 最高者.

        Args:
            candidates: [(code, fitness), ...].

        Returns:
            最优 code; 空列表 → "".
        """
        if not candidates:
            return ""
        return max(candidates, key=lambda t: t[1])[0]

    # ── iterate ──────────────────────────────────────────────────────────────
    def iterate(self, seed: str, max_iter: int = 10, scope: str = "tests") -> dict:
        """进化 max_iter 代, 每代保留最优作为下一代 seed.

        Args:
            seed: 初始种子代码.
            max_iter: 最大代数.
            scope: pytest scope.

        Returns:
            dict: {best, best_fitness, generations, n_gen}.
        """
        current_seed = seed
        overall_best = seed
        overall_fitness = -1.0

        for gen in range(max_iter):
            cands = self.generate_candidates(current_seed)
            scored = [(c, self.evaluate_candidate(c, scope=scope)) for c in cands]
            best_code = self.select_best(scored)
            best_fit = max((f for _, f in scored), default=0.0)

            self.generations.append(
                Generation(gen=gen, seed=current_seed, candidates=scored,
                           best=best_code, best_fitness=best_fit)
            )

            if best_fit > overall_fitness:
                overall_fitness = best_fit
                overall_best = best_code
            current_seed = best_code or current_seed

        return {
            "best": overall_best,
            "best_fitness": overall_fitness,
            "generations": [g.as_dict() for g in self.generations],
            "n_gen": len(self.generations),
        }
