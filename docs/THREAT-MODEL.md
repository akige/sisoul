# sisoul Threat Model (v1.0-alpha)

STRIDE-based threat analysis for sisoul's decentralized P2P AI agent protocol.
For 5 alpha core features: did:key / Friends / 9 LLM adapters / Borrow LLM / Signal chat.

> **Trust model**: sisoul operates no servers. The user controls their vault, keys, and IPFS node. Threats below assume a hostile network + curious peers + compromised LLM provider.

## 1. Asset inventory

| Asset | Sensitivity | Where |
|---|---|---|
| Master seed (BIP-39) | **Critical** | `~/.sisoul/seed.txt` (chmod 600) |
| did:key private (Ed25519) | **Critical** | `~/.sisoul/did_key.json` (XChaCha20 encrypted) |
| Friend list (DIDs + petnames) | High (social graph) | `~/.sisoul/friends/*.json` |
| Chat ratchet state (per peer) | **Critical** | `~/.sisoul/chat/sessions/*.json` |
| Bob's LLM API key (when sharing) | **Critical** | `~/.sisoul/borrow_provider.json` |
| Case Graph (v2 personal knowledge) | High | `~/.sisoul/cases/*.json` |
| Personal LoRA weights (v2) | High | `~/.sisoul/lora/*.safetensors` |
| Chat plaintexts | High | RAM only (forward-secret on disk) |

## 2. Threat actors

| Actor | Capability | Goal |
|---|---|---|
| **Passive network observer** | Sees IPFS GossipSub traffic | Identify peers, count messages |
| **Active MITM** | Can drop/replay/inject GossipSub messages | Forge friend requests, replay borrow |
| **Curious lender (Bob)** | Has API key Alice borrows | Read Alice's prompts (plaintext to Bob's daemon) |
| **Compromised IPFS bootstrap** | Default IPFS Foundation peers | Trace peer-to-peer connections |
| **Compromised LLM provider** | Anthropic/OpenAI/etc. | Read all prompts that pass through them |
| **Local malware on Alice's box** | Code execution as Alice | Steal seed/keys |
| **Future quantum adversary** | Breaks Curve25519 with QC | Decrypt historical ciphertext |

## 3. STRIDE per feature

### 3.1 did:key Identity

| Threat | Mitigation |
|---|---|
| **S**poofing: Forge "I am Alice" | Ed25519 signature on every message; verify against did:key |
| **T**ampering: Modify Alice's pubkey in friend record | did:key is self-certifying (key=identifier); change=detected |
| **R**epudiation: Alice claims she didn't send msg | Signed with Alice's Ed25519 sk; non-repudiation by default |
| **I**nfo disclosure: did:key reveals network identity | **Accepted**. did:key IS public identity. For pseudonymity, generate fresh did per identity. |
| **D**oS: Flood signature verifications | Rate limit verification queue (default 100/sec/peer) |
| **E**oP: Steal Alice's private key | XChaCha20-Poly1305 vault + libsodium SecretBox. Master seed never leaves disk. |

### 3.2 Friends (QR + mDNS + DID)

| Threat | Mitigation |
|---|---|
| **S**poofing: Evil twin Bob | QR exchange shows did:key fingerprint; verify in person |
| **T**ampering: MITM modifies multiaddr | Multiaddr signed by did:key sk |
| **I**nfo disclosure: mDNS leaks LAN presence | mDNS opt-in; off by default; only on trusted networks |
| **D**oS: Flood friend requests | Local rate limit; allowlist mode for paranoid users |
| **E**oP: Add unauthorized friend | All friend records signed; PWA confirms before add |

### 3.3 Borrow LLM (Bob lends API key to Alice)

| Threat | Mitigation |
|---|---|
| **S**poofing: Eve poses as Alice to use Bob's quota | All borrow requests signed by Alice's did; Bob verifies signature against his friend allowlist |
| **T**ampering: MITM modifies prompt | Encrypted with PQXDH shared secret; tamper = decrypt fail |
| **R**epudiation: Alice denies the prompt | Signed; Bob has audit log |
| **I**nfo disclosure: Bob sees Alice's prompts | **Accepted**. By design Bob's daemon decrypts to forward to provider. Trust = social. Document clearly. |
| **I**nfo disclosure: LLM provider sees prompts | **Accepted**. Provider already has full visibility. sisoul does not add trust assumption here. |
| **D**oS: Alice abuses Bob's quota | Bob's daemon: `borrow_policy.json` per-friend rate limit + monthly token cap |
| **E**oP: Alice bypasses Bob's rate limit | Server-side enforcement on Bob's daemon |

### 3.4 Chat (Signal-grade with PQXDH + Double Ratchet)

| Threat | Mitigation |
|---|---|
| **S**poofing: Eve impersonates Alice in chat | Initial handshake signed by Alice's did:key; established session keyed to that did |
| **T**ampering: Modify chat ciphertext | AES-256-HMAC-SHA256; tamper = MAC fail |
| **I**nfo disclosure: Decrypt past messages after key compromise | Double Ratchet forward secrecy: past message keys discarded after use |
| **I**nfo disclosure: Decrypt future messages after compromise | Double Ratchet post-compromise security: new DH on every message |
| **I**nfo disclosure: Quantum adversary | PQXDH hybrid: X25519 + ML-KEM-1024. Adversary breaks both to read. |
| **R**eplay: Replay old chat msg | Per-message AEAD nonce + sequence number in header |
| **D**oS: Flood chat messages | Rate limit per-peer; ratchet step expensive enough to throttle |

### 3.5 Pre-key bundle distribution

| Threat | Mitigation |
|---|---|
| **T**ampering: Bob's pre-key replaced by Eve's | Pre-key bundle signed by Bob's did:key sk |
| **I**nfo disclosure: Pre-keys reveal who's online | 24h refresh; published whether online or not |
| **D**oS: Flood pre-key topic | Local cache; ignore duplicates by signature |

## 4. Threats NOT covered (out of scope)

- **Endpoint compromise**: If Alice's machine is rooted, all bets off. Use disk encryption + 2FA + hardware key for vault unlock.
- **Coercion / rubber-hose attacks**: Shamir 3-of-5 helps split keys across trustees but not against legal coercion. Plausible deniability not provided.
- **Side-channel attacks**: Timing / power analysis of crypto ops. Out of scope for alpha; defer to libsodium hardening.
- **Supply chain**: Compromised Python deps. Mitigation: cosign sigstore signing of releases; pip dep hashes recommended.
- **PWA hosting**: GitHub Pages can be taken down. Mitigation: PWA also deployable to IPFS+ENS (`pwa-ipfs-deploy.sh`).
- **DAO governance attacks** (v2+): Sybil, vote-buying. Use SBT (non-transferable) + 1p1v + quadratic.

## 5. Cryptographic primitives summary

| Use | Primitive | Library |
|---|---|---|
| Identity sign | Ed25519 | libsodium / PyNaCl |
| Vault encrypt | XChaCha20-Poly1305 | libsodium SecretBox |
| Key derivation | HKDF-SHA256 / HKDF-SHA512 | hashlib |
| Mnemonic seed | BIP-39 PBKDF2 (2048 iter) | mnemonic |
| Chat handshake | PQXDH (X25519 + ML-KEM-1024) | PyNaCl + kyber-py |
| Chat ratchet | Signal Double Ratchet | DoubleRatchet (Python) |
| Chat msg AEAD | AES-256-HMAC-SHA256 | DoubleRatchet |
| TLS to bootstraps | TLS 1.3 | system |
| Release sign | cosign / sigstore | sigstore-python |

## 6. Audit trail (CLI commands generate logs)

```bash
sisoul borrow request --from alice --to bob --model claude-opus-4-7
# → audit log entry with: timestamp, from_did, to_did, model, hash of prompt (not plaintext)
```

Audit logs at `~/.sisoul/audit/<YYYY-MM-DD>.jsonl`. Bob's daemon also logs on receive.

## 7. Open issues + future hardening

- [ ] **Pre-key one-time use**: alpha uses long-lived bundles; v1.x add OPKs (one-time prekeys) per X3DH spec
- [ ] **MLS group chat**: v2.0 add MLS for >2 participants
- [ ] **Bob-side prompt filtering**: opt-in keyword/length checks before forwarding to provider
- [ ] **Cold storage Shamir UI**: PWA wizard to print 5 shares as QR codes
- [ ] **Hardware key (YubiKey) for vault unlock**: alpha uses passphrase only
- [ ] **Sybil-resistant DAO** (v2+): SBT + 1p1v + quadratic vote
- [ ] **Selective disclosure**: BBS+ signatures for partial credential reveal (v3+)
- [ ] **Onion routing for Borrow**: Tor / nym for prompt source anonymity from Bob

## 8. Bug bounty (post-alpha)

After 10+ alpha users, propose bug bounty via Immunefi or similar:
- Critical (privkey extraction, cipher break): $5k+
- High (forge identity, MITM): $1k
- Medium (DoS, info leak): $200
- Low (UX confusion that causes wrong sec decision): $50

Funded via SIS treasury (v3.0).

## 9. Responsible disclosure

Per [SECURITY.md](../SECURITY.md): security@sisoul.io (when domain registers) or open private GitHub Security Advisory.
90-day disclosure window for HIGH+ CVE-class issues.

---

🔐 **Threat model is a living doc.** Update on every protocol change. Reviewed each minor version.
