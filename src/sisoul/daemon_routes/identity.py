"""sisoul daemon · identity (BIP-39 seed) HTTP API (Phase 2 W17-W20, 波 3 dev-A).

Endpoints (§28 §2.1 ``/sisoul/identity*`` + ``/sisoul/restore-seed``):
- GET  /sisoul/identity         返当前身份摘要 (master_key_fingerprint + has_seed + seed_path)
- POST /sisoul/restore-seed     从 BIP-39 mnemonic 触发 vault restore

由主 ``daemon.py`` 通过 ``app.include_router(identity_router)`` 整合 (波 3 主集成做).
本 router 不动 daemon.py 主文件.

设计:
- GET /sisoul/identity 是只读 + safe. 暴露 master_key_fingerprint (sha256 前 16 hex) 不暴露原 key/seed.
- POST /sisoul/restore-seed 是 destructive (建/覆盖 vault), force=False 默认.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from sisoul.cli_commands.restore import RestoreError, run_restore_from_seed
from sisoul.identity import (
    DEFAULT_SEED_FILE,
    InvalidMnemonicError,
    derive_subkey,
    load_mnemonic_from_file,
    mnemonic_to_master_key,
)
from sisoul.vault import DEFAULT_VAULT_DIR

identity_router = APIRouter(prefix="/sisoul", tags=["identity"])

# vault subkey purpose (跟 vault.encryption._VAULT_PURPOSE 一致)
_VAULT_PURPOSE = "vault"


# ── schemas ─────────────────────────────────────────────────────────────────


class IdentityResponse(BaseModel):
    """GET /sisoul/identity 响应."""

    has_seed: bool = Field(..., description="seed 文件是否存在")
    seed_path: Optional[str] = Field(None, description="seed 文件实际路径 (若存在)")
    master_key_fingerprint: Optional[str] = Field(
        None,
        description="sha256(vault_master_key) 前 16 hex; 无 seed 时 null",
    )
    seed_word_count: Optional[int] = Field(
        None, description="seed 词数 (12/15/18/21/24); 无 seed 时 null"
    )
    error: Optional[str] = Field(None, description="加载 seed 出错时的 err msg")


class RestoreSeedRequest(BaseModel):
    """POST /sisoul/restore-seed 请求."""

    seed: str = Field(..., description="BIP-39 12-24 词 mnemonic (空格分隔)")
    vault_dir: Optional[str] = Field(None, description="目标 vault 路径; 默认 ~/.sisoul/")
    force: bool = Field(False, description="vault 已存在时是否覆盖")


class RestoreSeedResponse(BaseModel):
    """POST /sisoul/restore-seed 响应."""

    ok: bool
    vault_dir: str
    master_key_fingerprint: str
    seed_path: str
    message: str


# ── helpers ─────────────────────────────────────────────────────────────────


def _resolve_seed_path(vault_dir: Optional[str]) -> Path:
    """vault_dir/seed.txt 优先; 否则 DEFAULT_SEED_FILE (~/.sisoul/seed.txt)."""
    if vault_dir:
        return Path(vault_dir).expanduser() / "seed.txt"
    return DEFAULT_SEED_FILE


def _fingerprint(master_key: bytes) -> str:
    return hashlib.sha256(master_key).hexdigest()[:16]


# ── routes ──────────────────────────────────────────────────────────────────


@identity_router.get("/identity", response_model=IdentityResponse)
def get_identity(
    vault_dir: Optional[str] = Query(
        None, description="vault 路径, 默认 ~/.sisoul/ (查 vault/seed.txt)"
    ),
) -> IdentityResponse:
    """返当前 sisoul 身份状态.

    - seed 文件存在 + 合法 → has_seed=True + fingerprint + word_count
    - seed 不存在 → has_seed=False (没 error)
    - seed 存在但非法 (corrupted / 权限错) → has_seed=False + error 描述
    """
    seed_path = _resolve_seed_path(vault_dir)

    if not seed_path.exists():
        return IdentityResponse(has_seed=False, seed_path=None)

    try:
        mnemonic = load_mnemonic_from_file(seed_path)
    except (PermissionError, ValueError) as e:
        return IdentityResponse(
            has_seed=False, seed_path=str(seed_path), error=str(e)
        )

    master_seed = mnemonic_to_master_key(mnemonic)
    vault_key = derive_subkey(master_seed, _VAULT_PURPOSE, index=0)
    return IdentityResponse(
        has_seed=True,
        seed_path=str(seed_path),
        master_key_fingerprint=_fingerprint(vault_key),
        seed_word_count=len(mnemonic.split()),
    )


@identity_router.post(
    "/restore-seed", response_model=RestoreSeedResponse, status_code=201
)
def post_restore_seed(body: RestoreSeedRequest) -> RestoreSeedResponse:
    """从 BIP-39 mnemonic 恢复 / 建 vault.

    错误码:
    - 400: mnemonic 不合法 (checksum / 词表错)
    - 409: vault 已存在且 force=False
    - 500: 其他 RestoreError / IO
    """
    vault_dir: Path = (
        Path(body.vault_dir).expanduser() if body.vault_dir else DEFAULT_VAULT_DIR
    )
    try:
        paths = run_restore_from_seed(
            seed=body.seed,
            from_seed_file=None,
            vault_dir=vault_dir,
            force=body.force,
        )
    except InvalidMnemonicError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except SystemExit:
        # run_restore_from_seed 在 vault 已存在 + no force 时 raise SystemExit(1)
        raise HTTPException(
            status_code=409,
            detail=f"vault 已存在: {vault_dir} (POST 时设 force=true 覆盖)",
        )
    except RestoreError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # 派生 fingerprint 给响应
    master_seed = mnemonic_to_master_key(body.seed.strip())
    vault_key = derive_subkey(master_seed, _VAULT_PURPOSE, index=0)
    return RestoreSeedResponse(
        ok=True,
        vault_dir=str(paths.root),
        master_key_fingerprint=_fingerprint(vault_key),
        seed_path=str(paths.root / "seed.txt"),
        message="restored from BIP-39 seed; 重新 sisoul login 重接 LLM key",
    )


__all__ = ["identity_router"]
