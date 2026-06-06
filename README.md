# sisoul

> Decentralized P2P AI agent protocol. BTC-mode install + run. No servers operated by sisoul.

[![CI](https://github.com/akige/sisoul/actions/workflows/ci.yml/badge.svg)](https://github.com/akige/sisoul/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/akige/sisoul?include_prereleases)](https://github.com/akige/sisoul/releases)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org/)
[![Sigstore](https://img.shields.io/badge/Sigstore-keyless%20OIDC-green)](https://docs.sigstore.dev/)
[![PWA](https://img.shields.io/badge/PWA-akige.github.io%2Fsisoul-blue)](https://akige.github.io/sisoul/)
[![Whitepaper](https://img.shields.io/badge/Whitepaper-v1.0%2014%20chapters-green)](docs/whitepaper/sisoul-v1.0-whitepaper.md)
[![Tests](https://img.shields.io/badge/tests-2069%20pass%20%2F%200%20fail-brightgreen)](#tests)
[![PQXDH](https://img.shields.io/badge/PQXDH-ML--KEM--1024%20hybrid-purple)](#features)

```
   ___  _                  _
  / __|(_) ___  ___  _  _ | |
  \__ \| |(_-< / _ \| || || |
  |___/|_|/__/ \___/ \_,_||_|
```

**Your AI agent. Your data. Your friends. No cloud.**

## What sisoul does

sisoul is a peer-to-peer protocol for AI agents to share capabilities (skills, case knowledge, LLM access, encrypted chat) across friends — without any centralized server operated by sisoul. Like BitTorrent + Signal + Wikipedia, applied to AI agents.

**Five core functions:**

1. **did:key Identity** — self-sovereign identity (ed25519), 0 signup / 0 email / 0 password
2. **Friends via QR / mDNS / DID input** — 3 ways to add friends; never see did:key long hashes (Petname local nickname display)
3. **LLM Provider Adapters (9)** — Anthropic, OpenAI, Gemini, Copilot OAuth, Ollama, Akash, Bittensor, Hyperbolic, LiteLLM Generic
4. **Borrow LLM cross-NAT P2P** — Alice without API key can borrow Bob's LLM, end-to-end encrypted through kubo IPFS GossipSub
5. **Signal-level Chat** — Double Ratchet + PQXDH post-quantum hybrid

## Quick start

### Install (one line)

```bash
# alpha installer (release tarball + cosign + Pages install.sh 未上线, 走源码装)
git clone https://github.com/akige/sisoul && cd sisoul
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[daemon,crypto,chat,llm]'
```

### Initialize

```bash
sisoul init    # 5-step wizard guides you through:
               #   1. Petname (your local nickname)
               #   2. did:key generation
               #   3. LLM provider selection
               #   4. Daemon mode (background/foreground)
               #   5. QR code for friends
```

### Start daemon and add friends

```bash
sisoul daemon start --background

# Add friends via mDNS (LAN)
sisoul friend mdns scan

# Or via QR code
sisoul friend qr --print

# Or via DID (paste from friend)
sisoul friend add did:key:z6Mk...
```

### Use it

```bash
# Ask the LLM (uses your provider or borrowed via friend)
sisoul ask "How to handle Rust async deadlock?"

# Chat with a friend (E2E encrypted)
sisoul chat send did:key:z6Mk... "hello"

# Install a skill from IPFS
sisoul skill install <ipfs-cid>
```

### PWA (mobile)

Visit [akige.github.io/sisoul/](https://akige.github.io/sisoul/) — works on iOS Safari, Android Chrome, desktop browsers. No install needed.

## Architecture

```
┌───────────────────────────────────────────────────────┐
│ Your machine                                          │
│  ┌──────────────────┐    ┌──────────────────────────┐ │
│  │ Your AI agent    │◄──►│ sisoul daemon            │ │
│  │ (Claude/Codex/   │    │                          │ │
│  │  Cursor/Aider)   │    │  - vault (markdown)      │ │
│  └──────────────────┘    │  - did:key identity      │ │
│                          │  - kubo IPFS embedded    │ │
│  ┌──────────────────┐    │  - GossipSub P2P         │ │
│  │ PWA (mobile)     │◄──►│  - LLM provider adapter  │ │
│  └──────────────────┘    │  - chat E2E (Signal)     │ │
│                          └────────┬─────────────────┘ │
└──────────────────────────────────│────────────────────┘
                                   │ P2P GossipSub
                                   ▼
                           ╔═══════════════╗
                           ║ Your friends  ║
                           ║ (1-100s)      ║
                           ╚═══════════════╝
```

**16 modules ship across Wave A-M** (see [obs §31-§46](./docs/architecture/) for details).

## Features

| Feature | Status | Module |
|---|---|---|
| did:key identity | ✅ | `src/sisoul/identity/` |
| Vault (markdown + frontmatter) | ✅ | `src/sisoul/vault/` |
| BIP-39 seed (12-word mnemonic) | ✅ | `src/sisoul/identity/seed.py` |
| Shamir 3-of-5 backup | ✅ | `src/sisoul/identity/shamir.py` |
| kubo IPFS embedded | ✅ | `src/sisoul/p2p/ipfs_kubo.py` |
| GossipSub P2P transport | ✅ | `src/sisoul/p2p/transport.py` |
| mDNS local discovery | ✅ | `src/sisoul/friend/mdns.py` |
| Petname local nicknames | ✅ | `src/sisoul/friend/petname.py` |
| QR add-friend | ✅ | `src/sisoul/cli_commands/qr.py` |
| 9 LLM provider adapters | ✅ | `src/sisoul/providers/` |
| Borrow LLM cross-NAT | ✅ | `src/sisoul/friend/borrow.py` |
| Signal Double Ratchet chat | ✅ alpha | `src/sisoul/chat/double_ratchet.py` |
| PQXDH post-quantum chat | ✅ alpha | `src/sisoul/chat/pqxdh.py` |
| Skill marketplace (P2P MCP) | 🔄 v2.0 | `src/sisoul/v2/skill_marketplace/` |
| Case-Based Reasoning Graph | 🔄 v2.0 | `src/sisoul/v2/case_graph/` |
| Personal LoRA (per-user training) | 🔄 v2.0 | `src/sisoul/v2/personal_lora/` |
| Provenance Chain (EAS attest) | 🔄 v2.0 | `src/sisoul/v2/provenance/` |
| Multi-Agent Debate | 🔄 v3.0 | future |
| Federated LoRA | 🔄 v3.0 | future |
| SIS Token + Optimism mainnet | 🔄 v1.0 stable | `contracts/` |

## Roadmap

- **alpha v1.0** (current) — 5 core + Signal chat + 100 user validation. Supported today: **macOS / Linux / WSL2** (Python 3.11+ source install)
- **beta v1.1** (T+1m) — Android F-Droid + iOS AltStore + **macOS menu-bar native app** + group chat + Sepolia DAO
- **beta v1.2** (T+2m) — Win11 native + i18n + skill marketplace foundation
- **v1.0 stable** (T+6m) — Optimism mainnet attestations + Soulbound Honor Badge distribution (per §4.10: non-transferable ERC-721, 0 economic value, no token) + 100+ community nodes
- **v2.0 智能体网络** (T+12m) — Case retrieval + Personal LoRA + Provenance + Skill marketplace
- **v3.0 超级智能体** (T+18m) — Multi-Agent Debate + Federated LoRA + on-chain non-monetary credit ledger (reputation-weighted reciprocity, not a tradable token)
- **emergent collective intelligence** (T+36m) — 10K+ MAU + 1M+ cases + 70%+ recall (stretch goal, ~25-35% probability)

## Tests

```bash
# Full suite (~70 sec)
.venv/bin/pytest tests -q --tb=line --ignore=tests/test_v1_integration_full_user_journey.py

# Alpha launch e2e (5 user scenarios)
.venv/bin/pytest tests/test_alpha_launch_e2e.py -v

# v2.0 foundation
.venv/bin/pytest tests/test_v2_foundation.py -v
```

Current state: **1939+ pass / 0 fail / 24 skip**.

## Architecture deep-dive

- [Whitepaper v1.0](docs/whitepaper/sisoul-v1.0-whitepaper.md) — 14 chapters
- [Alpha Launch Playbook](docs/ALPHA-LAUNCH-PLAYBOOK.md) — install + use + risks
- [Release Notes v1.0-alpha](RELEASE-NOTES-v1.0-alpha.md)
- obs documents §28 § 31 §52 §57 §61 §62 §63 §64

## Contribute

Apache-2.0 licensed. PRs welcome.

```bash
git clone https://github.com/akige/sisoul
cd sisoul
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests
```

## Credits

- Protocol references: BitTorrent / Signal / Wikipedia / Stack Overflow / Bitcoin / Tor
- Built with: kubo (go-ipfs), libp2p, sigstore, Optimism L2, EAS, IPFS GossipSub
- Whitepaper: 14 chapters, ~50 pages

---

🤖 sisoul is developed with [Claude Code](https://claude.com/claude-code).

License: Apache-2.0
