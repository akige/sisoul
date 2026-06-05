# Security Policy

## Supported Versions

| Version | Supported | Notes |
|---|---|---|
| 1.0.0-alpha | ✅ | current alpha; security patches via x.y.z+1-alpha tags |
| 1.0.0-internal | ⚠️ | dev-only, will not receive patches |
| < 1.0.0 | ❌ | unreleased |

## Reporting a Vulnerability

**Do NOT** open public GitHub issues for security vulnerabilities.

### Channel

Email `security@sisoul.io` (will be set up post-launch; until then use any maintainer's GitHub-listed email + tag subject `[sisoul security]`).

### What to include

- sisoul version (`sisoul --version`)
- affected component (CLI / daemon / PWA / chat / etc.)
- minimal reproducer
- impact severity (P0 critical → P3 informational)

### Response timeline

| Severity | Initial response | Patch target |
|---|---|---|
| P0 (RCE, key exfil, P2P MITM) | 24h | 7 days |
| P1 (data loss, DoS, auth bypass) | 72h | 14 days |
| P2 (info leak, sandbox escape) | 7 days | 30 days |
| P3 (cosmetic, docs) | 14 days | next minor release |

## Security architecture (alpha)

### Threat model

| Adversary | What they can do | Mitigation |
|---|---|---|
| Curious peer | read messages on GossipSub topic | All chat E2E encrypted (Double Ratchet) |
| Network attacker (MITM) | observe/inject packets | TLS to bootstrap; libp2p Noise channel |
| Malicious skill author | run code on user machine | sigstore signing required (foundation: skip with explicit flag) |
| State actor (quantum future) | break ECDH retroactively | PQXDH hybrid (X25519 + ML-KEM-1024); decrypt of harvested messages requires breaking BOTH |
| Compromised release key | distribute malicious binary | sigstore keyless OIDC + Rekor transparency log (verifiable by all users) |
| Sybil attack | flood debate/reputation | Reputation-weighted routing; EAS attestations gate trust |

### What sisoul does NOT protect against (alpha)

- ❌ Endpoint compromise (your laptop infected): once attacker has your did:key private key, they impersonate you. Mitigation roadmap: Shamir 3-of-5 hardware-key backup (v1.0 stable T+6m).
- ❌ Global passive adversary watching all P2P traffic: traffic analysis can identify endpoints. Mitigation: Tor integration is a future option (not planned in v1.0 timeline).
- ❌ Browser-based PWA attacks (XSS on third-party domains): if you self-host PWA on a domain you don't control, that domain owner can MITM. Mitigation: use IPFS+ENS hosting (sisoul.eth, v1.0 stable) or run PWA from local daemon `localhost:9876/pwa`.

## Cryptographic stack

| Layer | Algorithm | Foundation backend |
|---|---|---|
| did:key identity | Ed25519 | pynacl (libsodium) |
| Vault encryption | XChaCha20-Poly1305 | pynacl |
| BIP-39 seed | secp256k1 + PBKDF2-HMAC-SHA512 | bip-utils |
| Shamir backup | GF(2^8) polynomial | sslib (Wave M) |
| Chat key exchange | PQXDH (X25519 + ML-KEM-1024) | kyber-py (real) or shim |
| Chat ratchet | Double Ratchet w/ Curve25519+HKDF-SHA512+AES-HMAC | python-doubleratchet |
| Provenance attest | EIP-712 typed data on EAS (Optimism L2) | sisoul.onchain |
| Release signing | sigstore keyless OIDC + Rekor log | cosign |

## Disclosure policy

After fix lands:
- CVE assigned (if severity ≥ P2)
- GitHub Advisory published
- Tweet/Discord/Farcaster notification
- Affected versions tagged with `vulnerable` label in release notes
- Credit to reporter in `THANKS.md` (unless they request anonymity)

## Pre-launch hardening checklist (T+0)

- [x] sigstore signing for all release binaries
- [x] No hardcoded secrets in source (grep'd: 0 `sk-`, `Bearer`, `password=`)
- [x] localhost-only daemon bind by default (`127.0.0.1`)
- [x] systemd MemoryMax=2G to limit blast radius
- [x] launchd plist intentional manual uninstall (avoid bootout corruption)
- [ ] Third-party dep audit (post-launch: `pip-audit` weekly cron)
- [ ] Penetration test (recruit 3rd party post-100 users)
- [ ] HSM 3-of-5 release multisig (v1.0 stable T+6m)
