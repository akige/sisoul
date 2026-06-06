"""Prekey directory — HTTP convenience layer for PQXDH bundle exchange.

Why this exists
================
PQXDH/Double-Ratchet requires Alice to know Bob's PreKey bundle before she
can send him an encrypted message. Three ways to do that:

  1. P2P GossipSub auto-discovery — both peers' daemons publish/subscribe
     to /sisoul/prekey/v1/<did_hash>. Works when both daemons run + kubo
     swarm is connected.
  2. Manual file exchange — `sisoul chat export-prekey` → scp / Signal /
     AirDrop → `sisoul chat cache-peer`. Works always, terrible UX.
  3. HTTP prekey directory — Bob POSTs his bundle to a public server,
     Alice GETs by DID. Like Signal's prekey server.

Alpha v1.0 ships all three. The CLI picks them in order: local cache → HTTP
directory (fast, easy) → GossipSub (decentralised, no server needed).

Per §4.11 (never-shutdown), the HTTP server is **convenience, not authority**:

- The server stores bundles uploaded by clients verbatim and serves them.
  It never modifies, signs, or vouches.
- Signatures on the bundle are checked client-side. A malicious server can
  only refuse to serve; it cannot forge.
- If our server vanishes, users fall back to GossipSub or manual exchange.
  No data lockin.
- Anyone can host their own directory at any URL by setting
  SISOUL_PREKEY_DIRECTORY env.
"""

from .server import (
    create_prekey_directory_app,
    PrekeyStore,
    PrekeyRecord,
    DEFAULT_DIRECTORY_URL,
)
from .client import (
    fetch_peer_prekey,
    publish_my_prekey,
    PrekeyDirectoryError,
)

__all__ = [
    "create_prekey_directory_app",
    "PrekeyStore",
    "PrekeyRecord",
    "DEFAULT_DIRECTORY_URL",
    "fetch_peer_prekey",
    "publish_my_prekey",
    "PrekeyDirectoryError",
]
