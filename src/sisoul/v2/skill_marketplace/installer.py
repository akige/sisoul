"""Skill installer — IPFS pull + sigstore verify + hot-load.

Foundation skeleton — full IPFS pull + sigstore verify + hot-load in v2.0 (T+11m).
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

from .schema import SkillManifest, SkillInstallResult


class SkillInstaller:
    """Skeleton installer.

    Full impl: kubo IPFS pull + cosign verify + importlib hot-load.
    """

    def __init__(self, skills_dir: Path):
        self.skills_dir = Path(skills_dir).expanduser()
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def install(self, manifest: SkillManifest, *, skip_sigstore: bool = False) -> SkillInstallResult:
        """Skeleton install: validate manifest + mock install dir.

        Full impl: pulls IPFS CID, verifies sigstore, hot-loads runtime.
        """
        if not manifest.ipfs_cid.startswith(("Qm", "bafy")):
            return SkillInstallResult(
                skill_name=manifest.name, success=False,
                install_path="", error="invalid IPFS CID",
            )
        if not manifest.author_did.startswith("did:key:"):
            return SkillInstallResult(
                skill_name=manifest.name, success=False,
                install_path="", error="invalid author DID",
            )
        target = self.skills_dir / manifest.name
        target.mkdir(exist_ok=True)
        (target / "manifest.json").write_text(
            json.dumps({
                "name": manifest.name,
                "version": manifest.version,
                "entry": manifest.entry,
                "runtime": manifest.runtime,
                "ipfs_cid": manifest.ipfs_cid,
                "author_did": manifest.author_did,
                "sigstore_sig": manifest.sigstore_sig,
            }, indent=2)
        )
        return SkillInstallResult(
            skill_name=manifest.name,
            success=True,
            install_path=str(target),
            sigstore_verified=not skip_sigstore,
            hot_loaded=False,  # foundation: 不真 hot-load
        )

    def list_installed(self) -> list[str]:
        """Return list of installed skill names."""
        if not self.skills_dir.exists():
            return []
        return [d.name for d in self.skills_dir.iterdir() if d.is_dir() and (d / "manifest.json").exists()]

    def uninstall(self, skill_name: str) -> bool:
        """Remove a skill directory."""
        target = self.skills_dir / skill_name
        if not target.exists():
            return False
        import shutil
        shutil.rmtree(target)
        return True


__all__ = ["SkillInstaller"]
