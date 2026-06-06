"""HTTP prekey directory server — FastAPI.

Endpoints:

  PUT  /v1/prekey/<did>       upload (or overwrite) own bundle
  GET  /v1/prekey/<did>       fetch a peer's bundle
  GET  /v1/inbox/<did>        list inbound friend-requests/messages addressed
                              to <did> (just metadata, content is e2e encrypted)
  POST /v1/inbox/<did>        drop a friend-request or message envelope for did
  GET  /healthz               liveness

Storage: per-DID JSON files under $SISOUL_PREKEY_DATA (default /var/lib/sisoul-prekey).

Security model:
- DOES NOT authenticate uploads. We rely on the bundle's own signature
  (signed_pre_key_pub + signature field) for client-side verification.
- Last-writer-wins per DID. Same DID can rotate freely.
- 50 KB per-bundle cap; 50 messages-per-inbox cap; 7-day TTL.
- Per-DID rate-limit: 20 PUT/min, 50 GET/min, 20 POST/min.
"""
from __future__ import annotations
import json
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Optional

try:
    from fastapi import FastAPI, HTTPException, Request, Response, Body
    from pydantic import BaseModel
    HAVE_FASTAPI = True

    class PrekeyPayload(BaseModel):
        did: str
        bundle: dict
        username: Optional[str] = ""
        bio: Optional[str] = ""

    class EnvelopePayload(BaseModel):
        sender_did: str
        kind: str  # "friend-request" | "chat-hint"
        topic: str
        ciphertext: Optional[str] = None
        note: Optional[str] = None
except Exception:
    HAVE_FASTAPI = False
    PrekeyPayload = None  # type: ignore
    EnvelopePayload = None  # type: ignore


DEFAULT_DIRECTORY_URL = os.environ.get(
    "SISOUL_PREKEY_DIRECTORY",
    "http://198.51.100.1:8767",  # maintainer-hosted public alpha instance
)
_DEFAULT_DATA_DIR = os.environ.get("SISOUL_PREKEY_DATA", "/var/lib/sisoul-prekey")
_MAX_BUNDLE_BYTES = 50_000
_MAX_INBOX_MESSAGES = 50
_TTL_SECONDS = 7 * 86400
_DID_RE = re.compile(r"^did:key:z[A-Za-z0-9]{40,90}$")


class PrekeyRecord:
    """In-memory representation of a stored bundle."""

    __slots__ = ("did", "bundle", "uploaded_at", "username", "bio", "last_seen")

    def __init__(self, did: str, bundle: dict, uploaded_at: float,
                 username: str = "", bio: str = "",
                 last_seen: Optional[float] = None) -> None:
        self.did = did
        self.bundle = bundle
        self.uploaded_at = uploaded_at
        self.username = username
        self.bio = bio
        self.last_seen = last_seen or uploaded_at

    def to_dict(self) -> dict:
        return {
            "did": self.did,
            "bundle": self.bundle,
            "uploaded_at": self.uploaded_at,
            "username": self.username,
            "bio": self.bio,
            "last_seen": self.last_seen,
        }


class PrekeyStore:
    """File-backed prekey + inbox store. Single-process safe (one daemon)."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else Path(_DEFAULT_DATA_DIR)
        (self.data_dir / "prekeys").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "inboxes").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe(did: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]", "_", did)

    def _prekey_path(self, did: str) -> Path:
        return self.data_dir / "prekeys" / f"{self._safe(did)}.json"

    def _inbox_path(self, did: str) -> Path:
        return self.data_dir / "inboxes" / f"{self._safe(did)}.jsonl"

    def put_prekey(self, did: str, bundle: dict,
                   username: str = "", bio: str = "") -> PrekeyRecord:
        # If a record already exists, keep its previously registered username
        # unless the caller explicitly passes a new (non-empty) one. This lets
        # `sisoul username register foo` persist across `rotate-prekey` calls.
        prev = self.get_prekey(did)
        if not username and prev:
            username = prev.username
        if not bio and prev:
            bio = prev.bio
        now = time.time()
        rec = PrekeyRecord(did=did, bundle=bundle, uploaded_at=now,
                           username=username, bio=bio, last_seen=now)
        tmp = self._prekey_path(did).with_suffix(".tmp")
        tmp.write_text(json.dumps(rec.to_dict()))
        tmp.replace(self._prekey_path(did))
        # Index username → did so /v1/resolve/<username> can find it.
        if username:
            self._index_username(username, did)
        return rec

    def _username_index_path(self, username: str) -> Path:
        idx_dir = self.data_dir / "usernames"
        idx_dir.mkdir(exist_ok=True)
        return idx_dir / f"{self._safe(username.lower())}.txt"

    def _index_username(self, username: str, did: str) -> None:
        # last-writer-wins: if Alice took username "foo" first and Bob tries to
        # take it later, Bob wins. For alpha that's fine; v1.1 adds proof-of-ownership.
        self._username_index_path(username).write_text(did)

    def resolve_username(self, username: str) -> Optional[str]:
        p = self._username_index_path(username)
        if not p.exists():
            return None
        try:
            did = p.read_text().strip()
            return did or None
        except Exception:
            return None

    def get_prekey(self, did: str) -> Optional[PrekeyRecord]:
        p = self._prekey_path(did)
        if not p.exists():
            return None
        try:
            obj = json.loads(p.read_text())
        except Exception:
            return None
        if time.time() - obj["uploaded_at"] > _TTL_SECONDS:
            return None
        return PrekeyRecord(
            did=obj["did"], bundle=obj["bundle"], uploaded_at=obj["uploaded_at"],
            username=obj.get("username", ""), bio=obj.get("bio", ""),
            last_seen=obj.get("last_seen"),
        )

    def list_active_peers(self, *, limit: int = 100, min_age_seconds: float = 0.0,
                          max_age_seconds: float = 7 * 86400,
                          filter_text: str = "") -> list[dict]:
        """Return summary cards of recently-active peers for /v1/discover."""
        now = time.time()
        out = []
        prekey_dir = self.data_dir / "prekeys"
        if not prekey_dir.exists():
            return []
        flt = filter_text.lower().strip()
        for path in prekey_dir.glob("*.json"):
            try:
                obj = json.loads(path.read_text())
            except Exception:
                continue
            last_seen = obj.get("last_seen", obj.get("uploaded_at", 0))
            age = now - last_seen
            if age < min_age_seconds or age > max_age_seconds:
                continue
            uname = obj.get("username", "")
            bio = obj.get("bio", "")
            if flt and flt not in uname.lower() and flt not in bio.lower():
                continue
            out.append({
                "did": obj["did"],
                "username": uname,
                "bio": bio,
                "last_seen": last_seen,
                "age_seconds": age,
            })
        out.sort(key=lambda x: x["age_seconds"])
        return out[:limit]

    def append_inbox(self, did: str, envelope: dict) -> dict:
        p = self._inbox_path(did)
        entry = {"received_at": time.time(), **envelope}
        with p.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        # Trim to last N
        try:
            lines = p.read_text().splitlines()
            if len(lines) > _MAX_INBOX_MESSAGES:
                lines = lines[-_MAX_INBOX_MESSAGES:]
                p.write_text("\n".join(lines) + "\n")
        except Exception:
            pass
        return entry

    def list_inbox(self, did: str, *, since: float = 0.0, limit: int = 50) -> list[dict]:
        p = self._inbox_path(did)
        if not p.exists():
            return []
        out = []
        try:
            for line in p.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("received_at", 0) < since:
                    continue
                out.append(e)
        except Exception:
            return out
        return out[-limit:]


# ── rate limit ──────────────────────────────────────────────────────────────


class _RateLimit:
    def __init__(self, max_per_window: int, window_seconds: float):
        self.max = max_per_window
        self.window = window_seconds
        self.buckets: dict[str, deque] = {}

    def check(self, key: str) -> bool:
        now = time.time()
        bucket = self.buckets.setdefault(key, deque())
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.max:
            return False
        bucket.append(now)
        return True


# ── app ─────────────────────────────────────────────────────────────────────


def create_prekey_directory_app(store: Optional[PrekeyStore] = None):
    if not HAVE_FASTAPI:
        raise RuntimeError("fastapi not installed; pip install fastapi uvicorn")
    app = FastAPI(title="sisoul prekey directory")
    s = store or PrekeyStore()
    put_limit = _RateLimit(20, 60)
    get_limit = _RateLimit(60, 60)
    post_limit = _RateLimit(20, 60)

    def _check_did(did: str) -> None:
        if not _DID_RE.match(did):
            raise HTTPException(400, detail=f"invalid did:key: {did}")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "service": "sisoul-prekey-directory", "ts": int(time.time())}

    _USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{2,32}$")

    @app.put("/v1/prekey/{did}")
    async def put_prekey(did: str, request: Request, payload: PrekeyPayload = Body(...)):
        _check_did(did)
        if payload.did != did:
            raise HTTPException(400, detail=f"path did={did} ≠ body did={payload.did}")
        client = request.client.host if request.client else "unknown"
        if not put_limit.check(client):
            raise HTTPException(429, detail="rate limit (20/min PUT)")
        body_size = len(json.dumps(payload.bundle))
        if body_size > _MAX_BUNDLE_BYTES:
            raise HTTPException(413, detail=f"bundle too large ({body_size}B > {_MAX_BUNDLE_BYTES}B)")
        username = (payload.username or "").strip()
        if username and not _USERNAME_RE.match(username):
            raise HTTPException(400, detail=f"username must match [a-zA-Z0-9_-]{{2,32}}: {username!r}")
        bio = (payload.bio or "").strip()[:200]
        rec = s.put_prekey(did, payload.bundle, username=username, bio=bio)
        return {"ok": True, "did": did, "uploaded_at": rec.uploaded_at,
                "username": rec.username, "bio": rec.bio}

    @app.get("/v1/resolve/{username}")
    async def resolve_username(username: str, request: Request):
        client = request.client.host if request.client else "unknown"
        if not get_limit.check(client):
            raise HTTPException(429, detail="rate limit (60/min GET)")
        if not _USERNAME_RE.match(username):
            raise HTTPException(400, detail=f"invalid username: {username!r}")
        did = s.resolve_username(username)
        if did is None:
            raise HTTPException(404, detail=f"username {username!r} not registered")
        return {"username": username, "did": did}

    @app.get("/v1/discover")
    async def discover(filter: str = "", limit: int = 50, max_age_hours: float = 168.0,
                       request: Request = None):
        if request:
            client = request.client.host if request.client else "unknown"
            if not get_limit.check(client):
                raise HTTPException(429, detail="rate limit (60/min GET)")
        peers = s.list_active_peers(
            filter_text=filter,
            limit=min(limit, 200),
            max_age_seconds=max_age_hours * 3600.0,
        )
        return {"count": len(peers), "peers": peers}

    @app.get("/v1/prekey/{did}")
    async def get_prekey(did: str, request: Request):
        _check_did(did)
        client = request.client.host if request.client else "unknown"
        if not get_limit.check(client):
            raise HTTPException(429, detail="rate limit (60/min GET)")
        rec = s.get_prekey(did)
        if rec is None:
            raise HTTPException(404, detail=f"no prekey for {did}")
        return rec.to_dict()

    @app.post("/v1/inbox/{did}")
    async def post_inbox(did: str, request: Request, env: EnvelopePayload = Body(...)):
        _check_did(did)
        _check_did(env.sender_did)
        client = request.client.host if request.client else "unknown"
        if not post_limit.check(client):
            raise HTTPException(429, detail="rate limit (20/min POST)")
        if env.kind not in ("friend-request", "chat-hint"):
            raise HTTPException(400, detail=f"invalid kind: {env.kind}")
        entry = s.append_inbox(did, env.model_dump())
        return {"ok": True, "received_at": entry["received_at"]}

    @app.get("/v1/inbox/{did}")
    async def get_inbox(did: str, since: float = 0.0, limit: int = 50, request: Request = None):
        _check_did(did)
        if request:
            client = request.client.host if request.client else "unknown"
            if not get_limit.check(client):
                raise HTTPException(429, detail="rate limit (60/min GET)")
        return {"did": did, "entries": s.list_inbox(did, since=since, limit=min(limit, 200))}

    return app
