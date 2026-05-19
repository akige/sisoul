---
pip: 4
title: P2P Encrypted Proxy, Three-Tier Authorization, and Friend Lending
author: Sisoul Working Group
status: Draft
type: Standards Track
category: Core
created: 2026-05-19
requires: PIP-001, PIP-002
replaces: (none)
discussions-to: https://github.com/sisoul/pips/discussions/4
---

# PIP-004: P2P Encrypted Proxy with Three-Tier Authorization

## Abstract

This PIP specifies the friend-and-skill sharing layer of Sisoul. It
defines:

1. The end-to-end encrypted peer-to-peer channel between two Sisoul
   daemons (libsodium `crypto_box`, i.e. X25519 + XChaCha20-Poly1305
   via `crypto_box_xchacha20poly1305`).
2. The three-tier authorization model
   (`strong-tie-auto` / `per-request` / `emergency-only`) that gates
   every cross-soul interaction.
3. The five-layer anti-abuse defense (monthly cap, rate limit,
   revoke, on-chain reputation, daemon security scan).
4. The LLM-quota-sharing protocol: friend A's daemon proxies a
   bounded number of LLM tokens through friend B's account, with
   honest accounting on both sides.
5. The AI-skill-sharing lifecycle: 30-minute scratch use with
   guaranteed wipe; signed skill packages; IPFS distribution.
6. The mutual ledger on-chain attestation, published to Ethereum
   Attestation Service (EAS) on Optimism Sepolia, that records the
   *fact* of a permission grant/use/revoke without leaking the
   underlying content.

Reference implementation: `src/sisoul/friend/` and
`src/sisoul/daemon_routes/{friend,proxy,permissions,p2p}.py`.

## Motivation

Sisoul's vault gives a user durable, vendor-neutral state. The next
question is: what *between* users? Users naturally want to share with
the people they trust: "borrow Alice's coding-style preferences",
"let Bob route a few prompts through my Claude quota when his is
exhausted", "give my mom emergency-only access if I'm unreachable".

Three things make this hard:

1. **Confidentiality**: cross-soul traffic must be end-to-end
   encrypted; a packet sniffer (or even a server operator) must learn
   nothing of payload content.
2. **Consent granularity**: not all friends should have equal access;
   the spouse / sibling / "online acquaintance" trust tiers map to
   different defaults.
3. **Abuse resistance**: any system that lets friend A spend friend
   B's LLM credits is a system that, by default, friend A will abuse
   either by mistake or by malice; without throttles the model
   collapses.

This PIP designs all three together and rejects the "trust transitive"
mistake (do not give Carol access just because Bob does). Every
delegation is direct, scoped, time-bounded, and revocable.

Non-goals:

* Anonymity at the network layer (the P2P transport is plain
  Tailscale or QUIC over the open Internet; users who want
  anonymity should layer Tor underneath).
* Cross-protocol federation (e.g. Matrix bridge); deferred.
* Group sharing semantics beyond "everyone I tier as strong-tie
  gets the same default"; full group ACLs deferred.

## Specification

### 1. Identity

Each soul (PIP-002 §3) has a single canonical signing keypair derived
deterministically from `soul_root`:

```
sign_seed = HKDF_SHA256(
    ikm  = soul_root,
    salt = b"sisoul-sign-v1",
    info = b"ed25519",
    L    = 32,
)
(sign_sk, sign_pk) = Ed25519_seed_keypair(sign_seed)
```

And a separate encryption keypair for box channels:

```
enc_seed = HKDF_SHA256(
    ikm  = soul_root,
    salt = b"sisoul-enc-v1",
    info = b"x25519",
    L    = 32,
)
(enc_sk, enc_pk) = X25519_keypair_from_seed(enc_seed)
```

Domain separation between signing and encryption keys is mandatory; do
not reuse one key for both. Both keys are derived from `soul_root`,
which itself is derived from the mnemonic (PIP-002 §3), so the soul's
identity is fully recoverable from the mnemonic.

Public keys (`sign_pk`, `enc_pk`) are published as part of the
`did:sisoul:...` resolution (see §2). Private keys live in daemon
memory only.

### 2. DID resolution

`did:sisoul:<base58btc>` resolves to a DID Document via one of:

1. **Local cache** in `~/.sisoul/state/did-cache/`.
2. **Friend's daemon**, if a peer relationship exists, over the box
   channel established in §4.
3. **Ethereum Attestation Service** (Optimism Sepolia), where each
   soul MAY publish its DID Document as a signed attestation at first
   broadcast (§11). Resolution is permissionless and cacheable.

The DID Document is small:

```json
{
  "id": "did:sisoul:5DGmf2yz...",
  "verificationMethod": [
    {
      "id": "did:sisoul:5DGmf2yz...#sign-1",
      "type": "Ed25519VerificationKey2020",
      "publicKeyMultibase": "z..."
    },
    {
      "id": "did:sisoul:5DGmf2yz...#enc-1",
      "type": "X25519KeyAgreementKey2020",
      "publicKeyMultibase": "z..."
    }
  ],
  "service": [
    {
      "id": "#sisoul-proxy",
      "type": "SisoulProxy",
      "serviceEndpoint": "tailscale://...|quic://host:port"
    }
  ]
}
```

The document is signed by `sign_sk` and the signature MUST verify
against `sign_pk`, which is itself attested on-chain or fetched from
a previously-trusted peer.

### 3. Adding a friend

Two souls become friends through an out-of-band introduction:

```
Step 1 (Alice, in her daemon UI):
  Alice runs `sisoul friend invite`
  Daemon prints a 1-line invite token:
    sisoul-invite://5DGmf2yz...?nonce=<32-hex>&exp=2026-05-20T07:00:00Z&sig=...

Step 2 (out of band):
  Alice sends the token to Bob via any channel they already trust
  (Signal, in person, etc.). Sisoul does not endorse any channel.

Step 3 (Bob, in his daemon):
  Bob runs `sisoul friend accept <invite-token>`
  Daemon:
    - parses the token
    - resolves Alice's DID Document
    - generates a fresh ephemeral nonce
    - opens a box channel to Alice's daemon (§4)
    - sends a `FRIEND_ACK { bob_did, ephemeral_nonce, sig }`

Step 4 (Alice):
  Alice's daemon receives FRIEND_ACK, verifies signature, prompts
  Alice in UI: "Bob (did:sisoul:abc...) accepted your invite. Set
  relationship tier:" [ strong-tie / weak-tie / acquaintance ]

Step 5 (both):
  Each side writes the friend record into PIP-001 §5.5 `friends.enc`.
  An attestation is published on-chain (§11) recording the directed
  edge Alice→Bob with the chosen tier.
```

An invite token is one-shot, expires within 24 h, and is bound to the
inviter's signing key. A re-used token MUST be rejected by the
inviter's daemon.

### 4. Box channel

When two daemons need to talk, they open a `crypto_box` channel:

```
Initiator A:
  - looks up B's enc_pk from DID
  - generates ephemeral X25519 keypair (e_sk_A, e_pk_A)
  - derives shared = X25519(e_sk_A, B.enc_pk)
  - session_key = HKDF_SHA256(shared, salt=b"sisoul-box-v1",
                              info=did_A || did_B)
  - opens transport (Tailscale stream or QUIC datagram)
  - sends FRAME_INIT { e_pk_A, did_A, sig_A }
       where sig_A = Sign(sign_sk_A, e_pk_A || did_A || did_B || nonce0)

Responder B:
  - verifies sig_A against A's sign_pk
  - generates ephemeral (e_sk_B, e_pk_B)
  - completes ECDH; derives the same session_key (using A's e_pk_A)
  - sends FRAME_INIT_ACK { e_pk_B, did_B, sig_B }
  - PFS: session_key is used; static enc_pk is NOT used for content
        encryption (only for key agreement bootstrap)

Both:
  - subsequent frames: SecretBox(payload, fresh_24_byte_nonce, session_key)
  - frame nonces are 24-byte random per frame
  - session terminates on idle 5 min OR explicit FRAME_BYE
```

Forward secrecy: ephemeral keys discarded at session end.
Replay protection: each frame's nonce is verified against a sliding
window (size 256) per session.

The transport is intentionally pluggable. The reference daemon ships
with three transports:

* **Tailscale**: preferred when both peers are on the same tailnet
  (instant, zero-config NAT traversal).
* **QUIC over UDP**: when peers have public addresses or a STUN-aided
  hole-punching succeeds.
* **Relay**: a Sisoul relay service (operated by anyone, including
  the user themselves) that forwards opaque session frames. The
  relay sees only ciphertext.

### 5. Three-tier authorization

Every cross-soul *action* (call this an "ask") is one of: `llm-proxy`,
`skill-share`, `vault-read-class`, `vault-write-class`, `notify`,
`emergency-info`. Each ask is gated by one of three tiers, configured
per friend in `friends.enc` (PIP-001 §5.5):

#### 5.1 Tier: strong-tie-auto

The friend's daemon answers the ask automatically without prompting
the user, subject to anti-abuse limits (§6).

Default for relationships explicitly tagged `strong-tie` (spouse,
close family, business partner).

#### 5.2 Tier: per-request

The friend's daemon notifies the user (system notification + PWA
queue) and the ask sits in a queue until the user approves or denies.
Asks expire after 24 h if not answered. Default for `weak-tie`.

#### 5.3 Tier: emergency-only

The ask is rejected outright UNLESS the requester provides a valid
"emergency token" (§8). Default for `acquaintance` and the implicit
default for any unspecified friend.

#### 5.4 Per-ask override

The friend MAY downgrade or upgrade an ask's effective tier in their
config, e.g. "Bob has strong-tie-auto for `notify` but only
per-request for `llm-proxy`". The configuration is stored as the
`permissions:` dict on the friend record (PIP-001 §5.5).

### 6. Five-layer anti-abuse

#### 6.1 Layer 1: Monthly cap

Each `(asker_did, action_kind)` pair has a monthly quota the giver's
daemon enforces locally. Defaults:

| action_kind        | monthly cap (per asker)       |
|--------------------|-------------------------------|
| llm-proxy          | 50,000 tokens                 |
| skill-share        | 10 skill borrows              |
| vault-read-class   | 100 reads                     |
| notify             | 200 notifications             |
| emergency-info     | 1 (per emergency event)       |

The giver's daemon decrements the counter atomically; on cap exceeded
the response is `429 QuotaExhausted` with a `retry-after` of the next
month boundary.

#### 6.2 Layer 2: Rate limit

Token-bucket per `(asker_did, action_kind)`. Defaults:

| action_kind   | rate            | burst |
|---------------|-----------------|-------|
| llm-proxy     | 1 req / 10 s    | 5     |
| skill-share   | 1 borrow / hour | 1     |
| notify        | 5 / minute      | 10    |

Exceeded → `429 RateLimited` with `retry-after` from the bucket.

#### 6.3 Layer 3: Revoke (instant)

The giver MAY revoke any permission at any time. Revocation is
immediate: the next ask returns `403 Revoked` and an on-chain
revocation attestation is published (§11). The revocation does not
roll back already-served asks; the friend cannot un-spend used quota.

#### 6.4 Layer 4: On-chain reputation

For each ask, both sides publish a compact attestation to EAS (§11).
Givers' attestations include a per-friend "satisfaction" score (1-5)
the giver MAY set when the asker has been particularly well- or
ill-behaved. Public, queryable, append-only.

Bad actors accumulate low scores; new prospective givers can
filter their accept list by reputation. The protocol does NOT
algorithmically gate on reputation (no global blocklist); each user's
daemon decides locally how to weigh on-chain signals.

#### 6.5 Layer 5: Daemon security scan

The giver's daemon scans each incoming ask payload for: malware
signatures in skill packages (ClamAV optional), shell-escape strings
in skill `entry_point` fields (no `bash -c`, no `eval`, no `os.system`
on import), and oversize payloads (> 16 MiB hard reject).

This is best-effort, defense in depth; it does not replace skill
sandboxing (§7.3).

### 7. AI skill sharing

#### 7.1 Skill package format

A skill is a signed tarball:

```
skill-<name>-<version>.tar.zst
├── manifest.json
│   {
│     "schema": 1,
│     "name": "vim-power-user",
│     "version": "1.0.0",
│     "author_did": "did:sisoul:...",
│     "author_sig": "<base58 ed25519 sig over the canonical bytes>",
│     "entry_point": "prompt-fragment",   // | "shell-tool" | "mcp-server"
│     "files": ["prompt.md", "..."],
│     "permissions": ["read-clipboard"],
│     "expires": "2026-12-31T00:00:00Z"
│   }
├── prompt.md
├── ...
```

The package CID (IPFS) is the immutable handle. The package is
content-addressed; integrity is enforced by the CID; authorship is
enforced by `author_sig` over the canonical manifest bytes.

#### 7.2 Borrow flow

```
Alice wants Bob's "vim-power-user" skill:

1. Alice's daemon → Bob's daemon: PROXY_REQ skill-share
     { skill_id, alice_did, asked_ttl_min=30 }

2. Bob's daemon checks (a) tier permission, (b) anti-abuse limits.

3. If allowed, Bob's daemon issues a signed grant:
     SkillGrant {
       skill_cid: "ipfs://bafy...",
       grantee_did: "did:sisoul:alice...",
       expires: 2026-05-19T07:30:00Z,    // exactly 30 min ahead
       grant_sig: Sign(bob.sign_sk, ...)
     }

4. Alice's daemon:
   a. Fetches the package from IPFS (or from Bob via box channel).
   b. Verifies skill_cid matches package contents.
   c. Verifies author_sig.
   d. Creates a scratch directory ~/.sisoul/scratch/<grant_id>/
      with mode 0700.
   e. Unpacks the package there.
   f. Registers a scheduled wipe at expires + 60 s grace.

5. Alice's AI tools (via PIP-003 ephemeral-context hook) see the
   skill's prompt-fragment for the 30-minute window. After expiry:
   - Scheduled wipe deletes the scratch directory.
   - PIP-003 ephemeral-context auto-removes the fragment.
   - An on-chain `SkillReturned` attestation is published.
```

#### 7.3 Skill sandboxing

`entry_point: prompt-fragment` skills are pure text and run inside the
LLM context only. They cannot execute code on the borrower's machine.

`entry_point: shell-tool` skills package a shell tool the AI may
invoke. The borrower's daemon runs the tool inside a sandbox:

* Linux: `bubblewrap` with read-only bind of `/usr`, isolated
  `/tmp`, no network unless `permissions: ["network"]` is granted
  by the borrower (separate from the skill's own permissions).
* macOS: `sandbox-exec` profile.
* Windows: AppContainer.

`entry_point: mcp-server` skills follow the same sandbox rules and
expose an MCP endpoint at a per-grant Unix socket.

#### 7.4 Lifecycle invariants

* **Atomic wipe**: scratch directory deletion is recursive and
  ignores busy-file errors after a 5-second retry loop; if still
  busy, the daemon SHALL log and continue, and a startup pass on next
  daemon launch SHALL retry.
* **No re-use**: after expiry, the same grant_id cannot be revived;
  a new borrow requires a new ask.
* **Borrower honesty**: a malicious borrower can in principle copy
  the unpacked skill bytes off-box before expiry. The protocol
  cannot prevent that. The on-chain `SkillReturned` is therefore an
  *attestation*, not a *guarantee*; an unreturned skill produces a
  visible negative mark on the borrower's on-chain reputation.

### 8. LLM proxy

#### 8.1 Use case

Alice's LLM quota is exhausted; she has Bob as a `strong-tie-auto`
friend who has surplus quota. Alice routes a prompt through Bob.

#### 8.2 Wire

```
Alice's daemon → Bob's daemon (over §4 box channel):
  LLM_PROXY_REQ {
    model: "anthropic/claude-opus-4-7",
    messages: [...],
    max_tokens: 4000,
    request_id: <uuid>
  }

Bob's daemon:
  - check tier, quota, rate-limit
  - if allowed, call its own configured LLM provider with Bob's key
  - stream response back to Alice over the same channel as
    LLM_PROXY_CHUNK / LLM_PROXY_DONE frames

Both daemons log:
  - Alice records "borrowed N tokens from Bob"
  - Bob records "lent N tokens to Alice"
  - An on-chain LedgerEntry is published per request (batched per
    hour for cost reasons; see §11.2)
```

The response stream is forwarded **without modification**; Bob's
daemon does not store the prompt or response content beyond a
SHA-256 fingerprint for accounting. The prompt is, however,
necessarily visible to Bob's daemon process for the duration of the
call; the protocol does NOT promise prompt-confidentiality from the
giver. Users SHOULD only proxy through friends they're willing to
share the prompt content with.

#### 8.3 Honest accounting

Both sides keep a running tally in `friends.enc` body:

```markdown
### did:sisoul:bob...
- 2026-05-15: borrowed 12,400 tokens (claude-opus-4-7)
- 2026-05-17: borrowed 8,200 tokens
- balance: -20,600 tokens owed to Bob
```

Settlement is out of protocol; users may reciprocate in kind, send
fiat, or simply accept the imbalance.

### 9. Compromise notification

If a user runs `sisoul rotate` (PIP-002 §9) under suspicion of
mnemonic compromise, the daemon SHALL:

1. Publish a `SoulCompromised { old_soul_id, timestamp }` attestation
   on-chain.
2. Box-channel notify every friend listed in `friends.enc` with the
   same payload, signed by the *new* signing key (which Alice's
   friends can re-derive from the on-chain successor record if they
   trust it, or out-of-band).

Friends' daemons receive the notification and surface it to the user
with the recommendation to revoke any permissions previously granted
to the old soul_id. The protocol does not automatically revoke; the
user retains the choice (perhaps the user *is* the compromised
party's spouse and wants to keep the trust relationship as the new
soul).

### 10. Emergency-only

The `emergency-only` tier requires the requester to supply a fresh
emergency token. There are two issuance modes:

1. **Self-signed dead-man token**: Alice configures, on her own
   daemon, that her `did:sisoul:eldest-son` is allowed to issue
   emergency-info requests if Alice's daemon has not been seen alive
   for ≥ 7 days. Her daemon pre-signs a long-lived token under that
   condition; the son's daemon presents the token to Alice's
   friends to gather information (e.g. "where did Mom save her
   medical-directive doc?").

2. **Third-party attested**: a notary, doctor, or family ombudsman
   (any party Alice trusts and has pre-registered) signs an
   emergency declaration; the requester presents this to friends who
   recognize that notary's DID.

Either way the friend's daemon logs the emergency-info disclosure
on-chain as a transparent attestation. Abuse of emergency tokens is
deterred by the resulting on-chain reputation hit.

### 11. On-chain ledger (EAS, Optimism Sepolia)

#### 11.1 Why EAS?

Ethereum Attestation Service provides a permissionless, schema'd,
queryable, low-cost attestation primitive on a low-fee L2. Sisoul
uses it to record the *fact* of permission events without leaking the
content:

* DID document publication (one-time).
* Friend edge addition / tier change / removal.
* Permission grant / revoke / use (batched).
* Skill borrow / return.
* Reputation score (giver → asker).
* Soul compromise notice.

Sepolia is the v1 default for cost reasons (it is testnet, but
adequate for protocol introduction). A future PIP will graduate to
mainnet when economics permit.

#### 11.2 Batching

Per-request on-chain writes would be cost-prohibitive. The daemon
batches non-critical events (proxy usage, ledger entries) hourly:
the batch is a Merkle root committed on-chain, with the leaves stored
locally and shareable on demand. The Merkle root suffices to prove
any individual leaf's inclusion without revealing the others.

Critical events (revoke, compromise notice) bypass batching and are
written immediately.

#### 11.3 Schemas

EAS schemas published by Sisoul (UIDs pinned in
`src/sisoul/onchain/schemas.py`):

```
SisoulDidDocument:
  bytes32 did_hash, bytes pubkey_sign, bytes pubkey_enc, string endpoint

SisoulFriendEdge:
  bytes32 from_did, bytes32 to_did, uint8 tier, uint64 since

SisoulPermissionGrant:
  bytes32 from_did, bytes32 to_did, string action, uint64 expires

SisoulPermissionRevoke:
  bytes32 from_did, bytes32 to_did, string action, uint64 ts

SisoulLedgerBatch:
  bytes32 from_did, bytes32 merkle_root, uint32 count, uint64 ts

SisoulReputation:
  bytes32 from_did, bytes32 to_did, uint8 score_1_5, string note

SisoulCompromise:
  bytes32 old_did, bytes32 new_did_or_zero, uint64 ts
```

All schemas are content-addressed; the deployed UIDs are stable.

#### 11.4 Privacy considerations

Attestations are public. Sisoul mitigates by:

* Using `keccak(did_string)` rather than the DID string itself in
  schema fields where possible, so casual blockchain explorers do not
  index by DID.
* Recording only counts and merkle roots in `LedgerBatch`, never
  per-request action data.
* NEVER putting vault content, prompt content, or response content
  on chain.

Users who do not want any on-chain footprint MAY disable the
on-chain layer in `daemon.toml` (`onchain.enabled = false`). The
P2P protocol functions without it; the cost is loss of the
public reputation surface and tamper-evident audit trail.

## Rationale

### Why libsodium `crypto_box` (X25519 + XChaCha20-Poly1305)?

* Mature, audited, side-channel-resistant primitive.
* Same library as PIP-001's SecretBox; one cryptographic dependency.
* 24-byte nonces remove nonce-counter fragility for streaming
  protocols (which is the friend-LLM-proxy case).
* The static-key-agreement → ephemeral-session pattern is the same
  one used by Signal's X3DH; we deliberately stop short of the
  full Double Ratchet because friend interactions are typically
  short-lived sessions, not long-lived conversations.

### Why three tiers and not a permission matrix?

User research during the internal beta showed configurable matrices
overwhelmed users. Three named tiers map cleanly onto the social
reality (close-family / friend / acquaintance) and the per-ask
override (§5.4) gives any user who wants more granularity an
escape hatch. We expect 90%+ of friend records to use defaults.

### Why an on-chain ledger at all?

The honest answer: most users do not need it. The case for putting it
in the v1 protocol:

* It is *opt-out* (`onchain.enabled = false` disables it).
* For the users who do need it (semi-public personalities with
  many weak-tie permissions; communities where reputation matters),
  the alternative — running a private server — reintroduces a trust
  anchor that defeats the rest of the design.
* EAS on Optimism Sepolia is cheap enough that the cost is
  immaterial in the v1 timeframe.

A future PIP may move to a non-blockchain consensus mechanism (e.g.
a Sisoul-specific gossip-based append-only log) if the on-chain
trade-offs become uncomfortable.

### Why a 30-minute skill TTL?

It is the shortest window that supports realistic use cases (a coding
session with a borrowed prompt fragment, a paper-writing session
with a borrowed style guide) while still keeping the "lend, don't
sell" model intact. Longer windows blur into permanent transfer and
encourage hoarding. The TTL is a soft default; the giver and asker
can negotiate up to 24 h at grant time, and on-chain attestations
record the agreed-upon TTL.

### Why a Merkle-batched ledger?

Per-request on-chain writes would cost roughly one Sepolia tx per
LLM call — even on L2 this is wasteful for a daemon that may make
hundreds of proxied calls per day. Batched Merkle roots give the
same auditability properties for ~1/100th the gas cost. The leaves
are stored in `~/.sisoul/state/ledger/<from-did>/<merkle-root>.json`
and can be shared with any party requesting proof of inclusion.

### Why no group sharing in v1?

Because group ACLs are where most permission systems lose their
souls (no pun intended). Once we add groups, every action's
"who is the asker" question becomes "what role is the asker in
which group", and the daemon's UX collapses under the choice. v1
keeps direct edges and defers group semantics until we have a
proven user need.

## Backwards Compatibility

There is no prior public Sisoul friend protocol. Internal-1.0 had a
prototype "buddy" link with two-tier permissions and no on-chain
ledger; the migrator at first launch of v1 daemon translates
buddy records into `friends.enc` entries with tier `weak-tie` and
prompts the user to upgrade any to `strong-tie`.

## Test Cases

### TV-1: DID derivation determinism

Given a fixed mnemonic, the derived `sign_pk` and `enc_pk` MUST match
the values in `tests/pip004/did-vectors.json`.

### TV-2: Box-channel round trip

Two test daemons (Alice, Bob) with fixed mnemonics establish a box
channel over a loopback transport, exchange 10 frames of 64 KiB
random payload, and verify byte-equality at the receiver.

### TV-3: Replay rejection

A captured frame from session N MUST be rejected when replayed at
session N+1 (different session_key) and within the sliding window of
session N (already-seen nonce).

### TV-4: Tier enforcement

`emergency-only` tier MUST return `403 Forbidden` for any ask that
does not carry a valid emergency token. `per-request` MUST queue and
not auto-answer. `strong-tie-auto` MUST auto-answer subject to
anti-abuse layers.

### TV-5: Monthly cap

After 50,000 tokens proxied in a calendar month, the next
`llm-proxy` ask from the same asker MUST return `429 QuotaExhausted`
with `retry-after` ≥ time to next month boundary.

### TV-6: Skill wipe

After a 30-minute skill grant expires, the scratch directory MUST be
absent and the on-chain `SkillReturned` attestation MUST have been
published. A `SIGKILL` on the daemon between expiry and wipe MUST
not prevent the wipe: the daemon's startup pass MUST detect and
delete any orphan scratch directories whose `grant_id`'s expiry has
passed.

### TV-7: Revocation immediacy

After a revoke, the next ask from that friend MUST return
`403 Revoked` within 100 ms; the asker's daemon MUST update its
local state to mark the permission revoked even before the on-chain
revoke attestation finalizes.

### TV-8: Ledger inclusion proof

A daemon that has published 1,000 ledger entries in a batch
(merkle root on-chain) MUST be able to produce a Merkle proof of
any individual entry's inclusion that verifies offline against the
on-chain root.

### TV-9: Compromise notification

When `sisoul rotate` runs, every friend's daemon MUST receive a
`SoulCompromised` notification signed by the *new* signing key
within 60 s (assuming reachability), and the friend's UI MUST surface
a "review permissions granted to old soul_id" affordance.

### TV-10: Emergency token validation

A dead-man emergency token presented before the dead-man condition
is met MUST be rejected with `403 EmergencyConditionNotMet`. After
the condition is met, the same token MUST validate and the resulting
emergency-info disclosure MUST be recorded on-chain.

## Reference Implementation

```
src/sisoul/friend/
├── __init__.py
├── relationship.py        # tier model, friends.enc CRUD
├── permissions.py         # per-friend permission map
├── borrow.py              # outbound asks
├── lend.py                # inbound asks, anti-abuse layers 1-2
├── anti_abuse.py          # quota & rate limit primitives
├── encrypted_proxy.py     # the box channel + LLM proxy
├── proxy_audit.py         # SHA-256 fingerprint accounting
├── skill_package.py       # tarball format, sign/verify
├── skill_ipfs.py          # IPFS publish / fetch / pin
└── skill_borrow.py        # 30-min lifecycle + wipe

src/sisoul/p2p/
├── __init__.py
├── transport_tailscale.py
├── transport_quic.py
└── transport_relay.py

src/sisoul/onchain/
├── __init__.py
├── eas.py                 # EAS client (Optimism Sepolia)
├── schemas.py             # schema UIDs
└── merkle.py              # batch root + inclusion proofs

src/sisoul/daemon_routes/
├── friend.py              # /v1/friend/* HTTP endpoints
├── proxy.py               # /v1/proxy/* HTTP endpoints
├── permissions.py         # /v1/permissions/* HTTP endpoints
├── p2p.py                 # transport dispatcher
├── skill.py               # /v1/skill/* HTTP endpoints
└── attest.py              # /v1/attest/* HTTP endpoints (on-chain UI)
```

## Security Considerations

### Threat model

In-scope adversaries:

1. **Malicious friend**: a peer the user has granted some permission
   who exceeds their authority.
2. **Eavesdropper**: an attacker on the network between two daemons.
3. **Compromised relay**: an attacker operating the relay used for
   transport (§4).
4. **Sybil attacker**: an attacker who creates many fake souls to
   game on-chain reputation.
5. **Bad-faith skill author**: a peer who lends a skill containing a
   shell exploit.

Out of scope:

* Compromised endpoint daemon (covered by PIP-001 / PIP-002 mnemonic
  threat model).
* Quantum adversary against curve25519 (acknowledged; PQ migration
  is a future PIP).
* Coercion of either party to reveal the session contents (no
  forward-secrecy guarantee survives endpoint coercion).

### What this layer protects

* **Confidentiality on the wire**: box channel + AEAD frames; relay
  sees only ciphertext.
* **Forward secrecy**: ephemeral session keys discarded at session
  end.
* **Authenticated identity**: every frame is bound to the soul's
  signing key.
* **Replay resistance**: nonce-sliding-window per session.
* **Permission bounds**: tiers + 5-layer anti-abuse.
* **Auditability**: on-chain ledger of permission events.

### What this layer does NOT protect

* **Prompt confidentiality from the lender**: a borrower's prompt is
  visible to the lender's daemon for the duration of the proxy call.
  Document clearly in UX.
* **Skill execution sandbox escapes**: bubblewrap / sandbox-exec /
  AppContainer are good but not perfect. Skills with `entry_point:
  shell-tool` should only be borrowed from highly-trusted peers.
* **On-chain privacy beyond hashing DIDs**: a determined adversary
  can correlate `keccak(did)` to a DID by trying candidates.
* **DoS resistance against a flood of strangers**: the daemon
  refuses incoming asks from non-friend DIDs by default, but a
  flood of friend-tier asks from a compromised friend can saturate
  the daemon's per-friend rate limit. Mitigation: revoke the friend.

### Sybil resistance

On-chain reputation alone does not stop a Sybil attacker. The
v1 mitigation is local: a new friend defaults to `acquaintance`
tier and `emergency-only` permissions; the user must explicitly
promote. The on-chain ledger surfaces the friend's history, which
a careful user can inspect before promoting. Strong Sybil resistance
(stake-weighted reputation, social-graph attestations) is left to a
future PIP.

### Skill author trust

A signed skill manifest binds the package to its author's DID. A
borrower SHOULD verify:

1. `author_sig` validates against the author's `sign_pk`.
2. The author's DID matches the lender's DID (lender re-signs at
   share time) OR is independently trusted by the borrower.
3. The package's CID matches what the manifest claims.

Without all three, the skill MUST NOT be unpacked. The reference
implementation enforces this and emits typed errors
(`SkillSignatureError`, `SkillCidMismatchError`).

### Implementation pitfalls to audit

* **Skipping signature verification under load**: never. The
  reference implementation has a regression test that crashes the
  daemon if a single sig-verify call is skipped under simulated
  high load.
* **Reusing session keys across sessions**: never. The daemon's
  session manager enforces a 1:1 session-to-key mapping with the
  ephemeral key zeroed on session end.
* **Storing decrypted skill bytes after expiry**: forbidden. Test
  TV-6 covers this; CI runs an explicit "search for grant files
  older than expiry" check in tmp dirs.
* **Leaking DID via DNS / SNI**: the QUIC transport uses an opaque
  SNI; the Tailscale transport doesn't have one.

## Appendix A: Example end-to-end LLM proxy

```
T+0    Alice's daemon receives a Claude Code prompt and detects her
       monthly Claude quota is exhausted.
T+10ms Daemon consults friends.enc: Bob is strong-tie-auto for
       llm-proxy, balance OK, rate limit OK.
T+15ms Box channel to Bob (re-use existing session if warm; else 1 RTT).
T+20ms Send LLM_PROXY_REQ.
T+25ms Bob's daemon: tier OK, monthly cap OK (37,400/50,000 used),
       rate OK (last call 30 s ago). Forward to Bob's Anthropic API.
T+800ms First streamed chunk back; Bob forwards as LLM_PROXY_CHUNK.
...
T+4500ms Final chunk; LLM_PROXY_DONE with token count = 2,800.
T+4501ms Both daemons append a ledger leaf:
         {ts, from: alice, to: bob, action: llm-proxy,
          tokens: 2800, request_id: ..., sig: ...}
T+next-hour Both daemons batch ~50 leaves into a Merkle root and
            publish to EAS.
```

## Appendix B: Friend file example

After Alice has added Bob (strong-tie) and Carol (weak-tie):

```yaml
---
class: friends
schema: 1
soul_id: did:sisoul:alice...
friends:
  - did: did:sisoul:bob...
    handle: bob
    relationship: strong-tie
    public_key: 5f3a...c91d
    added: 2026-05-19T07:30:00Z
    permissions:
      llm-proxy: strong-tie-auto
      skill-share: strong-tie-auto
      vault-read-class:
        preferences: per-request
        goals: per-request
      notify: strong-tie-auto
      emergency-info: strong-tie-auto
    quota_overrides:
      llm-proxy: 200000   # per month, overrides default 50k
  - did: did:sisoul:carol...
    handle: carol
    relationship: weak-tie
    public_key: ab12...3489
    added: 2026-05-19T08:00:00Z
    permissions: (defaults)
---
## Notes per friend

### did:sisoul:bob...
- 2026-05-15: lent me 12,400 tokens (claude-opus-4-7)
- 2026-05-17: lent me 8,200 tokens
- balance: -20,600 tokens owed to Bob

### did:sisoul:carol...
- met at the 2026-04-12 meetup
- expressed interest in borrowing my "Vim power user" skill
```

## Appendix C: Why P2P and not federated servers?

We considered a federation model (Matrix, ActivityPub) and rejected
it for the v1 timeframe. The reasons:

* Every federated server is a trust anchor. Sisoul's whole point is
  to remove trust anchors.
* The friend layer's traffic is bursty and small (notifications,
  occasional LLM proxy bursts); pure P2P over Tailscale handles this
  with ~zero infra.
* When NAT traversal fails (5–10% of network configurations), a
  relay falls back gracefully. The relay sees only ciphertext and
  can be operated by anyone, including the user.
* Federated servers would require a custodial layer for offline
  message buffering; we accept "you and your friend need to both be
  reachable" as the v1 limitation. A future PIP may add offline
  buffering via either federated relays or sealed-sender mailboxes.

## Appendix D: Numeric defaults table (canonical)

```
session.idle_timeout            = 300 s
session.frame_nonce_window      = 256 frames
session.max_frame_size          = 16 MiB
quota.llm_proxy.monthly         = 50_000 tokens
quota.skill_share.monthly       = 10 borrows
quota.vault_read.monthly        = 100 reads
quota.notify.monthly            = 200
quota.emergency_info.per_event  = 1
rate.llm_proxy                  = 1 req / 10 s, burst 5
rate.skill_share                = 1 borrow / hour, burst 1
rate.notify                     = 5 / minute, burst 10
skill.default_ttl_min           = 30
skill.max_ttl_min               = 24 * 60
invite.token_ttl_h              = 24
ledger.batch_window_s           = 3600
ledger.critical_immediate       = [revoke, compromise]
onchain.network                 = "optimism-sepolia"
```

Implementations MAY expose these as `daemon.toml` knobs but MUST
NOT change them silently across versions.

## Appendix E: Glossary

* **Soul**: see PIP-002.
* **Friend**: a soul-to-soul directed edge with tier and permissions.
* **Tier**: one of `strong-tie-auto` / `per-request` /
  `emergency-only`.
* **Ask**: a single cross-soul action (e.g. one LLM proxy call).
* **Box channel**: an ephemeral encrypted session between two
  daemons (§4).
* **Skill**: a signed package of prompt fragments / tool definitions
  shared via the borrow protocol.
* **EAS**: Ethereum Attestation Service, the schema'd attestation
  primitive Sisoul uses on Optimism Sepolia.
* **Merkle batch**: a hour-bucketed set of ledger entries whose root
  is published on-chain; leaves are stored locally and shareable.

## Copyright Waiver

Copyright and related rights for content in this document are waived
via [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/).
