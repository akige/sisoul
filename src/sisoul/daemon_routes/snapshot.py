"""sisoul daemon · snapshot endpoints (Phase 3 W41-W43 dev-C).

Arweave snapshot 的 FastAPI 路由. 给 PWA dashboard / 外部 orchestrator 用.

Endpoints (5):
- POST /sisoul/snapshot/now       → 立即 snapshot + 上传
- GET  /sisoul/snapshot/list      → 历史列表
- POST /sisoul/snapshot/restore   → 从 tx_id/CID/hash 还原
- POST /sisoul/snapshot/schedule  → 设周期 schedule (cron-like)
- GET  /sisoul/snapshot/config    → 查/改 config

⚠️ router 命名强制规范: snapshot_router = APIRouter(prefix="/sisoul/snapshot",
   tags=["snapshot"]).
"""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from sisoul.onchain.arweave import (
    ArweaveSnapshot,
    SnapshotHistory,
    SnapshotRecord,
    schedule_monthly_snapshot,
)
from sisoul.vault.storage import DEFAULT_VAULT_DIR


# ── 共享 client 构造 (跟 cli_commands/snapshot.py 同语义, 不依赖该模块) ──

def _build_client(network: str = "testnet") -> ArweaveSnapshot:
    return ArweaveSnapshot(
        pinata_jwt=os.environ.get("PINATA_JWT"),
        arweave_wallet_path=(
            Path(os.environ["ARWEAVE_WALLET"]).expanduser()
            if os.environ.get("ARWEAVE_WALLET")
            else None
        ),
        network=network,  # type: ignore[arg-type]
    )


# ── pydantic models ──────────────────────────────────────────────────────


class SnapshotNowRequest(BaseModel):
    vault_dir: Optional[str] = Field(default=None, description=f"vault 路径; 默认 {DEFAULT_VAULT_DIR}")
    upload: Literal["arweave", "ipfs", "both", "none"] = "both"
    network: Literal["testnet", "mainnet", "mock"] = "testnet"


class SnapshotRecordOut(BaseModel):
    timestamp: str
    size_bytes: int
    sha256: str
    ipfs_cid: Optional[str] = None
    arweave_tx_id: Optional[str] = None
    vault_master_key_fingerprint: str = ""
    network: str = "testnet"
    status: str = "ok"
    error: Optional[str] = None


class SnapshotRestoreRequest(BaseModel):
    tx_id_or_cid: str
    target_vault_dir: str
    source: Literal["auto", "arweave", "ipfs"] = "auto"
    network: Literal["testnet", "mainnet", "mock"] = "testnet"


class SnapshotScheduleRequest(BaseModel):
    cadence: Literal["monthly", "weekly", "daily", "never"] = "monthly"
    upload: Literal["arweave", "ipfs", "both"] = "both"
    install: bool = False


class SnapshotConfigOut(BaseModel):
    pinata_jwt_configured: bool
    arweave_wallet_path: Optional[str] = None
    arweave_allow_mainnet: bool
    history_path: str


def _to_out(r: SnapshotRecord) -> SnapshotRecordOut:
    return SnapshotRecordOut(**asdict(r))


# ── Router ───────────────────────────────────────────────────────────────


snapshot_router = APIRouter(prefix="/sisoul/snapshot", tags=["snapshot"])


@snapshot_router.post("/now", response_model=SnapshotRecordOut)
def post_snapshot_now(req: SnapshotNowRequest) -> SnapshotRecordOut:
    root = Path(req.vault_dir).expanduser() if req.vault_dir else DEFAULT_VAULT_DIR
    if not root.exists():
        raise HTTPException(status_code=400, detail=f"vault dir 不存在: {root}")
    client = _build_client(network=req.network)
    try:
        record = client.snapshot_now(root, upload=req.upload)
    except ValueError as e:
        # 波 7 dev-A bug-5: ValueError 是 input invalid (client err) → 400
        raise HTTPException(status_code=400, detail=f"snapshot 输入错: {e}") from e
    except RuntimeError as e:
        # 波 7 dev-A bug-5: RuntimeError 多为 upstream/网络挂 → 502 (bad gateway)
        raise HTTPException(status_code=502, detail=f"snapshot upstream 失败: {e}") from e
    return _to_out(record)


@snapshot_router.get("/list", response_model=list[SnapshotRecordOut])
def get_snapshot_list(
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[SnapshotRecordOut]:
    h = SnapshotHistory()
    records = h.load()[-limit:]
    return [_to_out(r) for r in records]


@snapshot_router.post("/restore", response_model=dict)
def post_snapshot_restore(req: SnapshotRestoreRequest) -> dict[str, Any]:
    target = Path(req.target_vault_dir).expanduser()
    client = _build_client(network=req.network)

    candidate = req.tx_id_or_cid
    # sha256? → history 查
    if len(candidate) == 64 and all(c in "0123456789abcdef" for c in candidate.lower()):
        rec = client.history.find(candidate)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"history 无此 sha256: {candidate}")
        candidate = rec.arweave_tx_id or rec.ipfs_cid or ""
        if not candidate:
            raise HTTPException(status_code=400, detail="history 记录无 ipfs/arweave 凭证")

    try:
        out = client.restore_from_arweave(
            candidate,
            target_vault_dir=target,
            source=req.source,
        )
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except FileNotFoundError as e:
        # 波 7 dev-A bug-5: 目标不存在 / 拉取后文件缺 → 404
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        # 波 7 dev-A bug-5: 输入 tx_id/CID 格式错 → 400
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        # 波 7 dev-A bug-5 (qa-D 报 P2-1): fake/mock tx_id 真拉失败 / Pinata Pinata 401 / Arweave gw 挂
        # 全是 upstream 不可用 (非 server 自身 bug). 改 502 不再 5xx server err.
        msg = str(e)
        if "fake" in msg.lower() or "mock" in msg.lower() or "format" in msg.lower():
            raise HTTPException(status_code=400, detail=msg) from e
        raise HTTPException(status_code=502, detail=msg) from e
    return {"restored_to": str(out), "ok": True}


@snapshot_router.post("/schedule", response_model=dict)
def post_snapshot_schedule(req: SnapshotScheduleRequest) -> dict[str, Any]:
    result = schedule_monthly_snapshot(
        cadence=req.cadence,
        upload=req.upload,
        install=req.install,
    )
    # 转 install_path Path → str (JSON 友好)
    if result.get("install_path"):
        result["install_path"] = str(result["install_path"])
    return result


@snapshot_router.get("/config", response_model=SnapshotConfigOut)
def get_snapshot_config() -> SnapshotConfigOut:
    from sisoul.onchain.arweave import DEFAULT_HISTORY_PATH

    return SnapshotConfigOut(
        pinata_jwt_configured=bool(os.environ.get("PINATA_JWT")),
        arweave_wallet_path=os.environ.get("ARWEAVE_WALLET"),
        arweave_allow_mainnet=os.environ.get("ARWEAVE_ALLOW_MAINNET") == "1",
        history_path=str(DEFAULT_HISTORY_PATH),
    )


__all__ = [
    "snapshot_router",
    "SnapshotNowRequest",
    "SnapshotRestoreRequest",
    "SnapshotScheduleRequest",
    "SnapshotRecordOut",
    "SnapshotConfigOut",
]
