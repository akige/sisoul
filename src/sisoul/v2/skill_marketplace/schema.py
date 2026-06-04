"""Skill Marketplace schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SkillManifest:
    """A skill package manifest."""

    name: str
    version: str
    entry: str  # main.py / index.js / Cargo.toml
    runtime: str  # "python" | "node" | "rust" | "wasm"
    ipfs_cid: str
    author_did: str
    sigstore_sig: str  # cosign signature
    description: str = ""
    tags: list[str] = field(default_factory=list)
    requires_skills: list[str] = field(default_factory=list)
    sis_price_per_call: float = 0.0
    sha256: str = ""


@dataclass
class SkillInstallResult:
    """Result of installing a skill from IPFS."""

    skill_name: str
    success: bool
    install_path: str  # ~/.sisoul/skills/<name>/
    error: Optional[str] = None
    sigstore_verified: bool = False
    hot_loaded: bool = False
