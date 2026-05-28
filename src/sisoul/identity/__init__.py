"""sisoul identity 模块 (Phase 2 W17-W20 · 波 3 dev-A).

§28 §1.1 模块 8 (BIP-39 灵魂迁移核心) · §29 §4.1 W17-W20.

子模块:
- seed.py: BIP-39 12 词 mnemonic 生成 / 校验 / 派生 master key + 子 key
- did.py:  DID 系统 (波 3 dev-B 实现, 本模块不动)

核心 API:
- generate_mnemonic(strength=128) → 12 词 BIP-39 mnemonic
- verify_mnemonic(mnemonic)       → checksum 校验 bool
- mnemonic_to_master_key(mnemonic, passphrase="")
                                   → 64B master seed (BIP-39 PBKDF2-HMAC-SHA512)
- derive_subkey(master, purpose, index=0)
                                   → 32B 子 key (用于 vault / did / skill 隔离)
- save_mnemonic_to_file(mnemonic, path)
- load_mnemonic_from_file(path)    → 严格 chmod 600 + 12 词校验

设计原则:
- master_seed 是 PBKDF2 64B (标准 BIP-39 seed). 各业务用 derive_subkey 派生 32B 子 key.
- subkey 派生用 HMAC-SHA256(master, purpose_bytes || index) → 32B (BIP-32-inspired 但简化, 不做 chain code).
- 子 key 之间相互独立 (vault key 泄露 ≠ did key 泄露), 同时保证 seed 一致 ⇒ 子 key 一致 (跨设备 restore).
"""

from __future__ import annotations

from sisoul.identity.seed import (
    DEFAULT_SEED_FILE,
    SUBKEY_SIZE,
    InvalidMnemonicError,
    derive_subkey,
    generate_mnemonic,
    load_mnemonic_from_file,
    mnemonic_to_master_key,
    save_mnemonic_to_file,
    verify_mnemonic,
)

# Wave B' P0-3: did:key 轻量化 (W3C-CCG did:key method).
from sisoul.identity.did_key import (
    DID_KEY_SCHEME,
    DidKey,
    DidKeyError,
    InvalidDidKeyFormatError,
    UnsupportedMulticodecError,
    decode_did_key,
    derive_did_key_keypair,
    did_key_to_pubkey,
    encode_did_key,
    generate_did_key,
    generate_did_key_from_master,
    verify_did_key,
)

__all__ = [
    "DEFAULT_SEED_FILE",
    "InvalidMnemonicError",
    "SUBKEY_SIZE",
    "derive_subkey",
    "generate_mnemonic",
    "load_mnemonic_from_file",
    "mnemonic_to_master_key",
    "save_mnemonic_to_file",
    "verify_mnemonic",
    "DID_KEY_SCHEME",
    "DidKey",
    "DidKeyError",
    "InvalidDidKeyFormatError",
    "UnsupportedMulticodecError",
    "decode_did_key",
    "derive_did_key_keypair",
    "did_key_to_pubkey",
    "encode_did_key",
    "generate_did_key",
    "generate_did_key_from_master",
    "verify_did_key",
]
