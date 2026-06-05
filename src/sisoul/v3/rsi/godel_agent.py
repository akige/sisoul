"""sisoul v3 RSI · GodelAgent — L2 prompt 自优化 (self-referential).

参考 Gödel Agent (arxiv 2410.04444): self-referential LLM 可读取并动态修改自己的
逻辑 / prompt. 本 skeleton 把"动态修改"收敛到 **prompt 变异** 这一受控子集:

一轮 ``run_iteration``:
    inspect_self_prompt → propose_prompt_mutation (LLM 出 N candidate)
        → 每个 candidate 过 Evaluator.score → 取 fitness 最高者 apply_mutation

🔴 安全红线 (本模块强制):
- 禁改 ``src/sisoul/v3/rsi/`` 自己 — Gödel 自指若能改评估器/安全 gate 则失控.
  ``apply_mutation`` 只动 ``self._current_prompt`` (内存态 prompt), 不写文件系统.
- ``dry_run=True`` 默认 — 真落地需显式 ``dry_run=False``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from sisoul.v3.rsi.evaluator import Evaluator


# 自指保护: 任何变异文本若试图写这些路径前缀, 一律拒绝.
SELF_PATH_GUARD = "src/sisoul/v3/rsi/"

DEFAULT_SEED_PROMPT = (
    "You are sisoul, a sovereign personal AI agent. "
    "Answer concisely, cite sources, refuse unsafe requests."
)


class GodelAgent:
    """L2 self-referential prompt 优化器 (skeleton)."""

    def __init__(
        self,
        daemon_ref: Any,
        llm_adapter: Any,
        evaluator: Optional[Evaluator] = None,
        seed_prompt: str = DEFAULT_SEED_PROMPT,
    ) -> None:
        """初始化 Gödel agent.

        Args:
            daemon_ref: 宿主 daemon 引用 (skeleton 只存, 不真用).
            llm_adapter: ``sisoul.llm.base.LLMAdapter`` 兼容对象 (出变异 candidate).
            evaluator: ``Evaluator`` 实例; None → 调用方在 run_iteration 前必须注入.
            seed_prompt: 初始 prompt.
        """
        self.daemon_ref = daemon_ref
        self.llm_adapter = llm_adapter
        self.evaluator = evaluator
        self._current_prompt = seed_prompt
        self.history: list[dict] = []

    # ── inspect ──────────────────────────────────────────────────────────────
    def inspect_self_prompt(self) -> str:
        """读取自己当前 prompt (self-reference 的 'read' 半边)."""
        return self._current_prompt

    # ── mutate (LLM) ───────────────────────────────────────────────────────
    def propose_prompt_mutation(self, reflection: str, n: int = 3) -> list[str]:
        """让 LLM 基于 reflection 出 N 个 candidate prompt 变异.

        Args:
            reflection: 对当前 prompt 表现的反思 (e.g. evaluator 反馈).
            n: candidate 数量.

        Returns:
            n 个候选 prompt 字符串. LLM 返回非预期格式时尽量降级解析,
            最坏返回 [current_prompt] (no-op, 保证不退化).
        """
        sep = "===VARIANT==="
        meta_prompt = (
            "You are optimizing the SYSTEM PROMPT of an AI agent. The prompt is multi-line markdown.\n"
            f"Current prompt:\n---\n{self._current_prompt}\n---\n"
            f"Reflection on weaknesses:\n{reflection}\n\n"
            f"Propose {n} improved full variants. Each variant is a COMPLETE replacement prompt (multi-line OK).\n"
            f"Separate variants with the line '{sep}' on its own line.\n"
            f"Output {n} variants in total, no numbering, no commentary outside variants."
        )
        raw = self.llm_adapter.chat([{"role": "user", "content": meta_prompt}])
        text = str(raw or "").strip()
        # If LLM used the separator (full multi-line variants), split by it.
        if sep in text:
            parts = [p.strip() for p in text.split(sep) if p.strip()]
            candidates = [p for p in parts if p and p not in ("```", "---")]
            if candidates:
                return candidates[:n]
        # Fallback: line-by-line (LLM ignored separator, e.g. mock or short outputs).
        # Preserves backward compat with the original parsing + existing tests.
        candidates = [ln.strip() for ln in text.splitlines() if ln.strip()]
        candidates = [c.lstrip("0123456789.-) ").strip() for c in candidates if c not in ("```", "---")]
        candidates = [c for c in candidates if c]
        if not candidates:
            return [self._current_prompt]
        return candidates[:n]

    # ── apply (safety-gated) ──────────────────────────────────────────────────
    def _violates_self_guard(self, candidate: str) -> bool:
        """candidate 是否试图触碰 rsi/ 自身路径 (自指保护)."""
        return SELF_PATH_GUARD in candidate

    def apply_mutation(self, candidate: str, dry_run: bool = True) -> bool:
        """应用一个 candidate prompt (有 safety gate).

        Args:
            candidate: 待应用的 prompt.
            dry_run: True (默认) → 只校验不落地; False → 真替换 current_prompt.

        Returns:
            True = 通过校验 (dry_run 下表示"可应用"; 非 dry_run 下表示"已应用").
            False = 被 safety gate 拒绝 (自指保护或 evaluator.safety_check 失败).
        """
        if self._violates_self_guard(candidate):
            return False
        if self.evaluator is not None and not self.evaluator.safety_check(candidate):
            return False
        if dry_run:
            return True
        self._current_prompt = candidate
        return True

    # ── run one iteration ─────────────────────────────────────────────────────
    def run_iteration(self, reflection: str = "", n: int = 3) -> dict:
        """跑一轮 RSI: inspect → mutate → evaluate → best replace.

        Args:
            reflection: 反思文本传给 ``propose_prompt_mutation``.
            n: candidate 数量.

        Returns:
            dict: {before, candidates, scored, best, applied, fitness}.

        Raises:
            RuntimeError: evaluator 未注入.
        """
        if self.evaluator is None:
            raise RuntimeError("GodelAgent.run_iteration 需要 evaluator (在 __init__ 注入或先赋值)")

        before = self.inspect_self_prompt()
        candidates = self.propose_prompt_mutation(reflection, n=n)

        scored: list[tuple[str, float]] = []
        for cand in candidates:
            if self._violates_self_guard(cand):
                continue  # 自指保护: 直接淘汰
            report = self.evaluator.score(cand)
            scored.append((cand, float(report["fitness"])))

        applied = False
        best_prompt = before
        best_fitness = -1.0
        if scored:
            best_prompt, best_fitness = max(scored, key=lambda t: t[1])
            if best_fitness > 0.0 and best_prompt != before:
                applied = self.apply_mutation(best_prompt, dry_run=False)

        result = {
            "before": before,
            "candidates": candidates,
            "scored": scored,
            "best": best_prompt,
            "fitness": best_fitness,
            "applied": applied,
        }
        self.history.append(result)
        return result
