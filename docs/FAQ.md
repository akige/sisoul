# sisoul FAQ

Frequently asked about sisoul alpha v1.0.

## Why "sisoul"?

silicon + soul. Decentralized AI agent protocol. Pronounced like "see-soul". Domain `sisoul.eth` (planned T+6m), Twitter `@sisoul_io` (TBD), Farcaster `/sisoul`.

## How is sisoul different from ChatGPT / Claude / Cursor?

sisoul is a **protocol layer**, not a chat product. It runs as a local daemon and:
- Stores your data (cases, skills, friends) on **your** machine
- Lets you **share** with friends peer-to-peer (no sisoul server)
- Lets you **borrow** a friend's LLM API if you don't have a key
- Lets your agent learn over time (cases + LoRA in v2.0)

You still use ChatGPT/Claude/Cursor for the LLM itself. sisoul wraps them.

## Is sisoul a blockchain?

**Mostly no.** sisoul is P2P (IPFS GossipSub) for case/skill/chat. Only the optional parts use chain:
- EAS attestation on Optimism L2 (provenance, ~$0.01 tx)
- DAO governance (Sepolia testnet alpha, Optimism mainnet T+6m)
- SIS token for skill micropayments (v3.0, T+15m)

You can use sisoul **without ever touching a blockchain** if you want.

## Is sisoul censorship-resistant?

For chat/borrow LLM: yes (P2P direct, E2E encrypted, kubo IPFS public network).
For provenance: requires Optimism L2 (geo-restricted by RPC providers, fallback to self-hosted node).
For install.sh: GitHub Releases primary, IPFS gateway + Codeberg fallback (alpha).

A user behind GFW can run sisoul (verified WSL2 + tx-jp cross-NAT in Wave F).

## Do I need to know crypto / Web3?

**No.** Default install + use needs nothing crypto-specific. The crypto bits are:
- `did:key` is auto-generated; you don't need to "buy" it
- No wallet required for alpha
- No tokens to swap or stake

If you want to verify attestations on EAS, you need an Etherscan-like explorer link (we provide it).

## What does the LLM borrow actually do?

Alice has no Anthropic API key. Bob does. They're friends.
1. Alice runs `sisoul borrow request did:key:z6MkBob`
2. Bob's daemon sees the request (E2E encrypted)
3. Bob's policy auto-approves (or shows notification)
4. Alice's `sisoul ask "..."` goes: Alice → P2P encrypted → Bob's daemon → Bob's Anthropic key → Anthropic → response back
5. Alice never sees Bob's key. Bob's quota gets used.

Cost split:
- Foundation: free between friends (mutual reciprocity expectation)
- v3.0 (T+15m): auto SIS micropayment (~0.01 SIS / call)

## What if my friend goes offline?

- Chat: stored on `sisoul-store` (P2P relay) for offline pickup
- LLM borrow: fails immediately (need synchronous)
- Case retrieval: still works (you have local cache)

## What about my data privacy?

- Vault is `~/.sisoul/` (your machine only by default)
- Chat is E2E encrypted (Signal Double Ratchet + post-quantum)
- Case sharing requires explicit `--share <did>` flag per case
- did:key is your only identifier (no email, name, IP logged centrally)

## Why post-quantum (PQXDH)?

NIST standardized ML-KEM-1024 in 2024. Quantum computers can't break it today. But adversaries can **harvest** your encrypted messages today and decrypt them in 5-10 years when quantum is ready. Hybrid (X25519 + ML-KEM-1024) means an attacker must break BOTH to read past messages.

Cost: ~1.5KB extra per handshake. Worth it.

## Why TF-IDF instead of ChromaDB embedding?

Foundation (alpha) uses TF-IDF for zero deps + works offline. v2.0 ship (T+8-10m) swaps to ChromaDB + sentence-transformers for true semantic retrieval.

Both have the same interface. We didn't want alpha to depend on a 200MB embedding model.

## Why no LoRA training in alpha?

Training a personal LoRA requires:
- 8GB+ GPU (most users don't have)
- 4-12 hours
- 1000+ conversations as dataset

Foundation ships the schema + stub. v2.0 末 (T+12m) ships real PEFT pipeline with federated learning option (borrow friend's GPU).

## Is this a SaaS? A startup?

Neither. sisoul is:
- Apache-2.0 open source
- No company behind it (Foundation possibly post-launch for legal + multisig holds)
- No VC funding
- No token sale (planned)
- SIS token (v1.0 stable T+6m) will be:
  - 40% airdrop (alpha + beta users)
  - 30% public RPGF / community
  - 20% Foundation 4-year vesting
  - 10% protocol treasury

## How do I trust the install.sh?

3 layers:
1. **sigstore** keyless OIDC signature on each release (verifiable by anyone via Rekor public log)
2. **Reproducible build** (planned T+6m): rebuild from source matches binary
3. **HSM 3-of-5 multisig** for Foundation keys (T+6m: 3 of 5 maintainers in geographically distinct locations)

Until then (alpha): GitHub Actions OIDC + cosign verify.

## What's the business model?

There isn't one yet, and we intentionally avoid pressure to invent one. Long-term options (DAO decides post-mainnet):
- RPGF (retroactive public goods funding) from Optimism / Gitcoin
- Optional pro features (advanced LoRA training, mainnet attestations bulk) charged in SIS
- Foundation services (hosted bootstrap relays, professional support contracts) → 100% to community treasury

We will not:
- Sell your data
- Inject ads
- Lock you into a domain we control (you can self-host PWA, IPFS, EAS)
- Charge for the protocol itself

## When v1.0 stable? When涌现?

| Version | Time |
|---|---|
| alpha v1.0 (now) | shipped |
| beta v1.1 | T+1 month |
| beta v1.2 | T+2 months |
| v1.0 stable | T+6 months |
| v2.0 智能体网络 | T+12 months |
| v3.0 超级智能体 | T+18 months |
| 集体智能涌现 (target) | T+36 months, 25-35% probability |

Even if 涌现 doesn't happen, alpha → v1.0 stable → v2.0 → v3.0 still ship as planned (engineering tasks, not network effects).

## How can I help?

| Skill | Where |
|---|---|
| Bug reports / docs | GitHub Issues + Discussions |
| Frontend (SolidJS / Tailwind) | PWA routes, mobile-first improvements |
| ML (PEFT / FedAvg) | v2.0 LoRA pipeline (T+10-12m) |
| Solidity audit | DAO + airdrop contracts before mainnet (T+5-6m) |
| Translators | Chinese/Japanese/Spanish/German docs |
| Skill authors | publish first 50 skills to bootstrap marketplace |
| Network operators | run public bootstrap relay (3+ needed) |

DM on Twitter @sisoul_io (post-launch) or Discord (post-launch).

## I have a different question

Open a GitHub Discussion at https://github.com/sisoul/sisoul/discussions, or read:

- `docs/QUICK-START.md` (5-min first run)
- `docs/ALPHA-LAUNCH-PLAYBOOK.md` (10 sections)
- `docs/whitepaper/sisoul-whitepaper-v1.0.md` (14 chapters, ~50 pages)
- `SECURITY.md` (threat model)
- `CONTRIBUTING.md` (dev setup)

CLI help: `sisoul cheatsheet` or `sisoul <command> --help`.
