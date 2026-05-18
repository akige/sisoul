"""sisoul vault 模块 (Phase 1 W3-W4).

§28 §1.1 模块 2: 本地 vault + libsodium 加密.

vault 文件结构 (`~/.sisoul/`):
- dna.json                — 元数据 (sisoul_version / vault_created_at / master_key_hash)
- preferences/<YYYY-MM-DD>.md — 按日累积的偏好 (frontmatter + body)
- goals/<id>.md            — 长期目标 (frontmatter: id / title / created_at / progress)
- chat-history/<date>/<session-id>.md — chat history (Phase 1 W11+, 本波先建空 dir)

子模块:
- storage.py     — 文件 layer (read/write/list with frontmatter)
- encryption.py  — libsodium SecretBox 加密 + master key 派生 (BIP-39 placeholder)
- frontmatter.py — markdown frontmatter 解析 (用 python-frontmatter)
"""

from __future__ import annotations

from sisoul.vault.encryption import (
    decrypt_bytes,
    derive_master_key,
    encrypt_bytes,
)
from sisoul.vault.frontmatter import dump_frontmatter, load_frontmatter
from sisoul.vault.storage import (
    DEFAULT_VAULT_DIR,
    VaultPaths,
    list_files,
    read_file,
    vault_size,
    write_file,
)

__all__ = [
    "DEFAULT_VAULT_DIR",
    "VaultPaths",
    "decrypt_bytes",
    "derive_master_key",
    "dump_frontmatter",
    "encrypt_bytes",
    "list_files",
    "load_frontmatter",
    "read_file",
    "vault_size",
    "write_file",
]
