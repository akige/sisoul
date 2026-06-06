# sisoul

> Decentralized P2P AI agent protocol. Your AI agent, your data, your friends, no cloud.

[![CI](https://github.com/akige/sisoul/actions/workflows/ci.yml/badge.svg)](https://github.com/akige/sisoul/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Tests](https://img.shields.io/badge/tests-2075%20pass-brightgreen)](#)

## What is sisoul?

sisoul is a peer-to-peer protocol for AI agents to share capabilities (skills, case knowledge, LLM access, encrypted chat) across friends — without any centralized server.

Think **BitTorrent + Signal + Wikipedia**, applied to AI agents.

## Install

```bash
curl -sSfL https://github.com/akige/sisoul/releases/latest/download/install.sh | bash
sisoul init
```

[Quick Start →](QUICK-START.md) · [中文 →](i18n/zh-CN/QUICK-START.md)

## Features

- **did:key identity** — self-sovereign, no signup
- **Friends via QR / mDNS / DID** — Petname display, never see hashes
- **9 LLM provider adapters** — Anthropic, OpenAI, Gemini, Copilot, ...
- **Borrow LLM cross-NAT P2P** — friend's API key, E2E encrypted
- **Signal-grade chat** — Double Ratchet + PQXDH (post-quantum hybrid)

## Status

- **alpha v1.0** (now) — 5 core + Signal chat shipped
- **beta v1.1** (T+1m) — Android F-Droid + iOS AltStore
- **v1.0 stable** (T+6m) — Optimism mainnet + SIS Airdrop
- **v2.0 智能体网络** (T+12m) — Case retrieval + Personal LoRA + Provenance + Skill marketplace
- **v3.0 超级智能体** (T+18m) — Multi-Agent Debate + Federated LoRA + SIS micropay
- **集体智能涌现** (T+36m) — bonus (25-35% probability)

## Architecture

```
┌──────────────────┐
│ Your AI agent    │◄──►┌──────────────────────────┐
│ (Claude/Codex/   │    │ sisoul daemon            │
│  Cursor/Aider)   │    │  - vault (markdown)      │
└──────────────────┘    │  - did:key identity      │
                        │  - kubo IPFS embedded    │
┌──────────────────┐    │  - GossipSub P2P         │
│ PWA (mobile)     │◄──►│  - LLM provider adapter  │
└──────────────────┘    │  - chat E2E (Signal)     │
                        └────────┬─────────────────┘
                                 │ P2P GossipSub
                                 ▼
                         ╔═══════════════╗
                         ║ Your friends  ║
                         ║ (1-100s)      ║
                         ╚═══════════════╝
```

[Full architecture →](ARCHITECTURE.md)

## Get involved

- [GitHub Issues](https://github.com/akige/sisoul/issues) — bug reports + features
- [Discussions](https://github.com/akige/sisoul/discussions) — questions, ideas
- [Contributing Guide](https://github.com/akige/sisoul/blob/main/CONTRIBUTING.md) — dev setup
- [Security](https://github.com/akige/sisoul/blob/main/SECURITY.md) — disclosure policy

## License

[Apache-2.0](https://github.com/akige/sisoul/blob/main/LICENSE)

---

🤖 Most of sisoul was developed with [Claude Code](https://claude.com/claude-code).
