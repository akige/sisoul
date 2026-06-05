"""sisoul v3 RSI · Evaluator — 变异候选的综合打分 + 安全 gate.

任何 RSI 变异 (prompt / code / weights) 落地前必须过本模块:

1. ``safety_check(code)`` — grep 危险 pattern (rm -rf / eval / exec / __import__ / subprocess …)
2. ``run_pytest(scope)`` — 跑 pytest, 返回 (pass, fail); fail > 0 → fitness 归零
3. ``run_bench(target)`` — 可选性能 bench, 返回耗时 (秒, 越小越好)
4. ``score(code)``       — 综合上面三项成 0~1 fitness dict

skeleton 约定:
- 不真起子进程跑 pytest 时, ``bench_fn`` / pytest 走真路径但 scope 限定到小范围
- 测试里 mock ``run_pytest`` / ``run_bench`` 避免真跑全量
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


# ── 危险 pattern allowlist (safety_check grep 这些) ──────────────────────────
# 任一命中 → safety_check 返回 False, 变异拒绝落地.
DANGEROUS_PATTERNS: tuple[str, ...] = (
    r"\brm\s+-rf\b",
    r"\bos\.system\s*\(",
    r"\bsubprocess\.(?:call|run|Popen|check_output)\s*\(",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\b__import__\s*\(",
    r"\bshutil\.rmtree\s*\(",
    r"\bos\.remove\s*\(",
    r"\bos\.unlink\s*\(",
    r"\bsocket\.socket\s*\(",
    r":\(\)\s*\{.*\}\s*;\s*:",  # fork bomb
)


@dataclass
class ScoreReport:
    """单次 ``score()`` 的结构化结果."""

    safe: bool
    pass_count: int
    fail_count: int
    bench_seconds: float
    fitness: float
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "safe": self.safe,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "bench_seconds": self.bench_seconds,
            "fitness": self.fitness,
            "detail": self.detail,
        }


class Evaluator:
    """RSI 变异候选的综合评估器 (safety + pytest + bench)."""

    def __init__(
        self,
        pytest_root: Path,
        bench_fn: Optional[Callable[[str], float]] = None,
    ) -> None:
        """初始化 evaluator.

        Args:
            pytest_root: pytest 根目录 (跑 ``run_pytest`` 时的 rootdir).
            bench_fn: 可选性能 bench 函数, 签名 ``(target: str) -> float`` 返回秒数.
                      None → ``run_bench`` 返回 0.0 (不计性能).
        """
        self.pytest_root = Path(pytest_root)
        self.bench_fn = bench_fn

    # ── 1. safety_check ─────────────────────────────────────────────────────
    def safety_check(self, code: str) -> bool:
        """grep 危险 pattern. 任一命中返回 False (拒绝落地).

        Args:
            code: 待检查的源码字符串.

        Returns:
            True = 安全 (无危险 pattern); False = 命中危险 pattern.
        """
        for pat in DANGEROUS_PATTERNS:
            if re.search(pat, code):
                return False
        return True

    def matched_dangerous(self, code: str) -> list[str]:
        """返回 code 命中的所有危险 pattern (调试 / 日志用)."""
        return [pat for pat in DANGEROUS_PATTERNS if re.search(pat, code)]

    # ── 2. run_pytest ───────────────────────────────────────────────────────
    def run_pytest(self, scope: str) -> tuple[int, int]:
        """跑 pytest, 返回 (pass_count, fail_count).

        Args:
            scope: pytest 路径 / -k 表达式 (相对 ``pytest_root``).

        Returns:
            (pass, fail). 解析失败或超时 → (0, 1) (视为失败, fitness 归零).
        """
        target = (self.pytest_root / scope) if scope and not scope.startswith("-") else scope
        cmd = [sys.executable, "-m", "pytest", str(target), "-q", "--no-header", "-p", "no:cacheprovider"]
        try:
            proc = subprocess.run(  # noqa: S603 — 受控 evaluator, scope 来自 allowlist
                cmd,
                cwd=str(self.pytest_root),
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (subprocess.TimeoutExpired, OSError):
            return (0, 1)
        return self._parse_pytest_output(proc.stdout + proc.stderr)

    @staticmethod
    def _parse_pytest_output(output: str) -> tuple[int, int]:
        """从 pytest 输出末行解析 (pass, fail). 纯函数, 测试可直接调."""
        passed = failed = 0
        m_pass = re.search(r"(\d+)\s+passed", output)
        m_fail = re.search(r"(\d+)\s+failed", output)
        m_err = re.search(r"(\d+)\s+error", output)
        if m_pass:
            passed = int(m_pass.group(1))
        if m_fail:
            failed += int(m_fail.group(1))
        if m_err:
            failed += int(m_err.group(1))
        return (passed, failed)

    # ── 3. run_bench ─────────────────────────────────────────────────────────
    def run_bench(self, target: str) -> float:
        """跑性能 bench, 返回耗时 (秒). 无 bench_fn → 0.0.

        Args:
            target: 传给 ``bench_fn`` 的目标标识.

        Returns:
            耗时秒数 (越小越好). bench_fn 抛异常 → ``float('inf')``.
        """
        if self.bench_fn is None:
            return 0.0
        start = time.perf_counter()
        try:
            self.bench_fn(target)
        except Exception:  # noqa: BLE001 — bench 失败视为最差性能
            return float("inf")
        return time.perf_counter() - start

    # ── 4. score ─────────────────────────────────────────────────────────────
    def score(
        self,
        code: str,
        scope: str = "tests",
        bench_target: Optional[str] = None,
    ) -> dict:
        """综合 safety + pytest + bench 成 0~1 fitness dict.

        fitness 计算:
        - safety_check False → fitness = 0.0 (硬否决)
        - fail > 0           → fitness = 0.0 (pytest gate)
        - 否则 base = 1.0, bench 越快 fitness 越接近 1 (有 bench 时轻微衰减)

        Args:
            code: 候选源码 (safety_check 用).
            scope: pytest scope.
            bench_target: bench 目标; None → 不跑 bench.

        Returns:
            ``ScoreReport.as_dict()``.
        """
        safe = self.safety_check(code)
        if not safe:
            return ScoreReport(
                safe=False, pass_count=0, fail_count=0,
                bench_seconds=0.0, fitness=0.0,
                detail={"reason": "safety_check failed", "matched": self.matched_dangerous(code)},
            ).as_dict()

        passed, failed = self.run_pytest(scope)
        if failed > 0:
            return ScoreReport(
                safe=True, pass_count=passed, fail_count=failed,
                bench_seconds=0.0, fitness=0.0,
                detail={"reason": "pytest gate failed"},
            ).as_dict()

        bench_seconds = self.run_bench(bench_target) if bench_target is not None else 0.0
        # bench 越快越好: fitness = 1 / (1 + bench_seconds), 无 bench 时 = 1.0
        fitness = 1.0 if bench_seconds == 0.0 else 1.0 / (1.0 + bench_seconds)
        return ScoreReport(
            safe=True, pass_count=passed, fail_count=failed,
            bench_seconds=bench_seconds, fitness=round(fitness, 6),
            detail={"reason": "ok"},
        ).as_dict()
