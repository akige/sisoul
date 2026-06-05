"""sisoul v3 RSI 框架 — 递归自我改进三层模型 (skeleton).

统一入口, expose 5 个核心 class:

- ``GodelAgent``      — L2 prompt 自优化 (Gödel Agent, 2410.04444)
- ``AlphaEvolveLoop`` — L3 code 进化 (AlphaEvolve, DeepMind 2025-05)
- ``DSPyOptimizer``   — declarative self-improving (DSPy MIPROv2 兼容接口)
- ``FederatedRSI``    — L4 跨 daemon 集体演化 (WebEvolver coevolution + FedAvg)
- ``Evaluator``       — pytest gate + bench + safety_check 综合打分
"""

from __future__ import annotations

from sisoul.v3.rsi.evaluator import Evaluator, ScoreReport
from sisoul.v3.rsi.godel_agent import GodelAgent
from sisoul.v3.rsi.alpha_evolve import AlphaEvolveLoop, AllowlistViolation
from sisoul.v3.rsi.dspy_optimize import DSPyOptimizer, OptimizableModule
from sisoul.v3.rsi.federated_rsi import FederatedRSI

__all__ = [
    "GodelAgent",
    "AlphaEvolveLoop",
    "AllowlistViolation",
    "DSPyOptimizer",
    "OptimizableModule",
    "FederatedRSI",
    "Evaluator",
    "ScoreReport",
]
