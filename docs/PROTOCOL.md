# sisoul Protocol Specification v1.0-alpha

> Wire-level protocol spec for third-party implementations.
> Reference implementation: this repo (Python). Other implementations (Rust/Go/Swift) welcome.

## 1. Identity

### 1.1 did:key

Each user is identified by a [W3C did:key](https://w3c-ccg.github.io/did-method-key/) identifier.

```
did:key:z6Mk{multibase-encoded Ed25519 public key}
```

- Algorithm: Ed25519 (curve25519-based EdDSA)
- Encoding: multibase base58btc
- Length: 56 chars (44 base58 chars after `did:key:z6Mk` prefix)

Generation:
```python
import nacl.signing
sk = nacl.signing.SigningKey.generate()
vk = sk.verify_key
# did_key = "did:key:z6Mk" + base58btc(0xed01 || vk.encode())
```

### 1.2 BIP-39 seed (optional but recommended)

Backup phrase: 12-word BIP-39 mnemonic. Derives master key via PBKDF2-HMAC-SHA512.

```
seed_master = PBKDF2-HMAC-SHA512(mnemonic, "mnemonic" + passphrase, 2048 iter, 64 bytes)
```

From master, derive sub-keys via HKDF (info field for purpose).

### 1.3 Shamir 3-of-5 (optional)

Split master into 5 shares, recover with any 3.

- Field: GF(2^8)
- Polynomial degree: 4 (k-1 where k=5)
- Threshold: 3

## 2. Vault

### 2.1 Layout

```
~/.sisoul/
├── dna.json             — vault DNA + version
├── did_key.json         — public did + (encrypted) private key
├── seed.txt             — BIP-39 mnemonic (chmod 600)
├── petnames.json        — {did:key:z6Mk... → local nickname}
├── friends/<id>.json    — friend records
├── cases/<id>.json      — v2 case records
├── lessons/<id>.json    — v2 distilled lessons
├── growth/<date>.json   — daily snapshots
└── chat/sessions/<peer-did>.json  — SecretBox-encrypted ratchet state
```

### 2.2 Encryption

- Vault file encryption: XChaCha20-Poly1305 (libsodium SecretBox)
- Key derivation: HKDF-SHA256(master, info="vault" | "chat" | etc.)
- Optional: ML-KEM-1024 hybrid (post-quantum, recommended)

## 3. P2P Transport

### 3.1 Stack

- **kubo** (go-ipfs) embedded as subprocess. Default bootstrap: IPFS Foundation public peers (9) + Cloudflare.
- **GossipSub** v1.1 (libp2p pubsub) for message broadcast.
- **Circuit Relay v2** for NAT traversal fallback.
- **AutoNAT + DCUtR** for hole-punching.

### 3.2 GossipSub topics

| Purpose | Topic format |
|---|---|
| Friend record sync | `/sisoul/friend/v1/{sha256(my_did)[:16]}` |
| Chat (Alice ↔ Bob) | `/sisoul/chat/v1/{sha256(min(a,b) ":" max(a,b))[:16]}` |
| MLS group chat (>2) | `/sisoul/mls/v1/{sha256(group_id)[:16]}` (RFC 9420, see §6.4) |
| Pre-key bundle announce | `/sisoul/prekey/v1/{sha256(did)[:16]}` |
| Case broadcast | `/sisoul/case/v1` (alpha: opt-in friend circle) |
| Skill announce | `/sisoul/skill/announce/v1` |

All payloads E2E encrypted via PQXDH-derived shared secret (per pair).

## 4. Friend Record

```json
{
  "did": "did:key:z6MkBob...",
  "petname": "Bob",
  "multiaddr": "/ip4/192.0.2.10/tcp/4001/p2p/12D3KooWBob...",
  "kubo_peer_id": "12D3KooWBob...",
  "added_at": "2026-06-04T00:00:00Z",
  "version": 2
}
```

Schema v2 (current): adds `kubo_peer_id` for direct swarm connect.

## 5. Borrow LLM (Alice → Bob's API key)

### 5.1 Request payload (Alice → Bob via P2P)

```json
{
  "type": "borrow_request",
  "request_id": "uuid-v4",
  "from_did": "did:key:z6MkAlice...",
  "to_did": "did:key:z6MkBob...",
  "model": "claude-opus-4-7",
  "messages": [{"role": "user", "content": "..."}],
  "max_tokens": 4096,
  "encrypted_with": "pqxdh-shared-key"
}
```

Sent via `/sisoul/borrow/v1/{bob_did_hash}` GossipSub topic, encrypted with PQXDH shared secret.

### 5.2 Bob's daemon

1. Decrypt with shared secret
2. Check `borrow_policy.json` (auto-approve / prompt / deny)
3. Forward to Bob's provider (Anthropic / OpenAI / etc.)
4. Encrypt response + return via reply channel

### 5.3 Response

```json
{
  "type": "borrow_response",
  "request_id": "uuid-v4",
  "from_did": "did:key:z6MkBob",
  "content": "...",
  "usage": {"prompt_tokens": 11, "completion_tokens": 20},
  "encrypted_with": "pqxdh-shared-key"
}
```

## 6. Chat (Signal-grade)

### 6.1 PQXDH handshake

Hybrid post-quantum: X25519 ECDH || ML-KEM-1024 KEM.

```
shared_secret = HKDF-SHA512(
    ecdh_shared(my_x25519_sk, peer_x25519_pk) ||
    kem_decapsulate(my_mlkem_sk, peer_mlkem_ct),
    info="sisoul-pqxdh-v1"
)
```

Pre-key bundle published periodically (24h refresh) to `/sisoul/prekey/v1/{did}` topic.

### 6.2 Double Ratchet

[Open Whisper Systems spec](https://signal.org/docs/specifications/doubleratchet/).

- Root key: HKDF(shared_secret, DH outputs)
- Sending/receiving chain keys: HKDF(root, DH)
- Per-message key: HKDF(chain) → AES-256-HMAC-SHA256 encrypt
- Ratchet step on every DH key exchange

Forward secrecy + post-compromise security.

### 6.3 Wire format

Each chat message:

```json
{
  "header": {
    "dh_pub": "base64...",
    "n": message_index,
    "pn": prev_chain_length
  },
  "ciphertext": "base64...(AES-256-CBC + HMAC-SHA256)"
}
```

Sent via GossipSub on `/sisoul/chat/v1/{pair_hash}` topic.

### 6.4 MLS Group Chat (>2 participants · RFC 9420)

For chats with more than two participants, sisoul uses **MLS — Messaging Layer
Security (RFC 9420)** instead of the pairwise Double Ratchet. MLS is a
tree-based group key agreement that scales to 1000+ members with one ratchet,
rather than O(n) pairwise Double Ratchet sessions.

**Epoch model.** The group lives in a sequence of *epochs*. Every membership
change (add / remove) produces a **Commit** that re-keys the group to a fresh
`epoch_secret` and increments the epoch counter (`ratchet_epoch()`):

- **Forward secrecy on join** — a member added at epoch *N* receives only the
  epoch-*N* secret (via its Welcome) and cannot decrypt epochs `< N`.
- **Forward secrecy on removal** — a removed member is excluded from the re-key
  recipient set and cannot derive any epoch `> N`.
- **Post-compromise security** — epoch secrets are independent, so compromise of
  one epoch reveals neither earlier nor later epochs.

**Key schedule.** Application messages use AES-128-GCM under a key derived from
the epoch's `encryption_secret` via `ExpandWithLabel` (RFC 9420 §8), keyed by
the sender's leaf index and a per-sender generation counter. The AEAD AAD binds
`group_id ‖ epoch ‖ sender ‖ generation`, so cross-epoch / cross-sender / replay
forgeries fail authentication.

**Wire format (RFC 9420 §6).** TLS presentation language with the §2.1.2
variable-length integer (QUIC-style 2-bit prefix) for vector lengths:

```
MLSMessage   { uint16 version=0x0001; uint16 wire_format; opaque body<V>; }
wire_format  ∈ { public_message(1), private_message(2), welcome(3),
                 group_info(4), key_package(5) }
FramedContent{ opaque group_id<V>; uint64 epoch; opaque sender<V>;
               uint8 content_type; uint32 generation;
               opaque authenticated_data<V>; opaque content<V>; }
content_type ∈ { application(1), proposal(2), commit(3) }
Welcome      { uint16 cipher_suite; opaque group_id<V>; uint64 epoch;
               opaque members<V>; opaque encrypted_group_secrets<V>; }
```

Group secrets in Commit / Welcome are distributed with an HPKE-style seal
(X25519 + HKDF-SHA256 + AES-128-GCM) to each recipient's identity key, mirroring
RFC 9420 TreeKEM path-secret encryption.

**Topic schema.** Each group maps to a single GossipSub topic carrying both
handshake (Commit / Welcome) and application `MLSMessage` bytes:

| Stream | Topic |
|---|---|
| MLS group | `/sisoul/mls/v1/{sha256(group_id)[:16]}` |

> **Skeleton note.** The reference implementation (`sisoul.chat.mls`) implements
> the RFC 9420 wire format and epoch/key-schedule semantics in pure Python; it
> does not yet perform full TreeKEM HPKE path encryption or KeyPackage / X.509
> credential validation. Identity keypairs are derived deterministically from
> the DID for testability and must be replaced by real per-device KeyPackage keys
> in production. The wire shape is RFC-faithful so the swap to a vetted MLS stack
> (OpenMLS / mlspp) is mechanical.

## 7. EAS Attestation (Optional, v2.0+)

Schema name: `sisoul.provenance.v1`

```solidity
struct ProvenanceAttest {
    string response_id;
    bytes32 query_hash;
    address[] cited_authors;
    uint256 timestamp;
}
```

Submitted to EAS contract on Optimism L2.

## 8. Skill Manifest

```json
{
  "name": "rust-async-expert",
  "version": "0.1.0",
  "entry": "main.py",
  "runtime": "python|node|rust|wasm",
  "ipfs_cid": "bafy...",
  "author_did": "did:key:z6MkBob",
  "sigstore_sig": "base64...",
  "sigstore_cert": "base64 (X.509 cert from Fulcio)",
  "description": "...",
  "sis_price_per_call": 0.0,
  "sha256": "..."
}
```

## 9. Versioning

All wire formats include `version: int` field. Backward compatibility:

- v1: alpha launch (current)
- v2: beta (T+1m) — group chat (MLS), Sepolia DAO
- v3: v1.0 stable (T+6m) — Optimism mainnet, mainnet EAS
- v4: v2.0 智能体网络 — LoRA federated, ChromaDB case
- v5: v3.0 超级智能体 — Multi-Agent Debate, SIS micropay

Implementations should advertise supported versions in did record.

## 10. Conformance test

A conforming implementation should:

- [ ] Generate did:key with Ed25519
- [ ] Connect to ≥3 IPFS public bootstrap nodes
- [ ] Send + receive via GossipSub on `/sisoul/test/v1/{did}` topic
- [ ] Encrypt/decrypt with PQXDH handshake (real ML-KEM-1024)
- [ ] Double Ratchet 10-message round-trip (forward secrecy verified)
- [ ] Sign + verify cosign sigstore release
- [ ] Borrow LLM request → response roundtrip (with mock provider)
- [ ] EAS attest mock + verify

## 11. Reference implementation

`github.com/sisoul/sisoul` (Python, this repo). License: Apache-2.0.

Use as compliance reference + interoperability test target.

---

🤖 Spec v1.0-alpha frozen for backward compat. Breaking changes go to v2.
