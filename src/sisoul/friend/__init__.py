"""sisoul friend 模块 (Phase 4 W51-W74 · 波 5).

§28 §3 P2P 朋友资源共享 完整设计:
- §3.1 朋友关系层 (DID + 双向 EAS attestation + 强连接评分)   — 本文件 relationship.py (dev-A 波 5)
- §3.2 加密 proxy 机制                                           — encrypted_proxy.py (dev-B 波 5)
- §3.3 3 档授权模式 + 滥用防御                                   — permissions.py + anti_abuse.py (dev-C 波 5)
- §3.4 互惠 ledger + LLM quota share / lend / borrow             — ledger.py + lend.py + borrow.py (dev-D 波 5)

本 ``__init__.py`` 由 dev-A 波 5 ship 框架, 其他 dev 后续 append (各自 import 自己模块).
为防 import 循环 / 顺序敏感, 这里只 export "总是存在" 的 §3.1 类型 (Friend / FriendRelationship /
FriendStatus / RelationshipType / 强连接评分函数). 其他 dev 各自模块独立 import, 不强依赖本 __init__.

模块边界 (波 5 严格约束):
- dev-A (本文件 + relationship.py): 朋友关系 + 双向 EAS attestation + 强连接评分 + sisoul friend 命令组
- dev-B: encrypted_proxy.py (加密 proxy + libsodium box, 复用 p2p.encryption)
- dev-C: permissions.py (3 档授权) + anti_abuse.py (滥用防御)
- dev-D: lend.py + borrow.py + ledger.py (LLM quota share + 互惠 ledger + 总 router 整合)
"""

from __future__ import annotations

# ── dev-A (relationship.py) ─────────────────────────────────────────────────
# 用 try/except 包: dev-A 与 dev-B 波 5 并行 ship, dev-A 未 ship 时不应 break dev-B.
# dev-A ship 后两边都 work; 见波 5 任务书"用 try/except 兼容 race".
_DEV_A_NAMES: list[str] = []
try:
    from sisoul.friend.relationship import (  # noqa: F401
        DEFAULT_FRIEND_DB,
        FRIEND_RELATIONSHIP_SCHEMA,
        FRIEND_RELATIONSHIP_SCHEMA_UID,
        Friend,
        FriendDB,
        FriendError,
        FriendNotFoundError,
        FriendRelationship,
        FriendRequest,
        FriendRequestError,
        FriendRequestNotFoundError,
        FriendStatus,
        RelationshipType,
        StrongTieScore,
        compute_strong_tie_score,
        encode_friend_attestation_data,
        enqueue_friend_attestation,
        record_interaction,
        verify_mutual_attestation,
    )

    _DEV_A_NAMES = [
        "Friend",
        "FriendDB",
        "FriendRelationship",
        "FriendRequest",
        "FriendStatus",
        "RelationshipType",
        "StrongTieScore",
        "FriendError",
        "FriendNotFoundError",
        "FriendRequestError",
        "FriendRequestNotFoundError",
        "FRIEND_RELATIONSHIP_SCHEMA",
        "FRIEND_RELATIONSHIP_SCHEMA_UID",
        "encode_friend_attestation_data",
        "enqueue_friend_attestation",
        "compute_strong_tie_score",
        "record_interaction",
        "verify_mutual_attestation",
        "DEFAULT_FRIEND_DB",
    ]
except Exception:  # noqa: BLE001 (dev-A 未 ship 期间 fallback)
    pass


# ── dev-B (encrypted_proxy.py) ──────────────────────────────────────────────
_DEV_B_NAMES: list[str] = []
try:
    from sisoul.friend.encrypted_proxy import (  # noqa: F401
        EncryptedProxy,
        ProxyError,
        ProxyPermissionError,
        ProxySession,
        ProxySessionMetadata,
        derive_friend_session_keypair,
    )

    _DEV_B_NAMES = [
        "EncryptedProxy",
        "ProxyError",
        "ProxyPermissionError",
        "ProxySession",
        "ProxySessionMetadata",
        "derive_friend_session_keypair",
    ]
except Exception:  # noqa: BLE001
    pass


__all__ = _DEV_A_NAMES + _DEV_B_NAMES
