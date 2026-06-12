"""HTTP prekey-directory client used by `sisoul chat send` + `chat rotate-prekey`.

The directory URL comes from the SISOUL_PREKEY_DIRECTORY env var. There is
**no public default**: the maintainer-hosted public instances were
decommissioned 2026-06-06 when v1.0-stable moved to a fully
decentralised path (kubo+GossipSub transport, EAS for username->did). To use the
legacy HTTP-directory path you must run your own instance and point clients at
it via SISOUL_PREKEY_DIRECTORY; otherwise these calls intentionally fail fast.
"""
from __future__ import annotations
import os
from typing import Optional

try:
    import httpx
    HAVE_HTTPX = True
except Exception:
    HAVE_HTTPX = False


class PrekeyDirectoryError(Exception):
    """Base."""


def _resolve_url() -> str:
    """Resolve the prekey-directory URL the client should hit.

    HISTORY: early alpha briefly defaulted to maintainer-hosted directory
    instances; all were DECOMMISSIONED 2026-06-06 (per project decision to
    ship v1.0 stable as
    fully decentralised: kubo+GossipSub for transport, EAS attestation on
    Optimism mainnet for username → did mapping).

    There is intentionally no public default. Set SISOUL_PREKEY_DIRECTORY
    to point at your own self-hosted instance, or omit it entirely once
    the v1.0-stable kubo+EAS code path lands.
    """
    return os.environ.get(
        "SISOUL_PREKEY_DIRECTORY",
        "",  # no default: tells callers "directory layer is opt-in"
    ).rstrip("/")


def publish_my_prekey(did: str, bundle_dict: dict, *,
                      username: str = "", bio: str = "",
                      timeout: float = 10.0,
                      directory_url: Optional[str] = None) -> dict:
    """PUT /v1/prekey/<did>. Optionally register username + bio at the same time."""
    if not HAVE_HTTPX:
        raise PrekeyDirectoryError("httpx not installed")
    url = (directory_url or _resolve_url()) + f"/v1/prekey/{did}"
    payload = {"did": did, "bundle": bundle_dict}
    if username:
        payload["username"] = username
    if bio:
        payload["bio"] = bio
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.put(url, json=payload)
            if r.status_code >= 400:
                raise PrekeyDirectoryError(f"PUT {url} → {r.status_code}: {r.text[:200]}")
            return r.json()
    except httpx.HTTPError as e:
        raise PrekeyDirectoryError(f"HTTP transport: {e}") from e


def resolve_username(username: str, *, timeout: float = 10.0,
                     directory_url: Optional[str] = None) -> Optional[str]:
    """GET /v1/resolve/<username> → did or None on 404."""
    if not HAVE_HTTPX:
        raise PrekeyDirectoryError("httpx not installed")
    url = (directory_url or _resolve_url()) + f"/v1/resolve/{username}"
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(url)
            if r.status_code == 404:
                return None
            if r.status_code >= 400:
                raise PrekeyDirectoryError(f"GET {url} → {r.status_code}: {r.text[:200]}")
            return r.json().get("did")
    except httpx.HTTPError as e:
        raise PrekeyDirectoryError(f"HTTP transport: {e}") from e


def discover_peers(filter_text: str = "", *, limit: int = 50, max_age_hours: float = 168.0,
                   timeout: float = 10.0,
                   directory_url: Optional[str] = None) -> list[dict]:
    """GET /v1/discover — list active peers (username/bio/last_seen)."""
    if not HAVE_HTTPX:
        raise PrekeyDirectoryError("httpx not installed")
    url = (directory_url or _resolve_url()) + "/v1/discover"
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(url, params={"filter": filter_text, "limit": limit,
                                    "max_age_hours": max_age_hours})
            if r.status_code >= 400:
                raise PrekeyDirectoryError(f"GET {url} → {r.status_code}: {r.text[:200]}")
            return r.json().get("peers", [])
    except httpx.HTTPError as e:
        raise PrekeyDirectoryError(f"HTTP transport: {e}") from e


def fetch_peer_prekey(did: str, *, timeout: float = 10.0,
                       directory_url: Optional[str] = None) -> Optional[dict]:
    """GET /v1/prekey/<did>. Returns bundle dict (PreKeyBundle.to_dict format)
    or None if 404. Raises PrekeyDirectoryError on transport / 5xx."""
    if not HAVE_HTTPX:
        raise PrekeyDirectoryError("httpx not installed")
    url = (directory_url or _resolve_url()) + f"/v1/prekey/{did}"
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(url)
            if r.status_code == 404:
                return None
            if r.status_code >= 400:
                raise PrekeyDirectoryError(f"GET {url} → {r.status_code}: {r.text[:200]}")
            payload = r.json()
            return payload.get("bundle")
    except httpx.HTTPError as e:
        raise PrekeyDirectoryError(f"HTTP transport: {e}") from e


def push_inbox(recipient_did: str, sender_did: str, kind: str, topic: str,
               *, note: Optional[str] = None, ciphertext: Optional[str] = None,
               timeout: float = 10.0, directory_url: Optional[str] = None) -> dict:
    """POST /v1/inbox/<recipient> with a small envelope so the recipient sees
    "you have a new friend-request / chat-hint" next time they run `sisoul inbox`.
    The envelope is metadata only — ciphertext is opaque to the directory."""
    if not HAVE_HTTPX:
        raise PrekeyDirectoryError("httpx not installed")
    url = (directory_url or _resolve_url()) + f"/v1/inbox/{recipient_did}"
    payload = {
        "sender_did": sender_did,
        "kind": kind,
        "topic": topic,
    }
    if note is not None:
        payload["note"] = note
    if ciphertext is not None:
        payload["ciphertext"] = ciphertext
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(url, json=payload)
            if r.status_code >= 400:
                raise PrekeyDirectoryError(f"POST {url} → {r.status_code}: {r.text[:200]}")
            return r.json()
    except httpx.HTTPError as e:
        raise PrekeyDirectoryError(f"HTTP transport: {e}") from e


def list_inbox(my_did: str, *, since: float = 0.0, limit: int = 50,
                timeout: float = 10.0, directory_url: Optional[str] = None) -> list[dict]:
    """GET /v1/inbox/<did>?since=...&limit=..."""
    if not HAVE_HTTPX:
        raise PrekeyDirectoryError("httpx not installed")
    url = (directory_url or _resolve_url()) + f"/v1/inbox/{my_did}"
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.get(url, params={"since": since, "limit": limit})
            if r.status_code >= 400:
                raise PrekeyDirectoryError(f"GET {url} → {r.status_code}: {r.text[:200]}")
            payload = r.json()
            return payload.get("entries", [])
    except httpx.HTTPError as e:
        raise PrekeyDirectoryError(f"HTTP transport: {e}") from e


__all__ = [
    "publish_my_prekey", "fetch_peer_prekey", "push_inbox", "list_inbox",
    "PrekeyDirectoryError",
]
