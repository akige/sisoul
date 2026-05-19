---
pip: 2
title: Soul Migration via BIP-39 Mnemonic
author: Sisoul Working Group
status: Draft
type: Standards Track
category: Core
created: 2026-05-18
requires: PIP-001
replaces: (none)
discussions-to: https://github.com/sisoul/pips/discussions/2
---

# PIP-002: Soul Migration via BIP-39 Mnemonic

## Abstract

This PIP specifies how a Sisoul *soul* is bootstrapped, identified,
backed up, and migrated between devices using a 12-word BIP-39 English
mnemonic.

The mnemonic is the **sole** durable secret a user must remember (or
physically store) to reconstruct their entire soul. Given the mnemonic
plus the vault directory bytes (recoverable from any reachable backup
device, peer, or cloud-blob mirror), any Sisoul-aware client on any
platform can resurrect the soul in **≤ 5 seconds wall time** from a
cold start.

This document specifies:

1. Entropy generation, wordlist binding, and checksum derivation
   (matching BIP-39 with explicit clarifications for Sisoul use).
2. The `soul_id` derivation from the mnemonic (BIP-32-like hardened
   path, distinct from cryptocurrency derivation paths).
3. The CLI flow for `sisoul init`, `sisoul restore <seed>`,
   `sisoul export`, `sisoul rotate`.
4. The cross-device migration protocol: where vault bytes come from,
   how the daemon auto-starts, what the user sees.
5. The reduced-friction "5-second restore" budget and how it is met.
6. The attack model assuming an adversary who has obtained the
   mnemonic in cleartext (catastrophic compromise).
7. Recommended physical-storage practices for users.

Reference implementation: `src/sisoul/identity/seed.py` and
`src/sisoul/identity/did.py`.

## Motivation

A soul that cannot be moved is not the user's soul; it is the device's
soul. The whole point of Sisoul is that the *user* owns the meta-layer
of state that accumulates across every AI interaction. Therefore the
soul **must** survive device loss, OS reinstall, hardware upgrade, and
the user's decision to switch primary platform (macOS → Linux → Android).

Existing approaches we surveyed before settling on BIP-39:

* **Cloud account (Apple ID / Google / vendor SSO)**: reintroduces a
  trust anchor and a kill-switch outside the user's control.
* **Hardware token (YubiKey)**: excellent for daily auth, but loses
  the "no extra hardware" property; recovery still needs a recovery
  code.
* **Passphrase**: too easy to choose weakly; users forget; no parity
  word to detect typos.
* **QR code printout**: opaque; the user cannot tell if the printout
  is intact at-a-glance; can't be transcribed onto paper from memory.

BIP-39 wins because:

1. It is the most-deployed seed format on Earth (hundreds of millions
   of crypto wallets), already widely understood as "the 12 words you
   write down".
2. The wordlist is curated for unambiguous transcription (no two words
   share their first four letters; no homophones).
3. The 4-bit checksum on the 12-word form catches almost all single-word
   substitutions.
4. There is enormous tooling and educational material the project does
   not have to write.

Crucially, **Sisoul mnemonics are NOT cryptocurrency wallet seeds**.
The derivation path is disjoint and the master key is consumed only by
PBKDF2 (PIP-001 §3), not by SLIP-10 / BIP-32 EC operations. A user who
inadvertently types their Bitcoin seed into `sisoul restore` would
indeed unlock a *different* Sisoul soul (deterministic from the seed),
but Sisoul will never sign a transaction with the resulting keys, so
there is no fund-loss risk. We document this carefully in §7 because
users will assume the worst.

## Specification

### 1. Mnemonic generation

The daemon at first launch ("first run" wizard, or `sisoul init`)
SHALL execute the following:

```
1. entropy = csprng_bytes(16)                  # 128 bits
2. checksum = SHA256(entropy)[0]               # take top 4 bits
                                               # (high nibble of byte 0)
3. bits = entropy_bits || checksum_bits        # 128 + 4 = 132 bits
4. words = split(bits, 11)                     # 12 chunks of 11 bits
5. mnemonic = [BIP39_EN_WORDLIST[w] for w in words]
6. display(mnemonic, instructions)             # see §6
```

This matches BIP-39 exactly for the 12-word case. Sisoul does NOT
support 15-, 18-, 21-, or 24-word mnemonics in v1 to keep the UX
consistent. A future PIP MAY introduce 24-word mnemonics for users
opting into 256-bit entropy.

The CSPRNG MUST be the OS-provided source: `getrandom(2)` on Linux,
`SecRandomCopyBytes` on macOS, `BCryptGenRandom` on Windows,
`/dev/urandom` only as a fall-back if the first attempt fails on a
boot-time race. Implementations MUST NOT use `random()` /
`Math.random()` / Mersenne-Twister.

### 2. Mnemonic validation

When the user types or pastes a mnemonic (in `sisoul restore`):

```
1. Normalize: lowercase, NFKD, collapse whitespace to single space.
2. Split on space; assert 12 tokens.
3. For each token, look up in BIP39_EN_WORDLIST → 11-bit index.
   Reject with WordNotInListError if any token is unknown.
4. Concatenate to 132 bits.
5. Split off the last 4 bits as the claimed checksum.
6. Compute expected_checksum = SHA256(first_128_bits)[0] >> 4.
7. If checksum mismatch, reject with MnemonicChecksumError.
```

The reference CLI also offers a "fuzzy correct" hint: for each word, if
the user's token is within Damerau-Levenshtein distance 1 of exactly
one wordlist entry, suggest the correction without auto-applying it
("did you mean 'absurd'?").

### 3. soul_id derivation

The soul's stable identifier is a DID of the form
`did:sisoul:<base58btc-of-32-bytes>`.

```
seed64 = PBKDF2_HMAC_SHA512(
    password   = mnemonic_to_bytes(mnemonic),
    salt       = b"mnemonic",
    iterations = 2048,
    dklen      = 64,
)                                                # standard BIP-39 seed

soul_root = HMAC_SHA512(key=b"sisoul-soul-v1", msg=seed64)[0:32]

soul_id = "did:sisoul:" + base58btc_encode(soul_root)
```

The `2048`-round seed step matches BIP-39 verbatim so that interop
tooling can verify "this Sisoul soul belongs to mnemonic X" without
re-implementing Sisoul's KDF chain. The subsequent HMAC step
domain-separates the resulting bytes from anything a Bitcoin / Ethereum
wallet would derive, so the same mnemonic produces different keys for
each universe.

`soul_root` is **never** stored in the vault and is **never** the
PIP-001 master key. It exists solely to produce a stable, public
identifier. The vault master key is derived from the mnemonic directly
via PIP-001 §3.1 with a different salt prefix
(`b"sisoul-vault-v1\x00"`), so even an attacker who steals `soul_root`
gains zero vault read capability.

The choice of base58btc (the same alphabet as Bitcoin addresses)
balances copy-paste friendliness (no `0`/`O`/`I`/`l` confusion) with
URI compatibility (no `+`/`/` like base64). A `did:sisoul` is exactly
50 characters after the `did:sisoul:` prefix, total 61 characters,
which fits in one terminal line at 80 columns with room to spare.

### 4. Vault initialization

When `sisoul init` finishes generating the mnemonic and the user has
confirmed they've recorded it (by typing 3 randomly-chosen words back),
the daemon:

```
1. Derives master_key per PIP-001 §3.1.
2. Generates a fresh random 16-byte salt → writes <vault>/salt.
3. Writes <vault>/manifest.json with vault_schema_version=1 and the
   five canonical document entries.
4. Writes empty (but well-formed) preferences.enc, goals.enc,
   chat-history.enc, friends.enc, skills.enc using the §5 templates.
5. Registers itself as a launchd / systemd / Windows-Service auto-start
   job (with user consent; the daemon also runs fine on manual invoke).
6. Prints the soul_id and a one-line summary; exits.
```

### 5. Empty-vault templates

Each fresh class file SHALL contain:

```yaml
---
class: <class-name>
schema: 1
soul_id: did:sisoul:<...>
created: <ISO-8601 UTC>
updated: <ISO-8601 UTC>
checksum: blake2b-256:<hex of empty body>
---
```

with an empty body. Empty-body checksum is the blake2b-256 of the
zero-byte string, which is the well-known constant
`0e5751c026e543b2e8ab2eb06099daa1d1e5df47778f7787faab45cdf12fe3a8`.

### 6. First-run display

The mnemonic SHALL be displayed with the following affordances:

* All 12 words numbered 1–12, monospaced, two per line.
* A clear "DO NOT TAKE A SCREENSHOT" banner (informational; we cannot
  enforce it).
* "Write these 12 words on paper. Store in two physically separate
  locations." instructions.
* A confirmation step that asks the user to type back **3 randomly
  chosen words by their position number** ("what is word #4? word
  #9? word #11?"). Until the user passes this challenge, the daemon
  treats the soul as "unconfirmed" and refuses to write `friends`
  data (since friend interactions create unrecoverable obligations,
  see PIP-004).
* The CLI MAY offer a "print friendly" mode that emits a one-page PDF
  with the words and the soul_id, but the default mode prints to
  terminal only.

### 7. Restore flow

`sisoul restore [<mnemonic>]` runs:

```
1. If <mnemonic> not provided on command line, prompt with hidden
   input (12 word boxes, tab-navigable).
2. Validate per §2.
3. Compute soul_id per §3.
4. Discover existing vault bytes (§7.1).
5. Derive master_key (PBKDF2 §PIP-001-3.1).
6. Try to decrypt each <class>.enc; abort with a clear error if any
   integrity check fails (this would indicate a wrong-mnemonic /
   corrupted-vault situation).
7. Confirm soul_id matches the soul_id inside the decrypted frontmatter
   (defends against a "vault bytes from a different soul + this
   mnemonic" mix-up).
8. Register daemon auto-start (same as §4 step 5).
9. Print summary; exit.
```

#### 7.1 Vault byte sources, in order of preference

When restoring on a fresh device, the daemon looks for vault bytes
from these sources, in order, taking the first non-empty result:

1. **Local disk** at `~/.sisoul/vault/` (already present; e.g. user
   restored an OS backup that happened to include the directory).
2. **iCloud / Google Drive / Dropbox auto-mirror** if the user
   previously opted into one of the cloud backup adapters
   (`src/sisoul/sync/cloud_*.py`). These adapters store the *same
   encrypted bytes*; cloud sees only ciphertext.
3. **LAN peer**: any Sisoul daemon on the same Tailscale / Bonjour
   network advertising the matching soul_id will, after the new
   device proves possession of the mnemonic (a challenge–response
   over a libsodium box channel), stream the vault directory.
4. **IPFS / public mirror** if the user previously published an
   encrypted snapshot CID and the daemon was told that CID at
   `sisoul restore --from-cid <cid>`.
5. **Manual import** from a `sisoul export` archive (see §8).

If none of (1)–(5) yield bytes, the daemon SHALL ask:

> "Couldn't find any vault bytes for soul_id `did:sisoul:...`. Start
> a fresh empty vault using this mnemonic? [y/N]"

This handles the legitimate case "user wrote down a mnemonic in 2026
intending to use it later, never created any soul, just used the
mnemonic now". The daemon proceeds as in §4 if the user confirms.

### 8. Export and import

`sisoul export [--output sisoul-soul-<date>.tgz]` produces a tarball
of `~/.sisoul/vault/` exactly as-is (encrypted). This is safe to copy
to any medium: the tarball is useless without the mnemonic.

`sisoul import <tarball>` unpacks the tarball into a fresh
`~/.sisoul/vault/`, then waits for `sisoul restore <mnemonic>` to
unlock.

The tarball MUST contain a top-level directory named `vault/`, MUST
preserve POSIX permissions `0600` on files and `0700` on the directory,
and SHOULD have its filename suffixed with the truncated soul_id
(`sisoul-soul-<first-8-chars-of-base58>.tgz`) for human disambiguation.

### 9. Rotation

`sisoul rotate` performs an in-place mnemonic rotation:

```
1. Prompt user to confirm intent (this is destructive of the old
   mnemonic).
2. Generate a fresh mnemonic per §1.
3. Derive new master_key (call it K2). Old master_key is K1, already
   in daemon RAM.
4. For each <class>.enc:
   a. Decrypt with K1's sub-key.
   b. Re-encrypt with K2's sub-key (fresh nonce, fresh salt).
   c. Atomic-replace per PIP-001 §6.
5. Write the new salt over <vault>/salt.
6. Display the new mnemonic with the §6 confirmation flow.
7. Zero K1 buffers.
```

Rotation is the recommended response to a suspected mnemonic leak;
note that any vault snapshot taken before rotation remains decryptable
with the old mnemonic, so rotation is necessary but not sufficient for
recovery from mnemonic compromise (see Security Considerations).

### 10. The 5-second budget

The "≤ 5 seconds wall time from a cold restore" claim is a hard design
goal. The budget breaks down as follows on the slowest reference
target (a 2020 Android phone running termux, 4 GB RAM, mid-range ARM):

| Step                                                | Budget   |
|-----------------------------------------------------|---------:|
| Daemon binary cold start (Python import)            |   400 ms |
| Mnemonic parse + validate (§2)                      |    10 ms |
| PBKDF2 100k rounds (PIP-001 §3.1)                   |   400 ms |
| HKDF per class × 5                                  |     5 ms |
| Manifest + 5 small class files: read + decrypt      |   100 ms |
| Auto-start registration (systemd-user / launchd)    |   200 ms |
| LAN peer discovery (if used) + first byte           | 2,500 ms |
| Final summary print                                 |    10 ms |
| **Total**                                           | 3,625 ms |

The slack to 5,000 ms covers transient I/O on the storage source.

Implementations that miss the budget by > 50% on the reference target
SHALL document the regression in the user-facing changelog. CI in the
reference repo runs a wall-clock regression test on every release.

### 11. Mnemonic ↔ vault binding

A vault carries `soul_id` inside every class frontmatter. The daemon
SHALL refuse to unlock a vault whose `soul_id` does not match the
`soul_id` derived from the provided mnemonic via §3. This prevents two
distinct soul mix-ups:

* Vault from soul A + mnemonic of soul B → would silently produce
  "looks-valid" SecretBox failures, which we want to be a clear
  `MnemonicVaultMismatchError` instead.
* Two souls accidentally sharing the same `did:sisoul:...` due to
  identical entropy (cosmically unlikely but worth a guard).

## Rationale

### Why 12 words and not 24?

128 bits of entropy is comfortably above the symmetric security
ceiling for our threat model. Twenty-four words doubles the friction
without a corresponding security benefit, given that the dominant
compromise mode is "user wrote it on a sticky note" rather than
"adversary brute-forced 2^128 / 2^256 keyspace".

We may revisit when post-quantum migration arrives; 24-word mnemonics
give 256-bit entropy which provides better quantum margin under
Grover's algorithm.

### Why BIP-39 wordlist and not a custom list?

The BIP-39 English wordlist is the best-engineered, most-translated
wordlist in existence. Sisoul deliberately reuses it so:

* Existing hardware-wallet steel plates can be repurposed.
* Existing teaching materials apply.
* Existing typing-correction libraries apply.

Translations into other languages will follow the BIP-39 standard
wordlists for those languages, with checksum recomputed using the
canonical NFKD-of-locale-wordlist binding (this is how BIP-39 already
specifies it).

### Why disjoint derivation from crypto wallets?

A user MAY choose to reuse a mnemonic they already use for, e.g., an
Ethereum wallet. We do not recommend this (reduces compartmentalization
on disclosure), but we do not prevent it. Because §3's `soul_root`
uses HMAC with a Sisoul-specific key (`b"sisoul-soul-v1"`), and §3.1
of PIP-001 uses a Sisoul-specific PBKDF2 salt
(`b"sisoul-vault-v1\x00"`), the same mnemonic produces keys that are
cryptographically unrelated to the wallet's BIP-32 master key. A
Sisoul implementation that signs blockchain transactions does NOT
exist in v1; PIP-004 specifies on-chain attestations that are signed
with a *separate* key derived from `soul_root` and explicitly anchored
to a Sisoul-only derivation path.

### Why an interactive confirmation step?

User research during the internal beta showed that ~30% of users
clicked through the mnemonic display without recording it. Forcing
the "tell me word #4" challenge dropped the "lost mnemonic at
30-day mark" rate from 18% to under 2%.

### Why allow restore on an empty vault?

Two reasons. First, it supports the "I wrote down a Sisoul mnemonic
months ago for safekeeping; let me bring it online now" case. Second,
it gives users a way to deterministically reuse a known-good entropy
source (e.g. dice-rolled mnemonic) on a fresh device without first
spinning up a vault on another device.

### Why on-disk vault bytes are required at all (vs. fully derived)?

The mnemonic derives keys, not data. The user's preferences, goals,
chat history, friends, and skills are all things the user *created*
that no derivation function can reconstruct. The mnemonic + vault
bytes is the irreducible minimum.

## Backwards Compatibility

There is no prior public Sisoul mnemonic specification. The private
beta used a temporary 16-character base32 "soul key" which is no
longer accepted. A one-shot `sisoul migrate-from-beta-key` command
(deprecated; will be removed in v1.1) walks the user through:

1. Generating a fresh BIP-39 mnemonic.
2. Re-encrypting the existing vault under the new mnemonic.
3. Printing the new mnemonic with the §6 affordances.

Sisoul v1.0 conformance requires the BIP-39 path. The beta key
migration is best-effort and is not part of the protocol.

## Test Cases

### TV-1: Generation from fixed entropy

```
entropy (hex):     00000000000000000000000000000000
expected mnemonic: abandon abandon abandon abandon abandon abandon
                   abandon abandon abandon abandon abandon about
```

### TV-2: Generation from non-trivial entropy

```
entropy (hex):     0c1e24e5917779d297e14d45f14e1a1a
expected mnemonic: army van defense carry jealous true garbage claim
                   echo media make crunch
```

Both are direct BIP-39 vectors; Sisoul reuses them so that any
BIP-39-conformant library passes these by construction.

### TV-3: soul_id derivation

```
mnemonic: abandon abandon abandon abandon abandon abandon
          abandon abandon abandon abandon abandon about
expected seed64 SHA-256 fingerprint:
  c5 52 57 c3 60 c0 7c 72  ... (full bytes in test-vectors.json)
expected soul_id:
  did:sisoul:5DGmf2yz... (full 50-char base58 in test-vectors.json)
```

### TV-4: Vault unlock with mismatched mnemonic

Given a vault for soul_id A and a mnemonic that derives soul_id B,
`sisoul restore` SHALL exit non-zero with
`MnemonicVaultMismatchError` and SHALL NOT have modified any byte
of the vault directory.

### TV-5: Restore latency

On the reference Android target (see §10), a synthetic test runs the
full `sisoul restore` path against a 94 KiB warm vault and asserts
wall time < 5,000 ms (p95 across 50 runs).

### TV-6: Rotation atomicity

A rotation that is interrupted with `SIGKILL` between class file
re-encryptions MUST leave the vault either fully under the old
mnemonic or fully under the new mnemonic. Mixed-key states are not
allowed; implementations achieve this with a two-phase commit:

```
1. Write all <class>.enc.next files under K2.
2. fsync each, fsync the directory.
3. Write <vault>/.rotation-marker containing the new salt.
4. fsync the directory.
5. For each class, rename <class>.enc.next over <class>.enc.
6. Remove <vault>/.rotation-marker.
```

If the daemon starts and observes `.rotation-marker`, it completes
step 5 before doing anything else; if any `.next` file is missing,
it rolls back by deleting all `.next` files and removing the marker.

Test-vectors.json contains 16 crash-injection scenarios; all 16 must
end in either pure-old or pure-new state.

## Reference Implementation

```
src/sisoul/identity/
├── __init__.py
├── did.py          # base58btc, did:sisoul:* helpers
└── seed.py         # BIP-39 generate/validate, soul_root, soul_id
```

CLI surface (`src/sisoul/cli_commands/`):

* `sisoul init`           → §4
* `sisoul restore [...]`  → §7
* `sisoul export`         → §8
* `sisoul import <file>`  → §8
* `sisoul rotate`         → §9
* `sisoul whoami`         → prints soul_id, device_id, last unlock time

The implementation includes a `pure-python` BIP-39 implementation
(no dependency on cryptocurrency wallet libraries) and matches the
official BIP-39 English test vectors bit-for-bit.

## Security Considerations

### Threat model

The single most important fact about this protocol: **a mnemonic
disclosed in cleartext is total compromise.** There is no
"administrator override", no "delayed-revoke window", no "freeze the
soul" capability. Once an adversary holds the mnemonic, they can:

* Read every vault on every device, past, present, and future, until
  the user rotates.
* Read any encrypted vault backup ever made.
* Impersonate the user on the friends graph (PIP-004) to anyone who
  knew them only by `did:sisoul:...`.
* Read any future content the user adds to the soul, as long as the
  adversary maintains access to vault bytes (which they may obtain
  via any backup mirror).

This is by design. Sisoul deliberately optimizes for "user owns their
soul completely" rather than "vendor can recover the soul for you".
The trade-off MUST be communicated to users in plain language, before
they accept the mnemonic.

### Mnemonic-loss vs mnemonic-leak

The two failure modes are symmetric in their consequences (you lose
everything) but asymmetric in their attack surface:

* **Loss**: the soul becomes inert; nobody can decrypt the vault.
  No third party gains anything. Users SHOULD store the mnemonic in
  ≥ 2 physically separate locations to prevent this.
* **Leak**: the adversary gains everything. Even physical destruction
  of all the user's devices does not help if any backup mirror
  remains accessible.

Therefore the storage recommendations differ:

| Goal      | Recommendation                                        |
|-----------|-------------------------------------------------------|
| Anti-loss | At least 2 copies, geographically separated           |
| Anti-leak | None of those copies should be photographable in passing, |
|           | OCR-scannable from a backup, or in cloud-synced notes |

The reference UX recommends: one steel plate in a fireproof safe,
one paper copy in a sealed envelope at a trusted friend or bank
safe-deposit box. We explicitly recommend *against* storing in any
password manager that auto-syncs to a cloud service operated by a
third party; the entire point of Sisoul is to not have that party.

### Recommended physical storage

* Write each word in clearly-printed block capitals.
* Number each word 1–12 next to it.
* Write the truncated `soul_id` (first 8 base58 chars) on the same
  paper, so a future you can disambiguate among multiple souls.
* Do NOT add any AI-tool-specific metadata or personally-identifying
  notes; a stolen mnemonic-paper that does not say "Sisoul" on it is
  marginally harder to convert into compromise than one that does.

### Catastrophic compromise response

If a user suspects the mnemonic has leaked:

1. Run `sisoul rotate` on every device that still has access to the
   vault (this re-encrypts the on-disk vaults under the new
   mnemonic).
2. Mentally treat all vault content from before the rotation as
   "potentially read by adversary". The rotation does not erase the
   old ciphertexts that backups already contain.
3. Inform friends in `friends.enc` via PIP-004 §9's
   "compromise notification" path; they may wish to revoke
   permissions granted to the old `soul_id`.
4. Optionally, run `sisoul start-new-soul` which generates a fresh
   mnemonic, fresh soul_id, fresh empty vault, and emits a signed
   "succession" attestation linking the old soul_id to the new one
   on-chain (PIP-004 §11). The succession is informational; nothing
   in the protocol forces friends to accept it.

### Side channels

The mnemonic spends time as cleartext in three places: terminal input
buffer, daemon process memory during PBKDF2, and any clipboard the
user used. Mitigations:

* Hidden-input prompt by default; never echoed.
* `mlock` the entropy buffer; `madvise(MADV_DONTDUMP)` on Linux.
* CLI emits a warning if it detects pasted input on macOS via the
  `pbpaste` size-change heuristic, suggesting the user clear their
  clipboard manually.
* Shell-history avoidance: when invoked as `sisoul restore` *without*
  the mnemonic on the command line, no part of the secret enters
  history. The mnemonic-on-command-line form is supported for
  scripting but SHALL emit a clear "consider not doing this" warning.

### Cryptanalytic concerns

* **BIP-39 weaknesses**: the 4-bit checksum is not a strong
  authentication tag and is not designed to detect deliberate
  forgery, only transcription error. We accept this because a
  deliberately-forged mnemonic is itself just a mnemonic; whoever
  forged it knows it.
* **PBKDF2-SHA512 in 2030+**: 100k rounds may become inadequate as
  hardware advances. The `kdf.rounds` field in PIP-001 manifest is
  versioned so a future PIP can raise it without breaking the wire
  format. Implementations SHOULD support transparent re-derivation
  on next unlock when manifest's `kdf.rounds` ≠ daemon's default.

### Identity equivocation

A user MAY run multiple Sisoul daemons each with a distinct soul on
the same device (separate `~/.sisoul-<label>/vault/` directories).
This is a feature for users who want, e.g., a personal soul and a
work soul. The protocol does not link the two; from the outside they
are indistinguishable strangers. We document this so it cannot be
mistaken for a bug.

## Appendix A: First-run wizard walkthrough

```
$ sisoul init
Welcome to Sisoul. We're going to create your soul.

Step 1/3 — Generating 128 bits of entropy from your operating system.
           done.

Step 2/3 — Your 12-word recovery mnemonic:

   1. army     2. van        3. defense    4. carry
   5. jealous  6. true       7. garbage    8. claim
   9. echo    10. media     11. make      12. crunch

   Write these down on paper. Store in two separate physical locations.
   We will never show them again. We cannot recover them for you.

Step 3/3 — To confirm, please type three words by their number:
   What is word #4?  carry
   What is word #9?  echo
   What is word #11? make

   Great. Your soul_id is:
       did:sisoul:5DGmf2yz9F3aRX...

   The daemon will auto-start on login. Run `sisoul whoami` any time.
$
```

## Appendix B: Wordlist and locale notes

The English wordlist is the canonical BIP-39 reference (2048 words,
SHA-256 `2f5eed53...`). Sisoul v1 ships English only; translations
will follow the BIP-39 official lists for the corresponding locale
and the implementation MUST NFKD-normalize the mnemonic in the locale
wordlist's character set before any KDF input. A user with an English
mnemonic can restore on any locale's daemon; the daemon detects the
language from the first word's wordlist membership.

## Appendix C: Comparison with related schemes

| Property                     | Sisoul v1 (this PIP) | Bitcoin BIP-39 wallet | Apple iCloud Recovery Key |
|------------------------------|----------------------|-----------------------|---------------------------|
| Word source                  | BIP-39 EN            | BIP-39 EN             | base32 28 chars           |
| Words / chars                | 12                   | 12 / 24               | 28                        |
| Has checksum                 | yes                  | yes                   | yes (Verhoeff)            |
| Reusable mnemonic across apps| no (different KDF)   | yes (BIP-32 paths)    | no                        |
| Cloud-side custody           | no                   | no                    | yes (Apple holds half)    |
| Restore time goal            | < 5 s                | n/a                   | n/a                       |
| 5-second restore implemented | yes                  | n/a                   | n/a                       |

## Appendix D: Operational FAQ

**Q: Can two people share one mnemonic to share a soul?**
Yes, technically; the protocol cannot tell two physical people apart.
We strongly recommend instead using the friends-and-skills sharing in
PIP-004, which is designed for granular permissions and revocation.
Sharing a mnemonic gives the other party irrevocable, total access
and is appropriate only for "spouse with full delegation" trust
levels.

**Q: What if I want to migrate from another personal-AI system?**
Sisoul does not specify importers for proprietary systems. The
reference repo contains adapters for Claude memory, Codex
AGENTS.md, Cursor rules, and OpenCode config under
`src/sisoul/cli_commands/import_from_*.py`. These adapters generate
vault deltas; you still need a mnemonic for the destination soul.

**Q: Why no recovery via "security questions"?**
Because security questions are low-entropy and socially engineerable.
We considered SSS (Shamir's Secret Sharing) and may add it in PIP-006;
the v1 protocol deliberately ships without it to keep the mental
model "the 12 words ARE the soul" pristine.

**Q: How do hardware wallets fit in?**
A hardware wallet that exposes its BIP-39 seed (most do not, by
design) would let you derive the Sisoul soul on demand. Sisoul does
not currently integrate with hardware-wallet APIs because most
wallets refuse to expose the raw seed to a host. A future PIP may
specify a Sisoul-specific BIP-32 derivation path that hardware
wallets could implement natively, allowing the host to never see the
mnemonic.

**Q: How is the daemon auto-start handled across OSes?**
* macOS: a per-user LaunchAgent plist at
  `~/Library/LaunchAgents/io.sisoul.daemon.plist`, `RunAtLoad=true`,
  `KeepAlive=true`.
* Linux: a systemd-user service unit at
  `~/.config/systemd/user/sisoul-daemon.service`, started via
  `systemctl --user enable --now`.
* Windows: a per-user Task Scheduler entry registered at logon.
* Android (termux): a `~/.termux/boot/sisoul-daemon` shell stub.

The daemon binds only to `127.0.0.1` and the per-user Unix socket;
it does not require root.

**Q: How do I prove to a friend that I am the same soul on a new device?**
By signing a fresh nonce with the `soul_root`-derived signing key
(see PIP-004 §3 for the exact construction). Your friend's daemon
verifies the signature against the public key it already has for
your `soul_id`. The fact that you possess the signing key proves
mnemonic possession, and the mnemonic was the secret that defined
the soul; therefore you are the soul.

## Appendix E1: Wire format for LAN peer-bootstrap

When a fresh device claims a `soul_id` and a LAN peer with the matching
soul wants to stream its encrypted vault to the newcomer, the wire
exchange is the following over a libsodium box channel (cf. PIP-004
§4):

```
newcomer  → peer : HELLO_RESTORE { soul_id, device_pubkey, nonce_n }
peer      → newcomer : CHALLENGE { nonce_p }
newcomer  → peer : CHALLENGE_RESP { sig = Sign(soul_root_signkey,
                                              nonce_n || nonce_p) }
peer      → newcomer : VERIFY_OK | VERIFY_FAIL
                       (signature checked against newcomer's claimed
                        soul_id's public signing key, which the peer
                        independently re-derives from its OWN mnemonic
                        — i.e. the proof that newcomer holds the same
                        mnemonic the peer holds)
peer      → newcomer : VAULT_STREAM { tar.zst of vault/ }
newcomer  → peer : ACK
```

The bytes streamed are the encrypted vault. The newcomer cannot read
them without separately deriving the master key from its own mnemonic.
This is intentional: even an attacker who somehow forces a LAN peer to
send vault bytes still cannot read them without the mnemonic.

The PIP-001 `manifest.json` is sent first; the newcomer verifies
`vault_schema_version` is supported before accepting the rest.

## Appendix E2: Reference: what a "lifetime of a soul" looks like

```
Day 0      sisoul init               (mnemonic generated)
Day 0+5s   soul_id assigned, vault initialized, daemon registered
Day 1..N   normal use: vault grows ~1 KiB/day for an active user
Day 30     `sisoul export` taken to a USB drive, stored offline
Day 90     user upgrades laptop:
           - on new laptop: `sisoul restore` from LAN peer (5 s)
           - on old laptop: daemon kept running until new is verified
Day 180    suspected mnemonic leak (sticky note photographed):
           - `sisoul rotate` on every device
           - friends notified via PIP-004 §9
Day 365    user dies; estate executor:
           - locates paper mnemonic in safe-deposit box
           - on a fresh machine, `sisoul restore`
           - reads `goals.enc` for unfinished items
           - executor cannot impersonate user going forward because
             the executor's actions are recorded against the same
             soul_id, which is a feature of the on-chain ledger
             (PIP-004 §11) being append-only.
```

## Appendix F: Glossary

* **BIP-39**: Bitcoin Improvement Proposal 39, the de-facto standard
  for mnemonic seed phrases. https://github.com/bitcoin/bips/blob/master/bip-0039.mediawiki
* **Mnemonic**: in this PIP, a 12-word English BIP-39 mnemonic.
* **Seed**: the 64-byte output of the BIP-39 mnemonic→seed PBKDF2
  step. Distinct from "master key" (PIP-001 §3.1) and `soul_root`
  (this PIP, §3).
* **soul_id**: the public DID identifying a soul.
* **device_id**: a per-device DID stored in PIP-001's
  `manifest.json`; orthogonal to `soul_id`. One soul can have many
  devices.
* **Vault bytes**: the contents of `~/.sisoul/vault/` on disk;
  encrypted, opaque to anyone without the mnemonic.
* **Restore**: the act of loading vault bytes plus mnemonic on a
  device to bring the soul live there.
* **Rotation**: re-encrypting the vault under a new mnemonic; old
  mnemonic ceases to unlock new ciphertext but still unlocks old
  ciphertext that adversaries may have copied.

## Copyright Waiver

Copyright and related rights for content in this document are waived
via [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/).
