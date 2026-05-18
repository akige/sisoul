"""sisoul daemon · DID HTTP API (Phase 2 W21-W22, dev-B).

Endpoints (§28 §2.1 ``/sisoul/did*``):
- GET  /sisoul/did               当前默认 DID + metadata (registry 第一条)
- POST /sisoul/did/register      新建 DID + ENS subdomain
- POST /sisoul/did/resolve       查 DID 文档
- GET  /sisoul/did/list          本地 DID 列表

由主 ``daemon.py`` 通过 ``app.include_router(did_router)`` 整合 (波 3 主集成做).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from sisoul.identity.did import (
    DIDError,
    DIDNotFoundError,
    HandleAlreadyTakenError,
    InvalidHandleError,
    NetworkNotSupportedError,
    list_local_dids,
    register_did,
    resolve_did,
)

did_router = APIRouter(prefix="/sisoul/did", tags=["did"])


def _registry_path(vault_dir: Optional[str]) -> Optional[Path]:
    if vault_dir is None:
        return None
    return Path(vault_dir) / "identity" / "dids.json"


# ── schemas ─────────────────────────────────────────────────────────────────


class DIDSummary(BaseModel):
    handle: str
    did: str
    ens: str
    network: str
    public_key: str
    created_at: str
    social_provider: Optional[str] = None


class CurrentDIDResponse(BaseModel):
    has_did: bool
    default: Optional[DIDSummary] = None
    count: int = 0


class RegisterRequest(BaseModel):
    handle: str = Field(..., description="handle, 3-63 字符 a-z0-9-")
    network: str = Field("sepolia", description="sepolia / mainnet / mock")
    live: bool = Field(False, description="真连 Sepolia RPC readonly smoke")
    vault_dir: Optional[str] = None
    rpc_url: Optional[str] = None
    social_provider: Optional[str] = None
    social_id: Optional[str] = None


class RegisterResponse(BaseModel):
    did: str
    ens: str
    network: str
    public_key: str
    ens_tx_hash: Optional[str] = None
    created_at: str


class ResolveRequest(BaseModel):
    target: str = Field(..., description="did:sisoul:<handle> 或 <handle>.sisoul.eth")
    vault_dir: Optional[str] = None
    include_document: bool = False


class ResolveResponse(BaseModel):
    did: str
    ens: str
    network: str
    public_key: str
    controllers: list[str]
    created_at: str
    document: Optional[dict[str, Any]] = None


class ListResponse(BaseModel):
    count: int
    items: list[DIDSummary]


# ── helpers ─────────────────────────────────────────────────────────────────


def _summary(did_obj: Any) -> DIDSummary:
    return DIDSummary(
        handle=did_obj.handle,
        did=did_obj.did_string,
        ens=did_obj.ens_subdomain,
        network=did_obj.network,
        public_key=did_obj.public_key,
        created_at=did_obj.created_at,
        social_provider=did_obj.social_provider,
    )


# ── routes ──────────────────────────────────────────────────────────────────


@did_router.get("", response_model=CurrentDIDResponse)
def get_current_did(
    vault_dir: Optional[str] = Query(None, description="vault 路径, 默认 ~/.sisoul/"),
) -> CurrentDIDResponse:
    """返当前默认 DID + metadata. registry 空时 has_did=False."""
    dids = list_local_dids(registry_path=_registry_path(vault_dir))
    if not dids:
        return CurrentDIDResponse(has_did=False, default=None, count=0)
    return CurrentDIDResponse(has_did=True, default=_summary(dids[0]), count=len(dids))


@did_router.post("/register", response_model=RegisterResponse, status_code=201)
def post_register(body: RegisterRequest) -> RegisterResponse:
    """新建 DID + ENS subdomain (默认 sepolia testnet mock)."""
    try:
        did_obj = register_did(
            body.handle,
            network=body.network,  # type: ignore[arg-type]
            registry_path=_registry_path(body.vault_dir),
            rpc_url=body.rpc_url,
            live=body.live,
            social_provider=body.social_provider,  # type: ignore[arg-type]
            social_id=body.social_id,
        )
    except (InvalidHandleError, HandleAlreadyTakenError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NetworkNotSupportedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except DIDError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return RegisterResponse(
        did=did_obj.did_string,
        ens=did_obj.ens_subdomain,
        network=did_obj.network,
        public_key=did_obj.public_key,
        ens_tx_hash=did_obj.ens_tx_hash,
        created_at=did_obj.created_at,
    )


@did_router.post("/resolve", response_model=ResolveResponse)
def post_resolve(body: ResolveRequest) -> ResolveResponse:
    """查 DID 文档. 本地 miss → 404."""
    try:
        did_obj = resolve_did(body.target, registry_path=_registry_path(body.vault_dir))
    except DIDNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except DIDError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ResolveResponse(
        did=did_obj.did_string,
        ens=did_obj.ens_subdomain,
        network=did_obj.network,
        public_key=did_obj.public_key,
        controllers=did_obj.controllers,
        created_at=did_obj.created_at,
        document=did_obj.to_did_document() if body.include_document else None,
    )


@did_router.get("/list", response_model=ListResponse)
def get_list(
    vault_dir: Optional[str] = Query(None, description="vault 路径"),
) -> ListResponse:
    """列本地 DID."""
    dids = list_local_dids(registry_path=_registry_path(vault_dir))
    return ListResponse(count=len(dids), items=[_summary(d) for d in dids])


__all__ = ["did_router"]
