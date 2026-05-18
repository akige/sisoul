"""sisoul p2p 模块 (Phase 3 W31-W36 · 波 4 dev-A).

§28 §1.1 模块 9 (跨设备 libp2p P2P sync + WebRTC fallback + 加密 vault 传输).

子模块:
- ``encryption``  — BIP-39 派生 P2P key + SecretBox 加密
- ``transport``   — libp2p (优先) / WebRTC aiortc (fallback) / InMemory (兜底测试)
- ``discovery``   — DHT / mDNS / manual peer list 三路组合
- ``sync``        — vault inventory + diff + 冲突 newer-wins / 人工
- ``node``        — SisoulP2PNode 整合, 全局单例

顶层便利 API:
- ``start_node(vault_dir, port)``
- ``stop_node()``
- ``sync_with_peer(peer_id)``
- ``list_peers()``

设计决策 (2026-05 实测):
- ``py-libp2p`` (PyPI 名 ``libp2p``) 当前版本对 protobuf 6.x gencode 跟 5.x runtime 不兼容,
  import 即 raise. ``select_transport()`` 自动 fallback 到 aiortc WebRTC.
- WebRTC 也未真做 NAT 穿透 (Phase 4 加 STUN/TURN), Phase 3 同机走 daemon-mediated
  in-memory bus, 跑通 sync 协议.
"""

from __future__ import annotations

from sisoul.p2p.node import (
    NodeStatus,
    SisoulP2PNode,
    SyncStats,
    get_node,
    list_peers,
    set_node,
    start_node,
    stop_node,
    sync_with_peer,
)
from sisoul.p2p.transport import (
    AIORTC_AVAILABLE,
    LIBP2P_AVAILABLE,
    PeerInfo,
)

__all__ = [
    "AIORTC_AVAILABLE",
    "LIBP2P_AVAILABLE",
    "NodeStatus",
    "PeerInfo",
    "SisoulP2PNode",
    "SyncStats",
    "get_node",
    "list_peers",
    "set_node",
    "start_node",
    "stop_node",
    "sync_with_peer",
]
