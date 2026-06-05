"""sisoul daemon — native push notification device register routes.

Endpoints:
- POST /v1/push/register       register an iOS/Android device token
- GET  /v1/push/devices        list registered devices (filter by did_key)
- DELETE /v1/push/devices/{token}  unregister a device
- POST /v1/push/test           send a test local notification (skeleton)

Storage: vault/push_devices.json (one row per (token, platform, did_key)).
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

push_router = APIRouter(prefix="/v1/push", tags=["push-notifications"])


PlatformType = Literal["ios", "android"]


def _vault() -> Path:
    return Path(os.environ.get("SISOUL_VAULT", "~/.sisoul")).expanduser()


def _devices_file() -> Path:
    return _vault() / "push_devices.json"


def _load_devices() -> list[dict]:
    f = _devices_file()
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text())
    except Exception:
        return []


def _save_devices(devices: list[dict]) -> None:
    f = _devices_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(devices, ensure_ascii=False, indent=2))


class PushRegisterRequest(BaseModel):
    token: str = Field(..., min_length=8, max_length=512)
    platform: PlatformType
    did_key: Optional[str] = None
    registered_at: Optional[str] = None


class PushDevice(BaseModel):
    token: str
    platform: PlatformType
    did_key: Optional[str]
    registered_at: str
    last_seen_at: str


class PushRegisterResponse(BaseModel):
    success: bool
    device: PushDevice
    is_new: bool


class PushDevicesResponse(BaseModel):
    devices: list[PushDevice]
    count: int


class PushTestRequest(BaseModel):
    title: str = "sisoul test push"
    body: str = "this is a test notification"
    target_did: Optional[str] = None


class PushTestResponse(BaseModel):
    sent: int
    devices_targeted: list[str]
    note: str = ""


@push_router.post("/register", response_model=PushRegisterResponse)
async def push_register(req: PushRegisterRequest) -> PushRegisterResponse:
    """Register a native push device token.

    Idempotent: same token re-registered updates last_seen_at + returns is_new=False.
    """
    devices = _load_devices()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    existing = next((d for d in devices if d.get("token") == req.token), None)
    if existing:
        existing["last_seen_at"] = now
        # Refresh did_key if newly bound
        if req.did_key and not existing.get("did_key"):
            existing["did_key"] = req.did_key
        _save_devices(devices)
        return PushRegisterResponse(
            success=True,
            device=PushDevice(**existing),
            is_new=False,
        )

    new_device = {
        "token": req.token,
        "platform": req.platform,
        "did_key": req.did_key,
        "registered_at": req.registered_at or now,
        "last_seen_at": now,
    }
    devices.append(new_device)
    _save_devices(devices)
    return PushRegisterResponse(
        success=True,
        device=PushDevice(**new_device),
        is_new=True,
    )


@push_router.get("/devices", response_model=PushDevicesResponse)
async def push_devices(did_key: Optional[str] = None) -> PushDevicesResponse:
    """List registered native push devices. Optional filter by did_key."""
    devices = _load_devices()
    if did_key:
        devices = [d for d in devices if d.get("did_key") == did_key]
    return PushDevicesResponse(
        devices=[PushDevice(**d) for d in devices],
        count=len(devices),
    )


@push_router.delete("/devices/{token}")
async def push_unregister(token: str) -> dict:
    """Remove a device by token. 404 if not found."""
    devices = _load_devices()
    n_before = len(devices)
    devices = [d for d in devices if d.get("token") != token]
    if len(devices) == n_before:
        raise HTTPException(status_code=404, detail=f"device token not found")
    _save_devices(devices)
    return {"success": True, "removed": n_before - len(devices)}


@push_router.post("/test", response_model=PushTestResponse)
async def push_test(req: PushTestRequest) -> PushTestResponse:
    """Send a test push notification to registered devices.

    Skeleton: does NOT actually call APNs/FCM (needs APNs key + FCM service account JSON
    configured separately). Returns the list of devices that would receive the push.

    Full impl: integrate `aioapns` (iOS) + `firebase-admin` (Android) and dispatch.
    """
    devices = _load_devices()
    if req.target_did:
        devices = [d for d in devices if d.get("did_key") == req.target_did]

    targeted = [d["token"][:16] + "..." for d in devices]
    return PushTestResponse(
        sent=0,  # skeleton; 0 actually sent
        devices_targeted=targeted,
        note="skeleton — configure APNs key (iOS) + FCM service account (Android) for real delivery",
    )


__all__ = ["push_router"]
