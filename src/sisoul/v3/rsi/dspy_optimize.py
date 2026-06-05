"""sisoul v3 RSI · DSPyOptimizer — declarative self-improving (DSPy 接口兼容).

参考 DSPy (Stanford): 声明式自我改进, 自动优化 module 的 prompt + few-shot demo.
本 skeleton **不真装 DSPy** (依赖重), 只提供 protocol-compatible 接口, 模拟
``dspy.MIPROv2`` 的 ``compile`` / bootstrap demo 行为.

核心抽象:
- ``OptimizableModule`` (Protocol): 有 ``prompt`` 属性 + ``__call__`` 的 module.
- ``DSPyOptimizer.compile(module)`` → 返回优化后的 module (prompt 注入了 best demos).
- ``bootstrap_demos(n)`` → 从 train_examples 里按 metric_fn 选 top-n 当 few-shot demo.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class OptimizableModule(Protocol):
    """可被 DSPyOptimizer 优化的 module 协议.

    需有可读写的 ``prompt: str`` 与 ``demos: list`` 属性, 以及 ``__call__``.
    """

    prompt: str
    demos: list

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


class DSPyOptimizer:
    """模拟 ``dspy.MIPROv2`` 的声明式优化器 (skeleton)."""

    def __init__(
        self,
        metric_fn: Callable[[Any, Any], float],
        train_examples: list,
    ) -> None:
        """初始化优化器.

        Args:
            metric_fn: 评分函数 ``(example, prediction) -> float`` (越大越好).
            train_examples: 训练样本列表; 每个样本是 ``(input, gold)`` 或 dict.
        """
        self.metric_fn = metric_fn
        self.train_examples = list(train_examples)
        self._compiled_demos: list = []

    # ── bootstrap demos ───────────────────────────────────────────────────────
    def bootstrap_demos(self, n: int = 5) -> list:
        """从 train_examples 里自动找 top-n 好 demo.

        skeleton 评分策略: 对每个样本用 metric_fn 自评 (example, gold-as-prediction),
        取分最高的 n 个. 真实现里会跑 module 前向再评分.

        Args:
            n: demo 数量.

        Returns:
            top-n demo 列表 (按 metric 降序).
        """
        scored = []
        for ex in self.train_examples:
            gold = self._gold_of(ex)
            try:
                s = float(self.metric_fn(ex, gold))
            except Exception:  # noqa: BLE001 — 评分失败的样本排末位
                s = float("-inf")
            scored.append((s, ex))
        scored.sort(key=lambda t: t[0], reverse=True)
        self._compiled_demos = [ex for _, ex in scored[:n]]
        return self._compiled_demos

    @staticmethod
    def _gold_of(example: Any) -> Any:
        """从样本里取 gold/label (支持 (input, gold) tuple 或 dict)."""
        if isinstance(example, dict):
            return example.get("gold", example.get("label", example))
        if isinstance(example, (tuple, list)) and len(example) >= 2:
            return example[1]
        return example

    # ── compile ───────────────────────────────────────────────────────────────
    def compile(self, module: Any, n_demos: int = 5) -> Any:
        """优化 module 的 prompt + few-shot demos, 返回优化后副本.

        不修改入参 module (返回 deepcopy), 模拟 DSPy ``compile`` 不可变语义.

        Args:
            module: 满足 ``OptimizableModule`` 协议的对象.
            n_demos: 注入的 demo 数.

        Returns:
            优化后的 module 副本 (``demos`` 填了 bootstrap 结果, ``prompt`` 追加 demo 提示).

        Raises:
            TypeError: module 不满足协议 (缺 prompt / demos 属性).
        """
        if not hasattr(module, "prompt") or not hasattr(module, "demos"):
            raise TypeError(
                "compile() 需要满足 OptimizableModule 协议的 module (需有 prompt + demos 属性)"
            )
        demos = self.bootstrap_demos(n_demos)
        optimized = copy.deepcopy(module)
        optimized.demos = list(demos)
        optimized.prompt = (
            f"{module.prompt}\n\n"
            f"[DSPyOptimizer: bootstrapped {len(demos)} few-shot demos]"
        )
        return optimized

    @property
    def compiled_demos(self) -> list:
        """最近一次 bootstrap 选出的 demos."""
        return list(self._compiled_demos)
