"""sisoul · onchain 模块包 (Phase 3 W41-W43).

§28 §1.1 模块 12 (Arweave 周期 snapshot) + §29 §5 W41-W43.

子模块:
- arweave.py (dev-C, W41-W43): 月度加密 snapshot → IPFS pin (即时) + Arweave 上链 (异步, 永久)
- eas.py     (dev-B, W41-W43): EAS attestation (vault 完整性 / DID 验证 ondain)

import 风格: 各子模块 append `from sisoul.onchain.<mod> import *` 段, 不互相覆盖.
"""

from __future__ import annotations

# ── arweave (dev-C, W41-W43) ─────────────────────────────────────────────────
from sisoul.onchain.arweave import (  # noqa: F401
    ArweaveSnapshot,
    SnapshotRecord,
    SnapshotHistory,
    schedule_monthly_snapshot,
)

# ── eas (dev-B, W37-W40, 波 4) ───────────────────────────────────────────────
# 完整公开 API 见 eas.__all__. star-import 暴露所有公开符号.
from sisoul.onchain.eas import (  # noqa: F401
    # 常量
    OPTIMISM_SEPOLIA_CHAIN_ID,
    OPTIMISM_SEPOLIA_DEFAULT_RPC,
    EAS_CONTRACT_OPTIMISM_SEPOLIA,
    EAS_SCHEMA_REGISTRY_OPTIMISM_SEPOLIA,
    # P3-5 跨链
    ARBITRUM_SEPOLIA_CHAIN_ID,
    ARBITRUM_SEPOLIA_DEFAULT_RPC,
    EAS_CONTRACT_ARBITRUM_SEPOLIA,
    BASE_SEPOLIA_CHAIN_ID,
    BASE_SEPOLIA_DEFAULT_RPC,
    EAS_CONTRACT_BASE_SEPOLIA,
    ZKSYNC_SEPOLIA_CHAIN_ID,
    ZKSYNC_SEPOLIA_DEFAULT_RPC,
    EAS_CONTRACT_ZKSYNC_SEPOLIA,
    MAINNET_BLOCKED_CHAINS,
    CHAIN_ID_BY_NETWORK,
    SHORT_TO_NETWORK,
    CHAIN_REGISTRY,
    ChainConfig,
    resolve_chain,
    SISOUL_AUDIT_SCHEMA,
    MOCK_SCHEMA_UID,
    DEFAULT_BATCH_SIZE,
    DEFAULT_BATCH_TIMEOUT_SEC,
    DEFAULT_ATTEST_QUEUE_DB,
    DEFAULT_ATTEST_CONFIG,
    # 异常
    EASError,
    NetworkNotSupportedError,
    AttestationNotFoundError,
    QueueEmptyError,
    ConfigError,
    # 数据 + queue
    AuditAttestation,
    BatchResult,
    AttestConfig,
    AttestQueue,
    # config
    load_config,
    save_config,
    # batch / verify / history
    upload_batch,
    encode_attestation_data,
    compute_attestation_uid,
    verify_attestation_local,
    verify_attestation_onchain,
    list_history_local,
    list_history_onchain,
    resolve_attester_did,
)
