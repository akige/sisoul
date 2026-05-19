"""sisoul daemon · DAO governance HTTP API (Phase 3 P3-4).

Endpoints (prefix /sisoul/dao):
- GET  /sisoul/dao/proposals          列出所有 mock proposals (live: 暂 stub, 需链上 event 索引)
- GET  /sisoul/dao/proposals/{id}     查单个 proposal status
- POST /sisoul/dao/propose            提案 PIP 升级 (PIP-id + next_status)
- POST /sisoul/dao/vote               投票
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from sisoul.dao.governance import (
    DAOConfig,
    DAOError,
    GovernorClient,
    ProposalNotFoundError,
    Web3NotInstalledError,
    load_dao_config,
    propose_pip_promotion,
)

dao_router = APIRouter(prefix="/sisoul/dao", tags=["dao"])

# 进程级 client 实例 (mock 模式靠它共享 _mock_store).
_shared_client: Optional[GovernorClient] = None


def _client(config_path: Optional[str] = None) -> GovernorClient:
    """返进程共享 client (mock 内存态需跨请求保留)."""
    global _shared_client
    if _shared_client is not None and config_path is None:
        return _shared_client
    try:
        cfg = load_dao_config(Path(config_path) if config_path else None)
    except DAOError as e:
        raise HTTPException(status_code=500, detail=f"dao config: {e}")
    client = GovernorClient(cfg)
    if config_path is None:
        _shared_client = client
    return client


def reset_dao_client_for_test() -> None:
    """test 用: 清空 shared client (隔离 mock 状态)."""
    global _shared_client
    _shared_client = None


# ── schemas ──────────────────────────────────────────────────────────────────


class ProposeRequest(BaseModel):
    pip_id: int = Field(..., gt=0, description="PIP id (e.g. 3 for PIP-003)")
    next_status: str = Field("review", description="review/finalcall/final/withdrawn")
    config_path: Optional[str] = None


class ProposeResponse(BaseModel):
    proposal_id: int
    description: str
    state: int
    state_name: str
    tx_hash: Optional[str] = None
    description_hash: str


class VoteRequest(BaseModel):
    proposal_id: int
    support: str = Field(..., description="for / against / abstain (或 0/1/2 字符串)")
    config_path: Optional[str] = None


class VoteResponse(BaseModel):
    proposal_id: int
    support: str
    tx_hash: str


class ProposalSummaryResponse(BaseModel):
    proposal_id: int
    description: str
    state: int
    state_name: str
    proposer: str
    votes_for: int
    votes_against: int
    votes_abstain: int
    tx_hash: Optional[str] = None


class ProposalsListResponse(BaseModel):
    mode: str
    count: int
    proposals: list[ProposalSummaryResponse]


# ── routes ───────────────────────────────────────────────────────────────────


@dao_router.get("/proposals", response_model=ProposalsListResponse)
def list_proposals(
    config_path: Optional[str] = Query(None, description="可选 config 路径"),
    limit: int = Query(50, ge=1, le=500),
) -> ProposalsListResponse:
    """列 proposals. mock: 内存 _mock_store; live: 暂 stub (需 event scan)."""
    client = _client(config_path)
    if client.config.mode == "mock":
        items = list(client._mock_store.values())[:limit]  # noqa: SLF001
        return ProposalsListResponse(
            mode="mock",
            count=len(items),
            proposals=[
                ProposalSummaryResponse(
                    proposal_id=s.proposal_id,
                    description=s.description,
                    state=s.state,
                    state_name=s.state_name,
                    proposer=s.proposer,
                    votes_for=s.votes_for,
                    votes_against=s.votes_against,
                    votes_abstain=s.votes_abstain,
                    tx_hash=s.tx_hash,
                )
                for s in items
            ],
        )
    # live 模式: 不在内存, 需 event 索引服务. 暂返空 + 提示.
    return ProposalsListResponse(mode="live", count=0, proposals=[])


@dao_router.get("/proposals/{proposal_id}", response_model=ProposalSummaryResponse)
def get_proposal(
    proposal_id: int,
    config_path: Optional[str] = Query(None),
) -> ProposalSummaryResponse:
    client = _client(config_path)
    try:
        s = client.summary(proposal_id)
    except ProposalNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Web3NotInstalledError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except DAOError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return ProposalSummaryResponse(
        proposal_id=s.proposal_id,
        description=s.description,
        state=s.state,
        state_name=s.state_name,
        proposer=s.proposer,
        votes_for=s.votes_for,
        votes_against=s.votes_against,
        votes_abstain=s.votes_abstain,
        tx_hash=s.tx_hash,
    )


@dao_router.post("/propose", response_model=ProposeResponse)
def post_propose(req: ProposeRequest) -> ProposeResponse:
    client = _client(req.config_path)
    try:
        s = propose_pip_promotion(req.pip_id, req.next_status, client)
    except Web3NotInstalledError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except DAOError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ProposeResponse(
        proposal_id=s.proposal_id,
        description=s.description,
        state=s.state,
        state_name=s.state_name,
        tx_hash=s.tx_hash,
        description_hash=s.description_hash,
    )


@dao_router.post("/vote", response_model=VoteResponse)
def post_vote(req: VoteRequest) -> VoteResponse:
    client = _client(req.config_path)
    try:
        tx = client.cast_vote(req.proposal_id, req.support)
    except ProposalNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Web3NotInstalledError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except DAOError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return VoteResponse(proposal_id=req.proposal_id, support=req.support, tx_hash=tx)


__all__ = ["dao_router", "reset_dao_client_for_test"]
