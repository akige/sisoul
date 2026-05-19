"""sisoul · onchain 模块包 (Phase 3 W41-W43; v1.0-decentralized Wave A #5).

§28 §1.1 模块 12 (Arweave 周期 snapshot) + §29 §5 W41-W43.
v1.0-decentralized Wave A agent-2: #5 替 Pinata 长期存为 Bundlr/Turbo 直传 Arweave.

子模块:
- arweave.py     (dev-C, W41-W43): 月度加密 snapshot → IPFS pin (即时, #6 helia hot 路径)
                  + Arweave 上链 (Bundlr/Turbo, 永久)
- bundlr_turbo.py (v1.0-decentralized Wave A): ArweaveUploader (Turbo / Irys / arweave-direct
                   / mock) + Quote / UploadReceipt / FundReceipt / BalanceInfo
- eas.py         (dev-B, W41-W43): EAS attestation (vault 完整性 / DID 验证 onchain)
"""

from __future__ import annotations

# ── arweave (dev-C, W41-W43; v1.0-decentralized Wave A #5 改 Bundlr/Turbo) ──
from sisoul.onchain.arweave import (  # noqa: F401
    ArweaveSnapshot,
    SnapshotRecord,
    SnapshotHistory,
    schedule_monthly_snapshot,
)
from sisoul.onchain.bundlr_turbo import (  # noqa: F401
    ArweaveUploader,
    BundlrError,
    ArweaveInsufficientFunds,
    ArweaveUploadTimeout,
    ArweaveTxNotFound,
    ArweaveMainnetGateError,
    Quote,
    UploadReceipt,
    FundReceipt,
    BalanceInfo,
    FREE_TIER_BYTES,
    TURBO_UPLOAD_BASE,
    TURBO_PAYMENT_BASE,
)

# ── eas (dev-B, W37-W40, 波 4) ───────────────────────────────────────────────
from sisoul.onchain.eas import (  # noqa: F401
    OPTIMISM_SEPOLIA_CHAIN_ID,
    OPTIMISM_SEPOLIA_DEFAULT_RPC,
    EAS_CONTRACT_OPTIMISM_SEPOLIA,
    EAS_SCHEMA_REGISTRY_OPTIMISM_SEPOLIA,
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
    EASError,
    NetworkNotSupportedError,
    AttestationNotFoundError,
    QueueEmptyError,
    ConfigError,
    AuditAttestation,
    BatchResult,
    AttestConfig,
    AttestQueue,
    load_config,
    save_config,
    upload_batch,
    encode_attestation_data,
    compute_attestation_uid,
    verify_attestation_local,
    verify_attestation_onchain,
    list_history_local,
    list_history_onchain,
    resolve_attester_did,
)
