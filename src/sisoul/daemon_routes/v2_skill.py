"""daemon HTTP routes for v2.0 Skill Marketplace (foundation skeleton)."""
from __future__ import annotations
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from sisoul.v2.skill_marketplace import SkillInstaller, SkillManifest

router = APIRouter(prefix="/v2/skill", tags=["v2-skill-marketplace"])


def _installer() -> SkillInstaller:
    skills_dir = Path(os.environ.get("SISOUL_SKILLS_DIR", "~/.sisoul/skills")).expanduser()
    return SkillInstaller(skills_dir)


class SkillInstallRequest(BaseModel):
    name: str
    version: str
    entry: str
    runtime: str
    ipfs_cid: str
    author_did: str
    sigstore_sig: str
    description: str = ""
    sis_price_per_call: float = 0.0
    skip_sigstore: bool = False


@router.post("/install")
def install(req: SkillInstallRequest) -> dict:
    m = SkillManifest(
        name=req.name, version=req.version, entry=req.entry, runtime=req.runtime,
        ipfs_cid=req.ipfs_cid, author_did=req.author_did, sigstore_sig=req.sigstore_sig,
        description=req.description, sis_price_per_call=req.sis_price_per_call,
    )
    r = _installer().install(m, skip_sigstore=req.skip_sigstore)
    if not r.success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=r.error or "install failed")
    return {
        "skill_name": r.skill_name,
        "install_path": r.install_path,
        "sigstore_verified": r.sigstore_verified,
        "hot_loaded": r.hot_loaded,
    }


@router.get("/list")
def list_installed() -> dict:
    skills = _installer().list_installed()
    return {"skills": skills, "count": len(skills)}


@router.delete("/{skill_name}")
def uninstall(skill_name: str) -> dict:
    ok = _installer().uninstall(skill_name)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="skill not found")
    return {"uninstalled": skill_name}
