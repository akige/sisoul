"""sisoul v3 RSI · FederatedRSI — L4 跨 daemon 集体演化 (真涌现层).

参考 WebEvolver (arxiv 2504.21024) 的 coevolution 思路 + federated learning:
多个 sisoul daemon 各自跑 L2/L3 RSI, 通过 P2P gossip 广播自己的"变异成果",
互相 merge → 集体演化速度 > 单 daemon. 这是 sisoul L4 "真涌现" 层.

本 skeleton **不真启 GossipSub / libp2p**, 走注入的 transport (默认 in-memory),
接口与 ``sisoul.p2p.push`` 的 transport 约定对齐 (async send / subscribe_topic).

核心能力:
- ``gossip_mutation(mutation)``     — 把本地 RSI 变异广播给 peer.
- ``subscribe_peer_mutations(cb)``  — 订阅 peer 的变异.
- ``merge_lora_weights(peers)``     — federated averaging (FedAvg) 合并 LoRA 增量.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional


# RSI 变异广播用的 gossip topic.
RSI_MUTATION_TOPIC = "/sisoul/rsi/mutation/v1"


class FederatedRSI:
    """L4 跨 daemon 联邦 RSI 协同器 (skeleton)."""

    def __init__(
        self,
        self_did: str,
        transport: Optional[Any] = None,
        topic: str = RSI_MUTATION_TOPIC,
    ) -> None:
        """初始化联邦 RSI.

        Args:
            self_did: 本 daemon 的 DID (广播 mutation 时标 origin).
            transport: ``sisoul.p2p.push`` 的 transport 兼容对象 (async send /
                       subscribe_topic / query_store); None → 集成时注入.
            topic: gossip topic.
        """
        self.self_did = self_did
        self.transport = transport
        self.topic = topic
        self.received_mutations: list[dict] = []

    # ── gossip out ────────────────────────────────────────────────────────────
    async def gossip_mutation(self, mutation: dict) -> dict:
        """把一个本地 RSI 变异广播给 peer.

        Args:
            mutation: 变异 payload (e.g. {"kind": "prompt", "code": ..., "fitness": ...}).

        Returns:
            实际广播的 envelope (附 origin / topic).

        Raises:
            RuntimeError: transport 未注入.
        """
        if self.transport is None:
            raise RuntimeError("FederatedRSI.gossip_mutation 需要 transport (在 __init__ 注入)")
        envelope = {"origin": self.self_did, "topic": self.topic, "mutation": mutation}
        await self.transport.send(self.topic, envelope)
        return envelope

    # ── gossip in ────────────────────────────────────────────────────────────
    async def subscribe_peer_mutations(
        self,
        callback: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> None:
        """订阅 peer 广播的 RSI 变异.

        收到非自己 origin 的变异 → 存入 ``received_mutations``, 并 (可选) 调 callback.

        Args:
            callback: 额外回调 ``(mutation_dict) -> Awaitable``; None → 只入队.

        Raises:
            RuntimeError: transport 未注入.
        """
        if self.transport is None:
            raise RuntimeError("FederatedRSI.subscribe_peer_mutations 需要 transport")

        async def _on_msg(msg: Any) -> None:
            payload = getattr(msg, "payload", None)
            if payload is None and isinstance(msg, dict):
                payload = msg
            if not isinstance(payload, dict):
                return
            if payload.get("origin") == self.self_did:
                return  # 不收自己的回声
            mutation = payload.get("mutation", payload)
            self.received_mutations.append(mutation)
            if callback is not None:
                await callback(mutation)

        await self.transport.subscribe_topic(self.topic, _on_msg)

    # ── federated averaging ────────────────────────────────────────────────────
    @staticmethod
    def merge_lora_weights(
        peer_weights: list[dict],
        self_weights: Optional[dict] = None,
    ) -> dict:
        """FedAvg: 对齐 key 做加权平均合并 LoRA 增量.

        Args:
            peer_weights: peer 的 LoRA 权重 dict 列表 (每个 dict: layer_name -> float|list[float]).
            self_weights: 本地权重 (一并参与平均); None → 只平均 peers.

        Returns:
            合并后的权重 dict (按所有 key 的并集, 缺失视为 0).

        Raises:
            ValueError: 没有任何权重可合并.
        """
        all_weights = list(peer_weights)
        if self_weights is not None:
            all_weights.append(self_weights)
        all_weights = [w for w in all_weights if isinstance(w, dict)]
        if not all_weights:
            raise ValueError("merge_lora_weights: 没有可合并的权重")

        keys: set[str] = set()
        for w in all_weights:
            keys.update(w.keys())

        n = len(all_weights)
        merged: dict[str, Any] = {}
        for k in keys:
            vals = [w.get(k, 0.0) for w in all_weights]
            merged[k] = FederatedRSI._avg(vals, n)
        return merged

    @staticmethod
    def _avg(vals: list, n: int) -> Any:
        """对一组值求平均. 支持标量与等长向量 (list[float])."""
        if vals and isinstance(vals[0], (list, tuple)):
            length = len(vals[0])
            normed = [v if isinstance(v, (list, tuple)) and len(v) == length else [0.0] * length for v in vals]
            return [sum(col) / n for col in zip(*normed)]
        numeric = [float(v) for v in vals if isinstance(v, (int, float))]
        # 把非数值视作 0 仍计入分母 n (FedAvg 语义: 缺失=0)
        return sum(numeric) / n

    # ── convenience sync wrapper (skeleton 自测/CLI 用) ────────────────────────
    def gossip_mutation_sync(self, mutation: dict) -> dict:
        """``gossip_mutation`` 的同步封装 (无事件循环时用)."""
        return asyncio.run(self.gossip_mutation(mutation))
