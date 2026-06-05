"""sisoul v3.0 超级智能体 — RSI (Recursive Self-Improvement) 框架.

L2-L4 三层递归自我改进模型 (skeleton, 接口对真 LLM 留好, 但不真调 LLM):

- **L2 prompt 自优化** — ``GodelAgent``: self-referential prompt 变异 (Gödel Agent, 2410.04444)
- **L3 code 进化**     — ``AlphaEvolveLoop``: evolutionary codegen + 自动 evaluator (AlphaEvolve, DeepMind 2025-05)
- **L4 集体演化**       — ``FederatedRSI``: 跨 daemon gossip 变异 + LoRA federated averaging
- 横切                  — ``DSPyOptimizer`` (declarative self-improving, DSPy MIPROv2 接口兼容)
                          ``Evaluator`` (pytest gate + bench + safety_check 综合打分)

设计红线 (见各模块 docstring):
- 禁改 ``src/sisoul/v3/rsi/`` 自己 (避免 Gödel 自指失控)
- AlphaEvolve target_module 必须在 allowlist
- 任何变异落地前过 ``Evaluator.safety_check`` + pytest gate
"""

from __future__ import annotations

__version__ = "0.1.0-alpha-skeleton"
