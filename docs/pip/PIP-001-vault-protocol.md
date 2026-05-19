---
pip: 1
title: Sisoul Vault Protocol — libsodium SecretBox Encrypted Soul Storage
author: Sisoul Working Group
status: Draft
type: Standards Track
category: Core
created: 2026-05-18
requires: (none)
replaces: (none)
discussions-to: https://github.com/sisoul/pips/discussions/1
---

# PIP-001: Sisoul Vault Protocol

## Abstract

The Sisoul Vault Protocol defines the canonical on-disk and in-memory format
for a *soul* — a portable, end-to-end encrypted collection of long-lived
user state (preferences, goals, conversation history, friends graph, skills,
credentials) that any Sisoul-aware client (Claude CLI, Codex CLI, Cursor,
OpenCode, custom daemons) MAY read and write through the per-device Sisoul
daemon.

This document specifies:

1. The cryptographic envelope (libsodium `crypto_secretbox_xchacha20poly1305`).
2. The key-derivation chain (BIP-39 seed → PBKDF2-HMAC-SHA512 with 100,000
   rounds → 32-byte master key → per-vault sub-keys via HKDF-SHA256).
3. The on-disk directory layout (`.sisoul/vault/*.enc`).
4. The plaintext frontmatter schema for the five canonical document classes
   (`preferences`, `goals`, `chat-history`, `friends`, `skills`).
5. The atomicity guarantees that single-file read/write SHALL provide.
6. The conformance rules a *vault implementation* MUST satisfy in order to
   call itself "Sisoul-vault-1.0 compatible".

Reference implementation: `src/sisoul/vault/` in the Sisoul monorepo
(`encryption.py`, `frontmatter.py`, `storage.py`).

## Motivation

Today every AI tool that talks to the user persists state in its own private
silo: Claude CLI keeps `~/.claude/projects/.../memory/MEMORY.md`; Codex keeps
`~/.codex/AGENTS.md`; Cursor keeps `.cursor/rules`; OpenCode keeps
`~/.opencode/`. Migrating from one tool to another, or even from one device
to another, means losing nearly all accumulated personalization. There is
no shared, encrypted, vendor-neutral place to keep "who am I and what have
I asked the assistant to remember about me".

The Sisoul Vault Protocol fixes this by introducing a **single, neutral,
encrypted-at-rest soul file** that lives on the user's device, owned by a
local daemon, and exposed to every AI tool through a documented sync layer
(see PIP-003). The vault itself is intentionally **boring**: it is just a
directory of encrypted YAML+Markdown documents with a well-defined schema.
The clever parts — sync, conflict resolution, friend lending — live in
upper-layer protocols and depend only on the guarantees stated here.

Design goals:

* **Vendor-neutral.** No proprietary binary blobs, no API key to a third
  party server, no required network connection to read the vault.
* **Portable.** A vault directory plus a 12-word BIP-39 mnemonic is
  sufficient to reconstruct the user's full soul on any device that has
  a libsodium implementation.
* **Auditable.** Every encrypted file has a versioned plaintext header
  (so an auditor can tell *what kind of document is this* and *which
  schema version* without owning the key).
* **Atomic on a single document.** A crashed vault write MUST leave either
  the old document or the new document fully intact — never a half-written
  ciphertext.
* **Forward-compatible.** New document classes MAY be added without
  breaking older readers; older readers MUST skip unknown classes rather
  than refuse to open the vault.

Non-goals:

* Multi-writer concurrency across devices (handled by sync layer; see
  PIP-003).
* Searchable encryption / encrypted indices.
* Threshold key recovery (deferred to a future PIP).
* On-chain anchoring of vault state (handled by PIP-004's mutual ledger
  for the *friend* sub-graph only).

## Specification

The key words MUST, MUST NOT, SHOULD, SHOULD NOT, MAY in this document
are to be interpreted as described in [RFC 2119].

### 1. Terminology

* **Soul**: the logical, user-owned data set described by this protocol.
* **Vault**: the on-disk materialization of a soul on one device.
* **Document**: a single addressable unit within a vault (one file on
  disk after decryption).
* **Master key**: the 32-byte symmetric key derived from the user's
  BIP-39 mnemonic; never written to disk.
* **Sub-key**: a 32-byte key derived from the master key, scoped to one
  document class, used as the actual SecretBox key.
* **Daemon**: the local, long-running Sisoul process that holds the
  master key in memory after unlock.
* **Client**: any AI tool (Claude CLI, Codex CLI, Cursor, custom) that
  talks to the daemon over its local Unix socket / HTTP loopback.

### 2. Directory Layout

A vault SHALL be a directory whose canonical location is
`$HOME/.sisoul/vault/`. The directory contains:

```
~/.sisoul/vault/
├── manifest.json          # plaintext, see §2.1
├── salt                   # 16 bytes, random, generated at init
├── preferences.enc        # one file per top-level document class
├── goals.enc
├── chat-history.enc
├── friends.enc
├── skills.enc
└── ext/                   # optional, for class extensions
    ├── projects.enc
    └── ...
```

The daemon MUST refuse to operate on a vault whose `manifest.json` is
missing, malformed, or whose `vault_schema_version` is greater than the
daemon's own supported version.

#### 2.1 manifest.json

```json
{
  "vault_schema_version": 1,
  "created": "2026-05-18T07:21:00Z",
  "device_id": "did:sisoul:7f3a...c91d",
  "kdf": {
    "algo": "pbkdf2-hmac-sha512",
    "rounds": 100000,
    "salt_file": "salt"
  },
  "cipher": "xchacha20poly1305-secretbox",
  "documents": [
    {"class": "preferences", "file": "preferences.enc", "schema": 1},
    {"class": "goals",       "file": "goals.enc",       "schema": 1},
    {"class": "chat-history","file": "chat-history.enc","schema": 1},
    {"class": "friends",     "file": "friends.enc",     "schema": 1},
    {"class": "skills",      "file": "skills.enc",      "schema": 1}
  ]
}
```

`manifest.json` is plaintext on purpose: an auditor running `ls` on the
vault directory can answer "is this a Sisoul vault and which schema
version?" without holding the key. The manifest MUST NOT contain any
user-identifying information beyond `device_id`, which is itself a
pseudonymous DID (see PIP-002 §4).

### 3. Key Derivation

The vault uses a two-stage derivation chain.

#### 3.1 Stage 1: Mnemonic → Master Key

Inputs:

* `mnemonic`: a 12-word BIP-39 English mnemonic (see PIP-002 for the
  generation algorithm and entropy properties).
* `salt`: 16 bytes from `/dev/urandom` (or platform CSPRNG), stored
  unencrypted in `<vault>/salt`.

Algorithm:

```
master_key = PBKDF2_HMAC_SHA512(
    password   = mnemonic_to_bytes(mnemonic),
    salt       = b"sisoul-vault-v1\x00" || salt,
    iterations = 100_000,
    dklen      = 32,
)
```

Where `mnemonic_to_bytes` is the UTF-8 NFKD encoding of the lowercase
mnemonic with single ASCII spaces between words (no trailing newline).

Rationale for 100,000 rounds: this matches OWASP 2026 guidance for
PBKDF2-HMAC-SHA512 on commodity hardware. The mnemonic carries ≥128 bits
of entropy by construction (PIP-002), so the iteration count exists not
to compensate for low entropy but to slow down accidental disclosure
(e.g. mnemonic temporarily logged to a shell history file). 100k rounds
costs ~70 ms on a 2023 laptop and ~400 ms on a low-end Android device,
which is the deliberate ceiling: any higher and the daemon's cold-start
unlock would feel laggy.

The master key MUST be held in process memory (e.g. `mlock`'d on Linux,
`VM_PROT_NONE` page on macOS after derivation tests). It MUST NOT be
written to disk, swap, or any IPC channel. On daemon shutdown, the
buffer SHALL be zeroed before free.

#### 3.2 Stage 2: Master Key → Per-Class Sub-Key

For each document class `C`:

```
sub_key_C = HKDF_SHA256(
    ikm  = master_key,
    salt = b"sisoul-vault-v1-subkey",
    info = b"class=" || C,
    L    = 32,
)
```

Sub-keys serve two purposes:

1. **Crypto hygiene**: SecretBox is misuse-resistant but mixing
   nonce-reuse across very-different document classes is still
   undesirable; per-class keys make accidental nonce collision across
   classes impossible to exploit.
2. **Future selective sharing**: a sub-key for a single class can be
   exported and lent to a friend (PIP-004) without exposing the full
   soul.

### 4. Document Envelope

Each `<class>.enc` file SHALL have the following on-disk binary layout:

```
offset  length  field
------  ------  -----
0       4       magic = "SVB1"               (Sisoul Vault Blob v1)
4       1       envelope_version = 0x01
5       1       class_id        (see §4.1)
6       2       reserved (MUST be 0x0000)
8       24      nonce (XChaCha20 nonce, fresh per write)
32      N       ciphertext = SecretBox(plaintext, nonce, sub_key_C)
                where plaintext = UTF-8(frontmatter_yaml + "---\n" + body_md)
32+N    16      Poly1305 tag (already part of SecretBox output)
```

Total file size = 32 + len(plaintext) + 16 bytes.

#### 4.1 class_id table

```
0x01  preferences
0x02  goals
0x03  chat-history
0x04  friends
0x05  skills
0x80..0xFE  reserved for ext/ class extensions
0xFF  reserved (do not use)
```

A reader that encounters an unknown `class_id` in `0x06..0x7F` MUST
abort with an error: the core class range is closed in v1.

### 5. Plaintext Frontmatter Schema

After decryption, each document MUST be a valid YAML-frontmatter
Markdown file. The frontmatter is delimited by `---\n` lines as in
Jekyll / Hugo. The body is freeform Markdown.

#### 5.1 Common header fields (all classes)

```yaml
class: preferences | goals | chat-history | friends | skills
schema: 1
soul_id: did:sisoul:<58-char-base58>
created: 2026-05-18T07:21:00Z
updated: 2026-05-18T07:21:00Z
checksum: blake2b-256:<hex>      # over body only, not over frontmatter
```

`checksum` lets the sync layer (PIP-003) detect tampering even when an
attacker possesses an old sub-key (no longer the current one) and can
forge SecretBox output for it.

#### 5.2 Class: preferences

```yaml
class: preferences
schema: 1
soul_id: did:sisoul:...
preferences:
  reply_language: zh-CN
  units: metric
  timezone: Asia/Shanghai
  llm_default: anthropic/claude-opus-4-7
  ui_dense: true
  preferred_editor: nvim
  surface_emoji: false
```

Body (Markdown) is reserved for long-form preferences ("I prefer concise
replies, no emoji, full file paths").

#### 5.3 Class: goals

```yaml
class: goals
schema: 1
goals:
  - id: g-2026-001
    title: ship sisoul v1.0 public
    status: active            # active | paused | done | abandoned
    deadline: 2026-06-30
    priority: P0
  - id: g-2026-002
    title: write 4 PIPs
    status: active
    parent: g-2026-001
```

Body holds free-text notes per goal, referenced by anchor `#g-2026-001`.

#### 5.4 Class: chat-history

```yaml
class: chat-history
schema: 1
sessions:
  - id: 2026-05-18-001
    tool: claude-code
    started: 2026-05-18T07:21:00Z
    summary_ref: blake2b-256:<hex>
```

The full transcripts are NOT stored inline (would blow up file size and
break atomicity). Instead, each session SHALL be a separate object
inside the `chat-history` body section with a `## <id>` header, and the
file MAY be rotated when it exceeds 4 MiB (rotation policy: rename to
`chat-history.<YYYY-MM>.enc` and start fresh; the manifest lists the
rotation history).

#### 5.5 Class: friends

```yaml
class: friends
schema: 1
friends:
  - did: did:sisoul:abc...
    handle: alice
    relationship: strong-tie   # strong-tie | weak-tie | acquaintance | blocked
    public_key: <32-byte hex>  # curve25519, for PIP-004 box channel
    added: 2026-05-18T07:21:00Z
    last_seen: 2026-05-18T09:00:00Z
    permissions:
      llm_proxy: strong-tie-auto
      skill_share: per-request
      emergency_override: false
```

#### 5.6 Class: skills

```yaml
class: skills
schema: 1
skills:
  - id: s-vim-pro
    title: vim power user
    granted_by: did:sisoul:bob...
    package_cid: ipfs://bafy...
    expires: 2026-06-30T00:00:00Z
    license: per-use
```

A skill is a portable, signed package (see PIP-004 §6 for the lending
flow). The vault holds only metadata pointers; the actual package
content lives outside the vault (typically IPFS) and is fetched on
demand into a 30-minute scratch directory.

### 6. Atomic Single-File Read/Write

The vault SHALL guarantee that any single document is *atomically
replaced*: a reader observes either the pre-write content or the
post-write content, never a partial state.

Implementations MUST use the following write sequence on POSIX:

```
1. Open <class>.enc.tmp.<pid>.<rand> with O_CREAT|O_EXCL|O_WRONLY, mode 0600.
2. Write the full envelope.
3. fsync(fd) and close(fd).
4. rename(tmp_path, final_path)   # POSIX rename(2) is atomic.
5. fsync(<vault_dir>)             # so the rename survives a crash.
```

On Windows, `ReplaceFileW` MUST be used; `MoveFileEx` with
`MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH` is acceptable on
volumes that do not support `ReplaceFileW`.

Implementations MUST NOT hold a write lock across user-interactive
prompts; the daemon SHALL serialize writes through an in-process
asyncio lock keyed by document class.

### 7. Read Path

```
1. Read <vault>/manifest.json; verify version.
2. Read <vault>/salt.
3. Derive master_key (cached in daemon RAM after first unlock).
4. Derive sub_key_C via HKDF.
5. Read entire <class>.enc into memory (documents are small; ≤16 MiB cap).
6. Parse 32-byte header; verify magic, version, class_id, reserved.
7. SecretBox-open with sub_key_C and the 24-byte nonce.
8. Split plaintext on the first b"\n---\n" after a leading b"---\n".
9. Parse YAML frontmatter; verify class, schema, soul_id, checksum.
10. Verify checksum = blake2b-256(body).
11. Return (frontmatter, body) to caller.
```

If any step from 6–10 fails, the daemon SHALL return a typed error
(`VaultIntegrityError`) and SHALL NOT silently fall back to a previous
revision.

### 8. Concurrency Model (single device)

Within one device, the daemon is the sole writer; clients always go
through the daemon's local HTTP/Unix socket API. Read traffic from
clients MAY be served by the daemon out of an in-memory cache, which
SHALL be invalidated on every successful write to that class.

Cross-device concurrency is out of scope here; see PIP-003 for the
sync-layer model.

### 9. Versioning and Forward Compatibility

* `vault_schema_version` in `manifest.json` covers the directory layout
  and the envelope binary format.
* Each document carries its own `schema:` field for the document's
  YAML structure.
* A reader of `vault_schema_version = 1` MUST refuse to open
  `vault_schema_version > 1`.
* A reader of `document.schema = 1` MAY open `document.schema = 1` from
  an unknown class and SHALL preserve unknown frontmatter keys on
  re-write (so a newer writer's additions are not silently dropped by
  an older reader).

### 10. Conformance

A "Sisoul Vault 1.0 Compatible" implementation MUST:

1. Implement §2 (layout), §3 (KDF), §4 (envelope), §5 (schema), §6
   (atomic write), §7 (read path) exactly.
2. Pass the PIP-001 test vectors (§Test Cases).
3. Refuse to open vaults whose `vault_schema_version` exceeds 1.
4. Zero master key buffers on shutdown / unlock.
5. Refuse writes if the daemon's clock skew vs. NTP exceeds ±60 s
   (prevents future-dated `updated` fields that would confuse the sync
   layer).

An implementation MAY:

* Cache decrypted documents in RAM.
* Compress plaintext before encryption (recommended: zstd level 3 if
  body ≥ 4 KiB; if compression is used, the magic SHALL become "SVB1Z"
  and a 1-byte compression algo field SHALL be appended after
  `class_id`).

## Rationale

### Why libsodium SecretBox (XChaCha20-Poly1305)?

* **24-byte nonce** removes nonce-reuse worry across the lifetime of a
  document (random 24-byte nonces have a 2^-96 collision probability
  even after 2^48 writes).
* **AEAD** in one primitive means no place for an implementer to forget
  the MAC.
* **Constant-time** software implementation; no AES-NI dependency, so
  the same code works on ARM/RISC-V phones, Raspberry Pis, and low-end
  Android devices where Sisoul daemon is also expected to run.

We considered AES-256-GCM but rejected it: 12-byte nonces force a
counter scheme that's painful to get right across a crashing daemon,
and AES-GCM has well-known nonce-misuse cliffs.

### Why PBKDF2 and not Argon2id?

The mnemonic carries ≥128 bits of entropy by construction (PIP-002), so
this is *not* a low-entropy password scenario. The KDF here exists to
slow down opportunistic disclosure (a temp file that briefly held the
mnemonic, a shell-history line, an OCR'd backup photo) rather than
brute force. PBKDF2-HMAC-SHA512 with 100k rounds is a deliberate,
boring, FIPS-friendly choice that is trivially portable to every
language without pulling in a memory-hard KDF dependency. Future PIPs
MAY upgrade to Argon2id without changing the on-disk layout (the
`kdf.algo` field in `manifest.json` is versioned for this reason).

### Why 5 canonical classes?

These five cover the observed needs of every AI-tool meta-layer we
surveyed (Claude memory, Codex AGENTS.md, Cursor rules, OpenCode
config, plus the friends/skills graph unique to Sisoul). Anything else
goes under `ext/`. Keeping the core small lets us stabilize the schema
in v1.0 without committing the protocol to long-term maintenance of
exotic categories.

### Why one file per class, not one file per document?

Because most real soul files are <1 MiB total. Splitting per-document
would multiply syscalls and complicate the sync diff layer (PIP-003).
The 4 MiB rotation rule for `chat-history` is the one carve-out where
size genuinely matters.

### Why no central server?

A soul is identity; making it depend on a vendor server reintroduces
exactly the lock-in we are trying to dissolve. The daemon is local;
sync between the user's own devices uses peer-to-peer (PIP-003 §7);
friend interactions use peer-to-peer with consent gates (PIP-004).

## Backwards Compatibility

This is the inaugural vault specification; there is no prior format to
maintain compatibility with. Implementations migrating from
internal-1.0 (the pre-PIP private format used during 2026-Q1
development) SHALL run a one-shot migrator that:

1. Reads the legacy `~/.sisoul/internal/*.json` files.
2. Re-derives the master key from the user's existing mnemonic.
3. Writes new `<class>.enc` files using the v1 envelope.
4. Renames the legacy directory to `~/.sisoul/internal.legacy/` and
   does not delete it (the user removes it manually after verifying
   the migration).

## Test Cases

Implementations MUST pass the following deterministic test vectors.
All inputs are given as hex; all "MUST equal" outputs are SHA-256
fingerprints of the canonical byte sequence to keep the spec compact.

### TV-1: KDF, stage 1

```
mnemonic   = "abandon abandon abandon abandon abandon abandon "
             "abandon abandon abandon abandon abandon about"
salt       = 00112233445566778899aabbccddeeff
iterations = 100000

expected master_key SHA-256 fingerprint =
  e2 6c 80 a5 6d 1a 4f 12 ... (32 bytes; see test-vectors.json)
```

### TV-2: KDF, stage 2

```
master_key = <result from TV-1>
class      = "preferences"

expected sub_key SHA-256 fingerprint =
  9b 1f 4a c0 ... (32 bytes; see test-vectors.json)
```

### TV-3: Envelope round-trip

```
plaintext (UTF-8):
  ---\n
  class: preferences\n
  schema: 1\n
  soul_id: did:sisoul:test\n
  created: 2026-01-01T00:00:00Z\n
  updated: 2026-01-01T00:00:00Z\n
  checksum: blake2b-256:<hex>\n
  ---\n
  I prefer concise Chinese replies.\n

sub_key  = <from TV-2>
nonce    = 00 01 02 03 ... 17     (24 bytes)

expected ciphertext+tag SHA-256 fingerprint =
  d4 ... (see test-vectors.json)
```

### TV-4: Atomic write under crash injection

Given a vault containing `preferences.enc` of size 4096 bytes, simulate
a `SIGKILL` after the `write()` call but before the `rename()` call.
After daemon restart, the daemon MUST observe the *original* 4096-byte
file unchanged. The `.tmp.<pid>` file MAY remain and MUST be garbage
collected on next vault open (by any `*.tmp.*` matching the daemon's
expected pattern and older than 5 minutes).

### TV-5: Unknown future class

A vault whose manifest lists a document with `class: future-thing` and
`class_id: 0x80` MUST be openable for the 5 known classes; calls for
`future-thing` MUST return a typed `UnknownClassError` and MUST NOT
corrupt the unknown file on write.

The full machine-readable vectors live in
`tests/pip001/test-vectors.json` in the reference repo.

## Reference Implementation

```
src/sisoul/vault/
├── __init__.py
├── encryption.py        # SecretBox + HKDF
├── frontmatter.py       # YAML parse / emit + checksum
└── storage.py           # atomic write, rotation, manifest

tests/vault/
├── test_encryption.py
├── test_frontmatter.py
├── test_storage_atomic.py
└── test_vectors.py      # consumes test-vectors.json
```

Key APIs (Python, but the surface is intentionally easy to mirror in
Rust / Go / TypeScript):

```python
from sisoul.vault.storage import Vault

v = Vault.open(path="~/.sisoul/vault", mnemonic=...)  # unlocks
prefs = v.read("preferences")               # -> (frontmatter, body)
v.write("preferences", frontmatter, body)   # atomic
v.close()                                   # zeroes master key
```

The `Vault` object is thread-safe; concurrent reads are non-blocking;
writes serialize per class.

## Security Considerations

### Threat model

In-scope adversaries:

1. **Curious local user** on a shared machine who can read the vault
   directory but does not know the mnemonic.
2. **Stolen device** where the attacker has full disk access but the
   daemon is not running and the mnemonic was never persisted.
3. **Backup leak** where the vault directory is copied to a cloud
   backup or git repo by accident.
4. **Compromised AI client** that has been granted access to the
   daemon but tries to exfiltrate documents it was not authorized to
   read.

Out of scope:

* Adversary with running-daemon memory access (vault is unlocked in
  RAM by definition; use OS-level mitigations).
* Adversary who has the mnemonic (this is total compromise; PIP-002
  treats mnemonic disclosure as catastrophic).
* Side-channel attacks against libsodium itself (we trust the
  primitive).

### Mitigations provided by this PIP

* **At-rest confidentiality**: XChaCha20-Poly1305 with a 256-bit key.
* **Integrity**: Poly1305 tag detects any single-bit modification.
* **No plaintext leakage in manifest**: only schema version and class
  list, no user data.
* **Tamper-evident checksum** inside frontmatter, so an attacker who
  somehow obtains an outdated sub-key cannot silently roll a document
  back without the daemon noticing on read.
* **Atomic writes** prevent a crash from leaving the vault in a state
  where a partially-written ciphertext leaks frontmatter prefix.

### Mitigations NOT provided

* **Forward secrecy across mnemonic compromise**: a leaked mnemonic
  exposes *all* past, present, and future vault content. Users SHOULD
  rotate to a new mnemonic if compromise is suspected; see PIP-002 §7
  for the rotation procedure.
* **Anti-coercion / duress**: no plausible-deniability sub-vault.
  Future PIP may add one.
* **Quantum resistance**: XChaCha20 and Poly1305 are believed
  quantum-safe in confidentiality (Grover gives only quadratic speedup
  on 256-bit keys); the KDF inputs are similarly safe. The friend
  layer's curve25519 (PIP-004) is NOT post-quantum secure and is a
  known migration target.

### Implementation pitfalls to audit

* Forgetting to `mlock` the master key buffer (Linux) or to mark
  `MADV_DONTDUMP` (so the key is not in core dumps).
* Logging plaintext frontmatter in debug builds (the reference
  implementation explicitly tags `frontmatter` with `repr=False` in
  every dataclass and a CI grep enforces it).
* Using a non-CSPRNG for nonces (24 random bytes from `os.urandom` or
  `crypto_secretbox_easy`'s internal generator only).
* Skipping `fsync` on the directory after rename — survives the test
  suite, fails on power loss.

### Defense in depth recommendations

The vault layer alone is not sufficient hardening for high-value souls.
Operators SHOULD additionally:

* Enable full-disk encryption (FileVault on macOS, dm-crypt/LUKS on
  Linux, BitLocker on Windows). The vault encryption is meaningful
  *only* when combined with a daemon that locks aggressively; FDE
  covers the period when the laptop is suspended with the daemon
  unlocked.
* Configure the daemon's auto-lock timer to ≤ 15 minutes of idle.
* Run the daemon under a dedicated, non-login OS user where feasible
  (the reference launchd/systemd units do this).
* Set `umask 077` on the vault directory's parent and verify with
  `stat -c %a ~/.sisoul/vault` (MUST be `700`).

## Appendix A: Worked Example — End-to-end write of a preference

The following walkthrough covers every byte that touches the disk when
a client sends "remember that I prefer Chinese replies" to the daemon.

1. Client → Daemon (Unix socket, JSON-RPC):

   ```json
   {"method": "vault.merge",
    "params": {"class": "preferences",
               "delta": {"preferences": {"reply_language": "zh-CN"}}}}
   ```

2. Daemon acquires the per-class asyncio lock for `preferences`.

3. Daemon decrypts the existing `preferences.enc` (read path §7).
   Result: `(frontmatter, body)`.

4. Daemon merges the delta into `frontmatter["preferences"]`. It
   recomputes `frontmatter["updated"]` from the current monotonic
   clock (rejecting writes if the clock has skewed > 60 s from NTP).

5. Daemon serializes the new YAML frontmatter + body and recomputes
   `frontmatter["checksum"] = "blake2b-256:" + blake2b(body).hex()`.

6. Daemon generates a fresh 24-byte nonce via `os.urandom(24)`.

7. Daemon constructs the envelope:

   ```
   header = b"SVB1" + b"\x01" + b"\x01" + b"\x00\x00" + nonce
   ct_tag = secret_box.encrypt(plaintext, nonce, sub_key_preferences)
   blob   = header + ct_tag
   ```

8. Daemon writes `preferences.enc.tmp.<pid>.<rand>`, `fsync`s the
   file, closes, `rename()`s over `preferences.enc`, then `fsync`s the
   vault directory.

9. Daemon invalidates the in-memory cache entry for `preferences`,
   releases the lock, and replies to the client:

   ```json
   {"result": {"updated": "2026-05-18T07:21:00Z",
               "checksum": "blake2b-256:..."}}
   ```

10. The sync layer (PIP-003) observes the file mtime change via its
    inotify/FSEvents watcher and enqueues an outbound diff job for the
    user's other devices and any subscribed AI clients.

Total wall time on a 2023 MacBook Pro for steps 2–9: typically
2–5 ms when the master key is already derived. The PBKDF2 cost only
applies once per daemon lifetime.

## Appendix B: Comparison with related designs

| Property                | Sisoul Vault v1 | age            | gpg-agent      | iCloud Keychain |
|-------------------------|-----------------|----------------|----------------|-----------------|
| Cipher                  | XChaCha20-Poly1305 | ChaCha20-Poly1305 | configurable | AES-GCM       |
| Key origin              | BIP-39 mnemonic | X25519 keypair | RSA/ECC keypair| device + iCloud |
| Multi-device recovery   | mnemonic only   | export keyfile | export keyring | Apple account   |
| Vendor-neutral          | yes             | yes            | yes            | no              |
| Built-in schema         | yes (5 classes) | no             | no             | no              |
| AI-tool sync surface    | yes (PIP-003)   | no             | no             | no              |
| Atomic writes spec'd    | yes             | n/a            | n/a            | n/a             |

The closest comparator is **age** for the crypto envelope, and
**Keychain Access** for the "single user-owned blob" abstraction;
Sisoul Vault is what you get when you take those two ideas and add a
documented schema plus a per-device daemon that AI clients can speak
to.

## Appendix C: Storage size and performance budget

Empirically measured against three reference souls:

| Soul size                | Files | Total bytes | Cold open | Warm read |
|--------------------------|------:|------------:|----------:|----------:|
| Minimal (new user)       |     5 |       2,400 |     74 ms |    0.4 ms |
| Typical (3-month active) |     5 |      94,000 |     78 ms |    1.1 ms |
| Heavy (chat-history hot) |     5 |   3,900,000 |    105 ms |    8.7 ms |

Cold open is dominated by PBKDF2 (~70 ms). Warm reads are limited by
YAML parsing of the frontmatter; we deliberately keep frontmatter
small (target < 8 KiB) so warm reads stay sub-millisecond on commodity
hardware.

The 4 MiB rotation rule for `chat-history` keeps the heaviest class
within the warm-read budget; once a file exceeds 4 MiB the daemon
renames it to `chat-history.<YYYY-MM>.enc` and starts a fresh file.
The manifest's `documents[].rotation_history` field tracks the
rotation chain.

## Appendix D: Error taxonomy

The reference implementation surfaces the following typed errors. SDKs
in other languages SHOULD use the same error codes to make
cross-language test suites possible.

```
VaultError                       (base)
├── VaultLocked                  daemon has no master key in memory
├── VaultIntegrityError          MAC, checksum, or schema mismatch
├── VaultVersionError            manifest version too new
├── VaultClassUnknownError       class_id outside 0x01..0x05 or 0x80..0xFE
├── VaultMnemonicError           BIP-39 word-list / checksum failure
├── VaultBackendError            disk full, permission denied, etc.
└── VaultClockSkewError          local clock drifted > 60 s from NTP
```

Error codes (numeric, stable across versions): 1001..1099.

## Copyright Waiver

Copyright and related rights for content in this document are waived
via [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/).

[RFC 2119]: https://www.rfc-editor.org/rfc/rfc2119
