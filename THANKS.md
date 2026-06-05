# Thanks

sisoul is built on the shoulders of giants. This document lists the people, projects, and protocols that made sisoul possible.

## Open source projects we depend on

### Identity & Crypto
- [pynacl](https://github.com/pyca/pynacl) — libsodium Python bindings (Ed25519, XChaCha20-Poly1305, SecretBox)
- [bip-utils](https://github.com/ebellocchia/bip_utils) — BIP-39 mnemonic + BIP-32 derivation
- [sslib](https://github.com/jqueiroz/python-sslib) — Shamir Secret Sharing GF(2^8)
- [python-doubleratchet](https://github.com/Syndace/python-doubleratchet) — Signal protocol implementation
- [kyber-py](https://github.com/jack4818/kyber-py) — ML-KEM-1024 pure Python (PQXDH backend)

### P2P & Storage
- [kubo (go-ipfs)](https://github.com/ipfs/kubo) — IPFS reference implementation
- [libp2p](https://libp2p.io) — modular P2P networking stack
- [Arweave Bundlr](https://bundlr.network) — permanent storage
- [zeroconf](https://github.com/jstasiak/python-zeroconf) — mDNS LAN discovery

### Blockchain
- [Optimism L2](https://www.optimism.io) — EAS attestation + DAO host
- [Ethereum Attestation Service (EAS)](https://attest.org) — provenance + reputation
- [ENS](https://ens.domains) — decentralized naming (planned T+6m)
- [sigstore](https://www.sigstore.dev) — keyless OIDC release signing + Rekor transparency log

### Stack
- [Python](https://python.org) 3.11 / 3.12
- [FastAPI](https://fastapi.tiangolo.com) — daemon HTTP server
- [typer](https://typer.tiangolo.com) — CLI framework
- [SolidJS](https://www.solidjs.com) — PWA reactive framework
- [Vite](https://vitejs.dev) — PWA build tool
- [TailwindCSS](https://tailwindcss.com) — PWA styling
- [httpx](https://www.python-httpx.org) — HTTP client (smoke tests)
- [pytest](https://docs.pytest.org) — test framework

## Protocol references & inspiration

- **BitTorrent** (2001) — P2P file distribution at scale; proved decentralization works for content
- **Signal** (2014) — Double Ratchet + X3DH gold standard for E2E encryption
- **Wikipedia** (2001) — collective knowledge; proved volunteer curation scales
- **Stack Overflow** (2009) — case-based reasoning; proved Q&A networks accumulate value
- **Bitcoin** (2009) — proof of decentralized financial network
- **Tor** (2002) — onion routing; threat model for anonymity research
- **OpenWhisperSystems / Moxie Marlinspike** — Signal protocol design
- **PQXDH paper** (Signal 2023) — post-quantum hybrid handshake
- **Anthropic / Constitutional AI** (2023) — multi-agent self-critique (Du 2023)
- **Google / Federated Learning** (McMahan 2017) — FedAvg foundation
- **Hu et al. / LoRA paper** (Microsoft 2021) — low-rank adaptation
- **Anthropic Claude Projects** (2024) — Case retrieval baseline (+25pp benchmark)

## Communities

- **Web3 / Ethereum** — for normalizing decentralized identity (DID)
- **IPFS Foundation** — for keeping the public IPFS network running
- **r/selfhosted / HackerNews** — for surface area where alpha launches
- **F-Droid / AltStore** — for open mobile distribution (beta v1.1 dependency)

## AI assistance

Most of sisoul code (including this document) was developed with:
- [Claude Code](https://claude.com/claude-code) by Anthropic
- Model: Claude Opus 4.7 (1M context)
- Subagent workflows: opus parallel workers for module ship

Acknowledged via `Co-Authored-By:` git trailers on every AI-assisted commit.

## Contributors

(Updated post-launch as PRs land. List initial maintainers + first 10 GitHub contributors here.)

### Founding maintainer

- @akige (GitHub) — initial design + Wave A-M ship + 6h sprint orchestration

### First contributors

(Add names as PRs merge: bug reporters, doc translators, skill authors, security researchers, design feedback.)

## How to be listed

Open a PR with your name + GitHub handle + 1-line description. Or get listed automatically by:
- Filing first bug report → "bug reporter"
- Submitting first PR → "code contributor"
- Translating docs → "translator (X language)"
- Publishing first skill → "skill author"
- Filing first valid security disclosure → "security researcher"

Anonymous contributions welcome (use pseudonym).
