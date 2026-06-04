"""Skill publisher — IPFS publish + sigstore sign (v2.0 ship T+11m)."""
from __future__ import annotations
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .schema import SkillManifest


class SkillPublisher:
    """Skeleton publisher.

    Full impl: tar + IPFS add + cosign sign-blob + sigstore Rekor log.
    """

    def __init__(self, author_did: str):
        if not author_did.startswith("did:key:"):
            raise ValueError(f"invalid author_did: {author_did}")
        self.author_did = author_did

    def _hash_skill_dir(self, skill_dir: Path) -> str:
        """Compute deterministic sha256 of all files in skill dir."""
        h = hashlib.sha256()
        for f in sorted(skill_dir.rglob("*")):
            if f.is_file():
                h.update(f.relative_to(skill_dir).as_posix().encode())
                h.update(b":")
                h.update(f.read_bytes())
                h.update(b"\n")
        return h.hexdigest()

    def package(self, skill_dir: Path, manifest_dict: dict) -> SkillManifest:
        """Foundation: validate dir + compute hash, return manifest.

        Full impl: tar + sigstore sign + IPFS pin.
        """
        skill_dir = Path(skill_dir).expanduser()
        if not skill_dir.exists():
            raise ValueError(f"skill dir not found: {skill_dir}")
        if not (skill_dir / "manifest.json").exists() and "name" not in manifest_dict:
            raise ValueError("missing manifest.json + no name in manifest_dict")
        sha = self._hash_skill_dir(skill_dir)
        # mock IPFS CID derived from sha (full impl: real `ipfs add`)
        cid = f"bafy{sha[:50]}"
        return SkillManifest(
            name=manifest_dict.get("name", skill_dir.name),
            version=manifest_dict.get("version", "0.1.0"),
            entry=manifest_dict.get("entry", "main.py"),
            runtime=manifest_dict.get("runtime", "python"),
            ipfs_cid=cid,
            author_did=self.author_did,
            sigstore_sig=f"mock-sig-{sha[:16]}",
            description=manifest_dict.get("description", ""),
            tags=manifest_dict.get("tags", []),
            sis_price_per_call=manifest_dict.get("sis_price_per_call", 0.0),
            sha256=sha,
        )

    def verify(self, manifest: SkillManifest, skill_dir: Path) -> bool:
        """Verify the skill dir matches manifest sha256."""
        actual_sha = self._hash_skill_dir(Path(skill_dir).expanduser())
        return actual_sha == manifest.sha256


__all__ = ["SkillPublisher"]
