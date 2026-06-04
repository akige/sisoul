# sisoul chat — Signal Double Ratchet + PQXDH (P2-G)

Per-message forward-secret, post-quantum-hybrid end-to-end chat for
sisoul peers, running over the existing Kubo (GossipSub) transport.

## Layers

| Layer | Module | Backend |
|---|---|---|
| Wire transport | `transport.py` | Kubo IPFS pubsub (HTTP API) + in-memory fallback |
| Handshake | `pqxdh.py` | X25519 (libsodium) + ML-KEM-1024 hybrid |
| Per-message ratchet | `double_ratchet.py` | upstream `DoubleRatchet>=1.3` (`recommended/` suite: Curve25519 / HKDF-SHA512 / AES-256-CBC + HMAC-SHA512) |
| Session manager | `session.py` | persists `~/.sisoul/chat/sessions/<peer>.bin` (SecretBox-encrypted) |
| CLI | `sisoul.cli_commands.chat` | `sisoul chat send/recv/sessions/rotate-prekey/status` |

## PQXDH backend resolution

The handshake tries to find a real ML-KEM-1024 implementation in this order:

1. **liboqs-python** — fastest, but requires `cmake` + the liboqs C library.
   Skipped automatically when `oqs` cannot be imported or the shared lib is
   missing (it otherwise triggers a 5-second blocking auto-install).
2. **kyber-py** — pure-Python ML-KEM-1024. Always installable from PyPI.
   This is the default backend on developer machines and in CI.
3. **shim** — deterministic placeholder. **Not cryptographically secure.**
   Round-trips correctly so unit tests can run on hosts that have neither
   real backend, but logs a clear warning. `pqxdh_mode()` returns `"shim"`.

Current expected mode on dev machines: **real** (kyber-py).

## Double Ratchet status

Backed by the `DoubleRatchet` PyPI package (1.3.0). The `recommended/` suite
gives the upstream-validated Signal cipher choices. Forward secrecy is
asserted by `tests/test_double_ratchet.py::test_forward_secrecy_*`:
serializing the session after sending message N, leaking only that snapshot,
does **not** allow decrypting message N+5.

**Production readiness:** the per-message crypto layer is production-grade.
The session manager (`session.py`) is wired but has not undergone an
external audit; expect further hardening before non-trivial deployments
(out-of-order delivery beyond `max_num_skipped_message_keys=1000`, multi-
device fan-out, DoS thresholds tuned to traffic, etc.).

## Topics

- Chat ciphertext: `/sisoul/chat/v1/<sha256(min(a,b):max(a,b))[:16]>`
- Pre-key announce: `/sisoul/prekey/v1/<sha256(did)[:16]>` — published every
  24 h or on `sisoul chat rotate-prekey`.

## CLI

```
sisoul chat send <peer-did> "<message>"   # encrypt + publish
sisoul chat recv [--since 1h]              # subscribe + decrypt
sisoul chat sessions list                  # active sessions + ratchet counters
sisoul chat rotate-prekey                  # fresh PreKeyBundle + announce
sisoul chat status                         # show pqxdh_mode + local DID
```

`--memory` on any of the above uses an in-process MemoryTransport (used by
the integration test suite; no Kubo daemon required).

## Tests

- `tests/test_pqxdh.py` (handshake roundtrip, ML-KEM-1024 sizes, hybrid)
- `tests/test_double_ratchet.py` (10+ send/recv, FS, ordering, serialization)
- `tests/test_chat_integration.py` (Alice+Bob mock GossipSub: PQXDH → ratchet → send → recv)
