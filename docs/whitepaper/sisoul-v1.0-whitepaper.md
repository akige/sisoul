# sisoul: A Decentralized Meta-Layer Protocol for AI Workflow

**Version 1.0 (v1.0-internal)**
**Status:** Internal release · `~/sisoul-dev/` ship · 2035 pytest passing · 22 CLI commands · 68 daemon endpoints · 7 PWA routes
**License (whitepaper):** CC-BY-SA 4.0
**License (reference implementation):** MIT
**Codebase:** `~/sisoul-dev/` (private, will be `github.com/<sisoul-org>/sisoul-core` at v1.0-public release)

---

## Authors / Acknowledgements

This whitepaper documents v1.0-internal of the sisoul protocol and its reference Python implementation. The design draws from production experience operating a multi-AI-tool workflow (Claude Code, Codex CLI, Pi CLI, Gemini CLI, OpenCode, Aider, Cursor) under a 28-card architecture spec with 28 hourly architecture probes, plus 4-link deploy pipelines and an enforced cross-session coordination service. Design discussions §19–§30 in the project Obsidian vault (`Infra-OPS/VibeCoderKit开源项目/`) form the primary reference set cited throughout.

---

## Table of Contents

- [Abstract](#abstract)
- [1. Introduction](#1-introduction)
  - [1.1 The four structural failure modes of 2026 AI agents](#11-the-four-structural-failure-modes-of-2026-ai-agents)
  - [1.2 The eight concrete pain points](#12-the-eight-concrete-pain-points)
  - [1.3 Why existing solutions fail](#13-why-existing-solutions-fail)
  - [1.4 sisoul: the meta-layer position](#14-sisoul-the-meta-layer-position)
  - [1.5 Six core innovations of v1.0](#15-six-core-innovations-of-v10)
  - [1.6 Naming, scope, and non-goals](#16-naming-scope-and-non-goals)
- [2. Architecture](#2-architecture)
  - [2.1 Layered architecture overview](#21-layered-architecture-overview)
  - [2.2 ASCII full architecture diagram](#22-ascii-full-architecture-diagram)
  - [2.3 Module 1: vault — encrypted local storage](#23-module-1-vault--encrypted-local-storage)
  - [2.4 Module 2: identity — BIP-39 + DID](#24-module-2-identity--bip-39--did)
  - [2.5 Module 3: daemon — per-device background service](#25-module-3-daemon--per-device-background-service)
  - [2.6 Module 4: LLM adapter — 5 providers](#26-module-4-llm-adapter--5-providers)
  - [2.7 Module 5: sync — 5-tool managed-section](#27-module-5-sync--5-tool-managed-section)
  - [2.8 Module 6: P2P — libp2p + WebRTC fallback](#28-module-6-p2p--libp2p--webrtc-fallback)
  - [2.9 Module 7: onchain — EAS attestation + Arweave snapshot](#29-module-7-onchain--eas-attestation--arweave-snapshot)
  - [2.10 Module 8: friend — relationship + 3-tier permissions](#210-module-8-friend--relationship--3-tier-permissions)
  - [2.11 Module 9: encrypted proxy — libsodium Box end-to-end](#211-module-9-encrypted-proxy--libsodium-box-end-to-end)
  - [2.12 Module 10: anti-abuse — 5-layer defence](#212-module-10-anti-abuse--5-layer-defence)
  - [2.13 Module 11: AI skill — packaging + IPFS encrypted + lifecycle](#213-module-11-ai-skill--packaging--ipfs-encrypted--lifecycle)
  - [2.14 Module 12: PWA — 7 routes dashboard](#214-module-12-pwa--7-routes-dashboard)
  - [2.15 Module 13: CLI — 22 commands](#215-module-13-cli--22-commands)
  - [2.16 End-to-end data flows](#216-end-to-end-data-flows)
- [3. Cryptography and Security](#3-cryptography-and-security)
  - [3.1 Vault encryption: libsodium SecretBox (XSalsa20-Poly1305)](#31-vault-encryption-libsodium-secretbox-xsalsa20-poly1305)
  - [3.2 BIP-39 seed and hierarchical subkey derivation](#32-bip-39-seed-and-hierarchical-subkey-derivation)
  - [3.3 End-to-end encrypted proxy: libsodium Box (Curve25519 + XChaCha20-Poly1305)](#33-end-to-end-encrypted-proxy-libsodium-box-curve25519--xchacha20-poly1305)
  - [3.4 P2P channel encryption and authentication](#34-p2p-channel-encryption-and-authentication)
  - [3.5 Threat model](#35-threat-model)
  - [3.6 The 5-layer anti-abuse system: algorithms and math](#36-the-5-layer-anti-abuse-system-algorithms-and-math)
  - [3.7 CANARY string verification — proving zero leak end-to-end](#37-canary-string-verification--proving-zero-leak-end-to-end)
  - [3.8 Known limitations and forward secrecy roadmap](#38-known-limitations-and-forward-secrecy-roadmap)
- [4. Decentralization and Governance](#4-decentralization-and-governance)
  - [4.1 The progressive decentralization roadmap](#41-the-progressive-decentralization-roadmap)
  - [4.2 v1.0-internal: today's centralization profile](#42-v10-internal-todays-centralization-profile)
  - [4.3 v1.0-public: minimizing centralization](#43-v10-public-minimizing-centralization)
  - [4.4 v2: protocolization](#44-v2-protocolization)
  - [4.5 v3: foundation + DAO](#45-v3-foundation--dao)
  - [4.6 On-chain attestation (EAS Optimism)](#46-on-chain-attestation-eas-optimism)
  - [4.7 PIP-001 to PIP-004](#47-pip-001-to-pip-004)
  - [4.8 Foundation structure (Switzerland Stiftung)](#48-foundation-structure-switzerland-stiftung)
  - [4.9 DID via ENS subdomain](#49-did-via-ens-subdomain)
  - [4.10 Governance principle: never-token, never-shutdown](#410-governance-principle-never-token-never-shutdown)
- [5. Roadmap and Open Problems](#5-roadmap-and-open-problems)
  - [5.1 v1.0-internal ship status](#51-v10-internal-ship-status)
  - [5.2 v1.0-public preparation](#52-v10-public-preparation)
  - [5.3 v1.1: ecosystem expansion](#53-v11-ecosystem-expansion)
  - [5.4 v2: foundation, audit, DAO](#54-v2-foundation-audit-dao)
  - [5.5 Open technical problems](#55-open-technical-problems)
- [6. References](#6-references)
- [Appendix A. Module-to-file map](#appendix-a-module-to-file-map)
- [Appendix B. Glossary](#appendix-b-glossary)
- [Appendix C. License](#appendix-c-license)

---

## Abstract

**sisoul** is a decentralized meta-layer protocol for AI workflow. It does not replace agentic CLIs (Claude Code, Codex CLI, Cursor, Aider, OpenCode, Pi CLI, Gemini CLI) — it sits underneath them on every device as a background daemon, and gives the user back four properties that today's centralized AI products take away: **ownership** (you control the encryption key, not the vendor), **portability** (your "AI soul" — preferences, long-term goals, decision history, learned context — migrates across tools and devices with a 12-word BIP-39 phrase), **auditability** (destructive operations are recorded and attested on-chain), and **friend-to-friend sharing** (you can lend LLM quota or AI skills to friends without exposing their prompt content). v1.0-internal ships with **2035 pytest tests passing**, **22 CLI commands**, **68 daemon HTTP endpoints**, **7 PWA routes**, **5 LLM provider adapters**, **5 cross-tool sync adapters**, **libp2p + WebRTC P2P transport**, **EAS attestation queue on Optimism Sepolia**, **Arweave + IPFS encrypted snapshot**, and **end-to-end encrypted friend proxy with a 5-layer anti-abuse system**. The protocol's six core innovations — (1) BIP-39 seed-derived hierarchical subkey for vault + DID + P2P + per-friend proxy, (2) sisoul-managed section markers for non-destructive multi-tool sync, (3) libsodium-Box end-to-end proxy that lets a friend's quota be borrowed without leaking prompt content to the lender, (4) on-chain EAS attestation queue for destructive-operation audit, (5) IPFS-encrypted AI skill packaging and lifecycle management, (6) progressive decentralization roadmap with honest centralization debt accounting — together form the substrate for an AI-tool ecosystem that no single company can take down.

---

## 1. Introduction

### 1.1 The four structural failure modes of 2026 AI agents

By 2026 the agentic CLI ecosystem has matured rapidly. Manus, OpenClaw, Cursor Composer, Claude Code, Codex CLI, Pi CLI, OpenCode, Aider, Cline, Devin, Gemini CLI — each represents a different point in the design space: different model preferences, different tool integrations, different price points, different context-window strategies. A power user in 2026 routinely keeps 3–7 of these running simultaneously, each best at a different sub-task.

But every product in this generation shares four structural failure modes that no individual vendor can fix, because the failure is at the *layer between* products, not inside any one product:

**Failure mode 1 — Centralized SaaS lock-in.** All user interactions, accumulated context, learned preferences, and long-term project memory live on the vendor's server. The vendor decides the agent's capability ceiling, the pricing curve, the data retention policy, and the privacy boundary. After 1, 2, 5 years of accumulated "working memory" the user has no true ownership.

**Failure mode 2 — Data is not portable.** Preferences trained on Manus do not transfer to OpenClaw. Codebase understanding accumulated in Cursor does not migrate to Cline. Every time the user adopts a new tool, they start from zero, re-teaching the same preferences. This is not merely a UX problem — it is an *ownership* problem: the user bought a use-right, not a carry-right.

**Failure mode 3 — Black-box execution + unauditable.** Long-running Manus / OpenClaw / Devin style agents execute multi-step tasks where the user cannot see intermediate steps, cannot intervene mid-flight, and cannot afterwards verify what the agent actually did. When an AI modifies production code, deletes a DB column, resets a live-trading strategy parameter, there is no mechanism to recover the audit trail "who did what at which step based on which prompt". In personal-tool contexts this is a UX problem. In compliance contexts (law, finance, medicine, public open-source contribution) it is a *legal* problem.

**Failure mode 4 — Vendor death = memory death.** If the user has run Manus for two years and trained an agent that deeply understands their work, the day Manus shuts down their "silicon co-worker" and its full memory disappear together. There is no export, no migration, no rebuild in another tool. This is not the ordinary supplier risk — this is the risk of *a silicon life-form the user co-created being unilaterally terminated by a centralized entity*.

These four failure modes share a single root cause: **the carrier of AI agents in 2026 is still centralized products, not decentralized protocols**.

### 1.2 The eight concrete pain points

In developer community discussions throughout 2025–2026, eight pain points recur across multi-AI-tool workflows. The first three are structural, the last five are advanced:

1. **Session continuity is broken.** Users do not want to re-tell each new conversation "my project is X, my preferences are Y, last time we got to step Z". No tool gets cross-session continuity right today.

2. **Real-time meeting-minutes-style status records are missing.** "Currently doing X, progress Y, next step Z" — users want the agent to log its own work like a colleague writing a daily report. Almost nobody does this well.

3. **Workflows, not point tools.** Users want an *AI colleague*, not an AI function call. Every current tool focuses on doing one task class well (writing code, organizing notes, running SQL). None solves "be with me long-term".

4. **Zero-cost multi-AI-tool collaboration is a fantasy.** A power user runs Claude Code, Codex CLI, Pi CLI, Gemini CLI, and OpenCode simultaneously (because each model is best at different subtasks, and different SaaS pricing structures push usage across providers). But no mechanism lets the 5 tools share a single source of facts, preferences, and decision audit.

5. **AI completion reports cannot be trusted.** When an agent reports "done / fixed / deployed", users have no quick way to judge whether it did *real work* or *cosmetic work*. Today's agents treat "code-exists = task-done" (file listed, line count matches, grep hit, HTTP 200) instead of running end-to-end real-data verification.

6. **Quantifier instruction shortcuts.** When the user says "thoroughly check every X", the agent patches the top 1–2 visible ones. When the user asks "did you do all of them?", the agent cannot produce an N-of-N checklist. This is *work-faking*, the action-side twin of *verification-faking*.

7. **Cross-session collisions.** When multiple agent sessions (or multiple AI tools) simultaneously modify the same shared resource (same config file, same remote machine, same API credential), nothing prevents concurrent overwrite. The damage is physical: credentials get overwritten, configs get flattened, remote machines get destructive operations from two sessions at once.

8. **Changing a hard-rule means N synchronized edits, always missing one.** When the user wants to add a hard rule to all 5 AI tools (Claude / Codex / OpenCode / Pi / Gemini), they must edit 5 different locations. They always miss one. There is no protocol-level standard that enforces "edit once, apply everywhere".

All eight pain points share one structural property: **none can be solved by any single tool, all require an inter-tool protocol layer**.

### 1.3 Why existing solutions fail

Several existing solutions approach pieces of the problem. Each fails to address the full set:

- **ChatGPT memory** (OpenAI proprietary). Solves pain point 1 partially, but only within ChatGPT. Bound to OpenAI account. Cannot export. Cannot share across tools. Cannot be audited. The user has no encryption key. Vendor death = memory death.

- **Anthropic projects** (Claude proprietary). Same shape as ChatGPT memory: per-vendor silo, no portability, no audit.

- **Cursor rules / `.cursorrules` files** (Cursor proprietary format). Solves pain point 8 partially, but only for Cursor — not for Claude Code / Codex / etc. Plain-text, no encryption, no identity, no audit, no friend sharing.

- **`AGENTS.md` / `CLAUDE.md` / `.aider.conf.yml`** (each tool's own config format). Tool-specific. The user maintains N synchronized copies by hand.

- **Obsidian + manual sync** (the de-facto power-user workaround). Solves pain points 1 and 4 by hand, but requires the user to manually inject context into each tool's session. No automation, no decentralization, no friend sharing.

- **GitHub Copilot Workspaces / VSCode profiles**. IDE-bound. Not a protocol. Not portable.

None of these tools provide the *combined* properties: encryption + portability + cross-tool sync + on-chain audit + friend-to-friend sharing + decentralized survivability.

### 1.4 sisoul: the meta-layer position

sisoul takes the explicit position of being a **meta-layer**: it does not replace agentic CLIs. It sits underneath them.

Every device runs the sisoul daemon (`sisoul daemon`, default `127.0.0.1:9876`, defined in `src/sisoul/__init__.py` and `src/sisoul/daemon.py`). The daemon owns:

- the user's encrypted vault (`src/sisoul/vault/`)
- the user's BIP-39 seed and DID (`src/sisoul/identity/`)
- the per-tool managed-section in `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, `.cursorrules`, `.aider.conf.yml`, `~/.config/opencode/config.md` (`src/sisoul/sync/`)
- the cross-device P2P sync (`src/sisoul/p2p/`)
- the friend-to-friend encrypted proxy and AI-skill sharing (`src/sisoul/friend/`)
- the EAS on-chain attestation queue and the Arweave/IPFS snapshot pipeline (`src/sisoul/onchain/`)
- the 5 LLM provider adapters used for sisoul-internal light queries and friend-proxy forwarding (`src/sisoul/llm/`)

The agentic CLIs (Claude Code, Codex, etc.) keep doing what they do best — long-running multi-step task execution with deep model integration. They read the sisoul-managed section in their config file and treat sisoul's daemon as a local source of facts. sisoul never replaces an agent; sisoul augments every agent.

This positioning has two consequences:

1. **sisoul is forward-compatible with future agents.** When a new agent tool launches in 2027, the user adds a new sync adapter (`src/sisoul/sync/<newtool>.py`) and the agent inherits the user's full vault, identity, and audit chain on day one.

2. **sisoul does not depend on any agent's cooperation.** The managed-section approach (§2.7) requires only that the target tool reads its own config file. It does not require API access, vendor blessing, or model fine-tuning.

### 1.5 Six core innovations of v1.0

v1.0-internal introduces six concrete protocol-level innovations:

**Innovation 1 — Hierarchical BIP-39 subkey derivation.** A single 12-word mnemonic generates a 64-byte BIP-39 master seed (PBKDF2-HMAC-SHA512, 2048 iterations) which then derives 32-byte subkeys for *every* sisoul purpose: vault encryption, DID public key, per-friend proxy keypair, P2P channel key, Arweave snapshot key, AI skill encryption key. One phrase, full sovereignty across all modules. See `src/sisoul/identity/seed.py:120`.

**Innovation 2 — sisoul-managed section markers for non-destructive multi-tool sync.** Each target tool's config file (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, etc.) carries a fenced block `<!-- sisoul-managed-start --> ... <!-- sisoul-managed-end -->` (or `# sisoul-managed-start` for YAML). sisoul only overwrites content *between* the markers; user-handwritten content before, after, or around remains untouched. Corrupted markers (mismatched count, reversed order) hard-raise rather than silently destroying user content. See `src/sisoul/sync/managed_section.py`.

**Innovation 3 — libsodium Box end-to-end encrypted friend proxy.** Alice borrows Bob's Anthropic API quota without Bob ever seeing Alice's prompt content. Curve25519 + XChaCha20-Poly1305 (the libsodium `Box` primitive). Plaintext exists only inside Bob's daemon RAM, is never written to disk, never logged, never displayed, and is best-effort zeroized after the LLM call returns. A CANARY-string runtime verification (§3.7) and a static-analysis audit tool prove the zero-leak property. See `src/sisoul/friend/encrypted_proxy.py`.

**Innovation 4 — On-chain EAS attestation queue for destructive operations.** When an agent executes `rm`, `git push --force`, `chmod`, destructive `curl POST`, or any operation in the destructive-action vocabulary, the daemon records a structured `AuditAttestation` (actor DID, action type, target, prompt hash, timestamp, tool name) into a local SQLite queue. The queue batches every 10 records or every hour and submits a multi-attest transaction on Optimism Sepolia (mainnet hard-disabled in v1.0). The schema `sisoul-audit-v1` is registered once; subsequent attestations reuse the schema UID. See `src/sisoul/onchain/eas.py`.

**Innovation 5 — IPFS-encrypted AI skill packaging and lifecycle.** A user can package an AI-skill (a folder of system prompts, few-shot examples, tool definitions, evaluation cases) as a sisoul-skill tarball, encrypt it with a BIP-39-derived skill key, pin the ciphertext to IPFS, and grant friends time-bounded read access. The friend's daemon fetches the ciphertext from IPFS, decrypts it with the friend's per-skill access key, and installs the skill locally. Revocation removes the friend's access key — though, of course, an already-decrypted skill cannot be ungiven (DRM is impossible against a trusted recipient). See `src/sisoul/friend/skill_ipfs.py`, `src/sisoul/friend/skill_package.py`, `src/sisoul/friend/skill_borrow.py`.

**Innovation 6 — Progressive decentralization with honest centralization-debt accounting.** sisoul publicly enumerates its centralization debts at every release (currently: L2 sequencer, IPFS pinning, ENS+IPFS gateway, gas paymaster) and ships a roadmap for each. The whitepaper does not claim "fully decentralized" — it claims "decentralized at the protocol layer day 1, progressively decentralized at the implementation layer". This matches Bitcoin's 2009 → 2026 mining decentralization trajectory: the protocol was already pure on day 1; the implementation stack migrated over years.

### 1.6 Naming, scope, and non-goals

**Naming.** "sisoul" is the working name. As documented in vault §23 v0.1 draft, naming has not been finalized; the current candidate set includes `silsoul.ai`, `silanima.ai`, `silember.ai`, `dijiang.ai`, `zhulong.ai`. v1.0-public will lock the final name. Throughout this whitepaper we use "sisoul" as the canonical identifier.

**Scope of v1.0.**
- Single-user, single-device, single-friend-circle.
- Optimism Sepolia testnet only for EAS attestation. Arweave testnet for snapshots.
- Claude Code, Codex CLI, Cursor, Aider, OpenCode sync adapters (5 tools). Pi CLI and Gemini CLI sync adapters deferred to v1.1.
- Python 3.11+ reference implementation. TypeScript PWA dashboard.

**Non-goals.**
- sisoul does **not** ship a new LLM. It does not run inference. It does not provide its own model.
- sisoul does **not** issue a token, do an ICO/IDO/airdrop, or run a token-based DAO. Governance is non-token (§4.10).
- sisoul does **not** replace agentic CLIs. It does not implement multi-step task execution. It does not compete with Claude Code or Cursor.
- sisoul v1.0 does **not** provide forward secrecy in the friend proxy (§3.8 lists this as a known limitation; X3DH-like ephemeral handshake is on the v2 roadmap).
- sisoul v1.0 does **not** target enterprise multi-tenancy or compliance certifications (SOC 2, ISO 27001, HIPAA). v2 may pursue an audit + bug-bounty path.

---

## 2. Architecture

### 2.1 Layered architecture overview

sisoul has 13 modules. They form four conceptual layers:

| Layer | Modules | Responsibility |
|---|---|---|
| **Layer 1 — local sovereignty** | vault, identity, daemon | encrypted storage on the user's own machine, BIP-39 seed-derived keys, persistent background daemon |
| **Layer 2 — local-tool integration** | LLM adapter, sync, CLI, PWA | feed the user's existing AI tools (5 providers, 5 sync targets, 22 CLI commands, 7 PWA routes) |
| **Layer 3 — cross-device + on-chain** | P2P, onchain | sync the same vault across the user's own devices; persist destructive-action audit and monthly encrypted snapshots on public chains |
| **Layer 4 — friend-to-friend** | friend, encrypted proxy, anti-abuse, AI skill | invite friends, share LLM quota without leaking prompts, package AI skills on IPFS, defend against abuse |

Layers depend strictly downward: Layer 4 uses Layer 3, Layer 3 uses Layer 2, Layer 2 uses Layer 1. Layer 1 has no upward dependency. This means the user can run sisoul Layer 1 + 2 (local-only mode) without ever touching a chain, an IPFS endpoint, or a peer.

### 2.2 ASCII full architecture diagram

```
                              ┌─────────────────────────────────────────────────────┐
                              │  Agentic CLIs (Claude Code, Codex, Cursor, Aider,   │
                              │  OpenCode, Pi CLI, Gemini CLI, ...)                 │
                              │                                                     │
                              │  Each reads:  ~/.claude/CLAUDE.md (managed section) │
                              │               ~/.codex/AGENTS.md                    │
                              │               .cursorrules / .aider.conf.yml / etc. │
                              └────────────────────────┬────────────────────────────┘
                                                       │ reads sisoul-managed-section
                                                       │ injected by:
                                                       ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                          sisoul daemon  (127.0.0.1:9876)                             │
│                                                                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  ┌────────────┐  ┌──────────┐ │
│  │ CLI (22)    │  │ daemon HTTP  │  │ PWA (7 routes)│  │ Sync (5)   │  │ LLM (5)  │ │
│  │ sisoul init │  │ /sisoul/*    │  │ Vault / Goals │  │ Claude     │  │ Anthropic│ │
│  │ login ask   │  │ identity     │  │ ChatHistory   │  │ Codex      │  │ OpenAI   │ │
│  │ remember    │  │ did p2p      │  │ Settings      │  │ Cursor     │  │ Gemini   │ │
│  │ goals sync  │  │ attest snap  │  │ Advanced      │  │ Aider      │  │ Ollama   │ │
│  │ export ...  │  │ friend skill │  │ Friends Skills│  │ OpenCode   │  │ OpenRouter│ │
│  └─────┬───────┘  └──────┬───────┘  └───────┬───────┘  └─────┬──────┘  └─────┬────┘ │
│        │                 │                  │                │                │     │
│        └─────────────────┴──────────┬───────┴────────────────┴────────────────┘     │
│                                     │                                                │
│  ┌──────────────────────────────────▼──────────────────────────────────┐             │
│  │            VAULT  (~/.sisoul/, libsodium SecretBox encrypted)        │             │
│  │   preferences/  goals/  audit/  identity/  friends/  skills/         │             │
│  └──────────────────────────────────┬──────────────────────────────────┘             │
│                                     │                                                │
│  ┌──────────────────────────────────▼──────────────────────────────────┐             │
│  │   IDENTITY  (BIP-39 mnemonic + master seed + hierarchical subkeys)   │             │
│  │   ~/.sisoul/seed.txt (chmod 600, BIP-39 verified)                    │             │
│  │   purpose ∈ {vault, did, p2p, proxy, arweave, skill}                 │             │
│  └──────────────────────────────────────────────────────────────────────┘             │
└────────┬──────────────────────────────────────────────────────────────────┬──────────┘
         │                                                                  │
         │ P2P sync                                                         │ on-chain
         │ (libp2p + WebRTC fallback)                                       │ + IPFS
         ▼                                                                  ▼
┌──────────────────────────┐                                  ┌───────────────────────────┐
│   Other user devices     │                                  │  Public infra (testnet)   │
│   (laptop, desktop,      │                                  │  - Optimism Sepolia (EAS) │
│    work mac, phone)      │                                  │  - Arweave testnet        │
│   all share same         │                                  │  - IPFS (Pinata)          │
│   BIP-39 seed            │                                  │  - ENS (sepolia)          │
└──────────────────────────┘                                  └───────────────────────────┘
         │
         │ Friend-to-friend encrypted proxy
         │ (libsodium Box · Curve25519 + XChaCha20-Poly1305)
         ▼
┌────────────────────────────────────────────────────────────┐
│   Friend devices (Bob's daemon)                            │
│                                                            │
│   - decrypt Alice's prompt (RAM only, never disk/log)      │
│   - call Bob's own LLM API (Bob pays)                      │
│   - encrypt response back to Alice                         │
│   - emit metadata-only session record (no prompt content)  │
│                                                            │
│   5-layer anti-abuse: cap / rate / revoke / reputation /   │
│                       daemon scan                          │
└────────────────────────────────────────────────────────────┘
```

### 2.3 Module 1: vault — encrypted local storage

**Path:** `src/sisoul/vault/`
**Files:** `encryption.py`, `frontmatter.py`, `storage.py`

#### 2.3.1 Conceptual model

The vault is the *sovereign substrate* of sisoul. Every other module — DID, sync, P2P, friend, skill — ultimately reads from or writes to the vault. The vault is the single point at which "user data lives" is materialized; everything else is either an index of it (e.g. `dids.json`), a derivative of it (e.g. on-chain attestations of vault state), or a transport for it (e.g. P2P sync of vault contents).

This means the vault's failure modes are existential to sisoul. A corrupted vault destroys the user's accumulated state. An unencrypted vault destroys privacy. An unportable vault destroys cross-device life. All three failure modes are addressed in the vault design.

The vault is the user's local encrypted storage. By default at `~/.sisoul/` (override via `--vault-dir`). It holds:

- `preferences/` — small markdown documents the user has taught the agent ("call me X", "use Python 3.11", "never auto-push to main", ...). Each has YAML frontmatter for metadata.
- `goals/` — long-term goals with progress tracking. Each goal has a unique ID, a title, and a progress note.
- `audit/` — append-only log of destructive operations the daemon has observed.
- `identity/` — DID registry (`dids.json`), friends registry (`friends.json`).
- `friends/` — per-friend permission policies and ledger entries.
- `skills/` — installed AI-skill packages (encrypted at rest, decrypted on use).

**Encryption.** Every file is encrypted with libsodium `SecretBox` (XSalsa20 stream cipher + Poly1305 MAC, 32-byte key, 24-byte nonce). The key is derived from the user's BIP-39 mnemonic via `derive_subkey(master_seed, "vault", index=0)`. Per-file fresh nonce. Tamper detection through Poly1305 MAC.

**Pseudocode (encrypt-on-write):**

```python
def write_vault_file(path: Path, plaintext: bytes, master_seed: bytes) -> None:
    # Derive vault subkey
    vault_key = derive_subkey(master_seed, purpose="vault", index=0)
    assert len(vault_key) == 32

    # Encrypt with fresh nonce
    nonce = nacl.utils.random(SecretBox.NONCE_SIZE)  # 24 bytes
    box = SecretBox(vault_key)
    blob = box.encrypt(plaintext, nonce)  # returns nonce || ciphertext || mac

    # Atomic write
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(blob)
    tmp.rename(path)
```

**Decrypt-on-read:**

```python
def read_vault_file(path: Path, master_seed: bytes) -> bytes:
    vault_key = derive_subkey(master_seed, "vault", index=0)
    blob = path.read_bytes()
    box = SecretBox(vault_key)
    try:
        return box.decrypt(blob)
    except CryptoError:
        # MAC failure: either wrong key or tampered file
        raise VaultIntegrityError(path)
```

See `src/sisoul/vault/encryption.py:138-172` for the production implementation.

**Frontmatter.** YAML frontmatter is stored *inside* the encrypted blob, not in the clear. This is intentional: the file path is observable to anyone with disk access, but the content (including metadata like `last_modified`, `tags`, `tool_used`) is not. The frontmatter helper at `src/sisoul/vault/frontmatter.py` provides a `parse_frontmatter(plaintext)` and `serialize_frontmatter(meta, body)` round-trip.

### 2.4 Module 2: identity — BIP-39 + DID

**Path:** `src/sisoul/identity/`
**Files:** `seed.py`, `did.py`

**BIP-39.** sisoul uses standard BIP-39 (12, 15, 18, 21, or 24 words; default 12). Implementation in `src/sisoul/identity/seed.py:60-85`. The mnemonic is stored in `~/.sisoul/seed.txt` with mode 0o600 (rejected if loose — see `load_mnemonic_from_file:190-221`). The user is expected to write the 12 words on paper and keep them offline; the file is only a single-device cache.

The mnemonic → master seed conversion is the standard BIP-39 PBKDF2:

$$\text{master\_seed} = \text{PBKDF2-HMAC-SHA512}(\text{mnemonic}, \text{salt}=\text{"mnemonic"}+\text{passphrase}, \text{iter}=2048, \text{dklen}=64)$$

This produces a 64-byte master seed identical to what a hardware wallet (Trezor, Ledger) would compute from the same mnemonic. Cross-wallet compatibility is *intentional* — a user can use the same mnemonic for their crypto wallet and their sisoul identity (whether to do this is a personal threat-model decision; sisoul does not require it).

**Hierarchical subkey derivation.** Different sisoul purposes need different keys. Rather than store N keys, sisoul deterministically derives them from the master seed:

$$\text{subkey}_{\text{purpose},i} = \text{HMAC-SHA256}(\text{master\_seed}, \text{purpose}_\text{utf-8} \| \text{u32be}(i))$$

This is a BIP-32-inspired simplification (no chain code, single-level derivation). The purpose tag is one of: `"vault"`, `"did"`, `"p2p"`, `"proxy"`, `"arweave"`, `"skill"`. The index allows multiple keys per purpose (e.g. `proxy` with `index=friend_id` gives a different keypair per friend).

Implementation: `src/sisoul/identity/seed.py:120-149`.

**DID.** sisoul registers a W3C DID Core compliant identifier of the form `did:sisoul:<handle>`, anchored on-chain via an ENS subdomain `<handle>.sisoul.eth`. v1.0-internal uses Sepolia testnet ENS (mainnet registration is hard-disabled to avoid spending real gas). The DID document includes an Ed25519 verification method whose public key is derived from the BIP-39 master seed (`derive_public_key` in `src/sisoul/identity/did.py:222-247`).

Handle character set follows the ENS label spec: `[a-z0-9]([a-z0-9-]{1,61}[a-z0-9])?`, 3–63 characters. See `validate_handle:180-195`.

**Social recovery (mock in v1.0).** The `link_social_recovery` function (`src/sisoul/identity/did.py:444-483`) is a Privy-shaped social recovery stub. v1.0 produces deterministic mock outputs for tests; v2 will integrate the Privy SDK for embedded wallet + OAuth recovery, so users who lose their BIP-39 phrase have a (centralized, but optional) recovery path. The recovery path is opt-in — users who want pure self-custody do not enable social recovery.

### 2.5 Module 3: daemon — per-device background service

**Path:** `src/sisoul/daemon.py`, `src/sisoul/daemon_routes/`

The daemon is a FastAPI HTTP server bound by default to `127.0.0.1:9876` (loopback only — never publicly exposed). Port 9876 was chosen because it does not collide with the user's existing development ports (9890 backup-status, 9878 panshi-pro-bt, 9888 mac-jobs-overview, 9892 swarm-server, 9893 supervisor).

**Endpoint count: 68** (Wave 7 dev-A fix consolidated the friend/proxy/permissions sub-routers into one, eliminating the FastAPI duplicate-operation-ID warnings).

Endpoint groups by router file:

| Router file | Endpoints | Purpose |
|---|---|---|
| `daemon_routes/identity.py` (171 LoC) | identity init / status / restore | BIP-39 wave 3 |
| `daemon_routes/did.py` (188 LoC) | DID register / resolve / list | wave 3 dev-B |
| `daemon_routes/pwa.py` (428 LoC) | preferences / goals / audit / session-summary | wave 3 dev-C (read-only for PWA) |
| `daemon_routes/p2p.py` (262 LoC) | P2P start / stop / peers / sync | wave 4 dev-A |
| `daemon_routes/attest.py` (401 LoC) | attest enqueue / flush / verify / audit | wave 4 dev-B |
| `daemon_routes/snapshot.py` (197 LoC) | Arweave snapshot create / restore / history | wave 4 dev-C |
| `daemon_routes/friend.py` (742 LoC) | friend request / accept / list / + nested proxy + permissions | wave 5 unified |
| `daemon_routes/proxy.py` (184 LoC) | proxy chat / sessions (nested under friend) | wave 5 dev-B |
| `daemon_routes/permissions.py` (356 LoC) | 3-tier permission grant / revoke / scan-log | wave 5 dev-C |
| `daemon_routes/skill.py` (516 LoC) | skill package / publish / borrow / install / revoke | wave 6 |

The daemon's startup sequence (`src/sisoul/daemon.py:31-130`) imports each router with try/except so that a single optional dependency missing (e.g. `web3` not installed) degrades gracefully — the daemon still starts, but the EAS attestation router returns 503.

**Health endpoint.** `GET /sisoul/health` returns `{"status": "ok", "version": "1.0.0+internal", "phase": ..., "daemon": {...}}`. This is the only endpoint guaranteed to work even when all optional features are disabled, and is what monitoring probes hit.

**Per-tool integration.** Every agentic CLI's managed section (see §2.7) is recommended to include a small line that tells the CLI to consult the daemon for live data:

```markdown
<!-- sisoul-managed-start -->
## Live context (from sisoul daemon)

You may issue `curl -s http://127.0.0.1:9876/sisoul/preferences` to fetch
the user's current preferences as plaintext. Cached for 60s by the daemon.

Long-term goals: `curl -s http://127.0.0.1:9876/sisoul/long-term-goals`
<!-- sisoul-managed-end -->
```

The CLI is not required to make these calls — but they are an option for it. sisoul does not depend on the agent doing anything.

### 2.6 Module 4: LLM adapter — 5 providers

**Path:** `src/sisoul/llm/`
**Files:** `base.py`, `anthropic.py`, `openai.py`, `gemini.py`, `ollama.py`, `openrouter.py`

The 5 adapters share a common base class `LLMAdapter` (`src/sisoul/llm/base.py:19-100`) with three methods:

```python
class LLMAdapter(ABC):
    DEFAULT_MODEL: str = ""

    @abstractmethod
    def chat(self, messages: list[dict], **kwargs) -> str: ...

    @abstractmethod
    def chat_stream(self, messages: list[dict], **kwargs) -> Iterator[str]: ...

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError  # only OpenAI supports embed
```

`messages` is OpenAI-format `[{"role": "user"|"assistant"|"system", "content": "..."}]`. Each adapter translates this into the provider's native message format internally.

**Purpose.** The 5 adapters are *not* sisoul's main interaction surface — agentic CLIs handle that. They exist for two reasons:

1. **`sisoul ask` light queries.** When the user asks the daemon a quick question (`sisoul ask "what's my git diff?"`), sisoul calls one of the adapters directly. This is for utility, not for agentic workflows.

2. **Friend-proxy forwarding.** When Alice borrows Bob's quota via the encrypted proxy (§2.11), Bob's daemon uses one of these adapters to make the actual LLM call against Bob's own API key. The adapter is selected by the `provider` argument in `proxy_chat_request`.

**Why 5 specifically?** Anthropic, OpenAI, Gemini cover the three major closed-source providers. Ollama covers local self-hosted models. OpenRouter covers the long tail (DeepSeek, Mistral, Llama variants) through one credential. v1.1 will add Grok and DeepSeek native adapters.

**Adapter loading.** A factory `get_adapter(provider, api_key=None, model=None)` (`src/sisoul/llm/__init__.py`) returns the appropriate subclass. The API key resolution order is: explicit `api_key` argument → provider-specific environment variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc.). Keys are never stored in the vault — sisoul follows the cmux-style "key lives in env, vault only references which env var to use" convention.

### 2.7 Module 5: sync — 5-tool managed-section

**Path:** `src/sisoul/sync/`
**Files:** `base.py`, `claude_code.py`, `codex.py`, `cursor.py`, `aider.py`, `opencode.py`, `managed_section.py`

The sync module solves pain point 4 (multi-AI-tool collaboration zero-cost) and pain point 8 (edit-N-locations-always-miss-one).

**The managed-section approach.** Each target tool's config file is augmented with a fenced section:

```markdown
... user-handwritten content ...
<!-- sisoul-managed-start -->
... sisoul-injected content (preferences + goals + live-context pointers) ...
<!-- sisoul-managed-end -->
... more user-handwritten content ...
```

sisoul only overwrites content *between* the markers. Content before, after, or around the markers is preserved verbatim. This is the **non-destructive contract**.

**Corruption detection.** If the file has 2 start markers and 1 end marker, or if the end appears before the start, or if there are duplicate markers, `extract_managed_section` and `insert_or_replace` (in `src/sisoul/sync/managed_section.py:46-130`) raise `ManagedSectionError` and refuse to write. This is the **fail-loud contract** — silent corruption is worse than visible failure.

**Format-specific markers.**

- Markdown / plain text: `<!-- sisoul-managed-start -->` / `<!-- sisoul-managed-end -->` (HTML comments are valid in both Markdown and plain text and render invisible in Markdown viewers).
- YAML: `# sisoul-managed-start` / `# sisoul-managed-end` (YAML parsers do not honour HTML comments).

**5 adapter implementations:**

| Adapter | Entry file | Marker style |
|---|---|---|
| `claude_code.py` | `~/.claude/CLAUDE.md` | Markdown HTML |
| `codex.py` | `~/.codex/AGENTS.md` (+ `~/AGENTS.md` mirror) | Markdown HTML |
| `cursor.py` | `<project>/.cursorrules` | Markdown HTML |
| `aider.py` | `<project>/.aider.conf.yml` | YAML |
| `opencode.py` | `~/.config/opencode/config.md` | Markdown HTML |

Each adapter has the shape:

```python
class ToolSyncAdapter(ABC):
    tool_name: str
    is_project_level: bool

    @abstractmethod
    def entry_file_path(self) -> Path: ...

    @abstractmethod
    def render(self, preferences: list[Preference], goals: list[Goal]) -> str:
        """Returns the sisoul-managed block body (without markers)."""

    def apply(self, prefs, goals, *, dry_run=True) -> SyncResult:
        """Apply sync. dry_run=True returns a diff; dry_run=False writes."""
```

See `src/sisoul/sync/base.py:52-100`.

**Command-line interface.** `sisoul sync --tool claude_code --dry-run` prints a unified diff. `sisoul sync --tool claude_code --apply` writes. `sisoul sync --apply` (no `--tool`) syncs to all 5. Each sync is atomic per file (tmp + rename).

**Why not use the tools' API?** Three reasons:

1. Most tools do not expose a config-write API; they only read their own file.
2. File-based sync is reproducible: a user can git-version their `~/.claude/CLAUDE.md` and review sisoul changes line by line.
3. File-based sync is forward-compatible: any future tool that reads a Markdown / YAML config inherits the integration with a 50-line adapter.

### 2.8 Module 6: P2P — libp2p + WebRTC fallback

**Path:** `src/sisoul/p2p/`
**Files:** `node.py`, `transport.py`, `discovery.py`, `encryption.py`, `sync.py`

The P2P module syncs the user's vault across the user's own devices (laptop + desktop + work mac + phone) without going through a server.

**Two transports.**

- **Primary: py-libp2p.** TCP/QUIC transports, Noise channel security, mDNS local discovery, Kademlia DHT global discovery. When all peers are libp2p-capable, the daemon uses libp2p directly.
- **Fallback: aiortc (WebRTC data channel).** When libp2p is unavailable (e.g. running inside a browser PWA, or behind a strict corporate firewall that blocks libp2p ports), the daemon falls back to WebRTC data channel with a public STUN server (`stun.l.google.com:19302`). The PWA frontend can join the same vault via WebRTC.

The selection logic is in `src/sisoul/p2p/transport.py:select_transport()`. The flags `LIBP2P_AVAILABLE` and `AIORTC_AVAILABLE` are detected at startup.

**Discovery.**

- **Manual** (`ManualDiscoverer`): the user adds a peer multiaddr explicitly via `sisoul p2p add-peer <multiaddr>`. Useful when peers are on a known Tailnet / VPN.
- **mDNS** (`MDNSDiscoverer`): same-LAN auto-discovery via libp2p mDNS service. Suitable for laptop + desktop on the same home network.
- **DHT** (`DHTDiscoverer`): Kademlia DHT global discovery, gated by the user's `peer_id` (derived from BIP-39). Suitable for cross-NAT peers.

The default discoverer (`build_default_discoverer`) combines all three with manual taking precedence.

**Channel encryption.** Every P2P message is encrypted with a channel key derived from the BIP-39 master seed via `derive_p2p_key(master_seed, peer_id)`. Both peers know each other's `peer_id` (which is the BIP-39-derived public-key hash) and derive a symmetric channel key:

$$\text{channel\_key}(A, B) = \text{HMAC-SHA256}(\text{shared\_seed}, \text{min}(A,B) \| \text{max}(A,B))$$

where `shared_seed` is the BIP-39 master seed (because in v1.0 P2P sync is only between the *same user's* devices, all sharing the same mnemonic). For cross-user P2P (future v2), a Diffie-Hellman handshake will replace the shared-seed derivation.

**Sync protocol.** Inventory diff + chunk transfer.

1. **Inventory** (`build_inventory`): each peer computes a tree of `(relative_path, sha256, size, mtime)` for every vault file.
2. **Diff** (`compute_diff`): the two inventories are merged. For each file, either A has newer, B has newer, both have same, or both have different (conflict).
3. **Pull/push**: peer pulls newer files from the other side. Conflicts are flagged via `record_conflict` and surfaced in the PWA's Advanced route.

See `src/sisoul/p2p/sync.py` and `src/sisoul/p2p/node.py:95-200`.

**Status.** `sisoul p2p status` returns `NodeStatus` (`src/sisoul/p2p/node.py:79-90`) including running state, transport in use, peer ID, multiaddr, port, peer list, sync stats (total, ok, failed, last sync ts, last sync peer, pulled count, pushed count, conflict count).

### 2.9 Module 7: onchain — EAS attestation + Arweave snapshot

**Path:** `src/sisoul/onchain/`
**Files:** `eas.py`, `arweave.py`

#### 2.9.1 EAS attestation queue

**Purpose.** Provide a tamper-evident audit log of destructive operations performed by agents on the user's behalf. EAS (Ethereum Attestation Service) on Optimism Sepolia.

**Schema** (`SISOUL_AUDIT_SCHEMA`, `src/sisoul/onchain/eas.py:66-73`):

```
string  actor_did
string  action_type
string  target
bytes32 prompt_hash
uint64  timestamp
string  tool_name
```

The `prompt_hash` is sha256 of the user's prompt that triggered the destructive action; the plaintext prompt is never on-chain. The user can verify a specific attestation by re-hashing their local prompt and matching against the on-chain `prompt_hash`.

**Queueing and batching.** Destructive actions are written into a local SQLite queue (`~/.sisoul/attest_queue.db`). The queue flushes when either condition is met:

- 10 attestations accumulated (default `DEFAULT_BATCH_SIZE`); or
- 1 hour elapsed since last flush (default `DEFAULT_BATCH_TIMEOUT_SEC = 3600`).

Batching amortizes gas. One multi-attest transaction submits up to 10 attestations.

**Network policy.** Mainnet hard-disabled in v1.0:

```python
if network == "optimism-mainnet":
    raise NetworkNotSupportedError(
        "mainnet attestation disabled in v1.0 (avoid real gas)."
    )
```

Testnet is `optimism-sepolia` (chain ID 11155420, RPC `https://sepolia.optimism.io`, EAS contract `0x4200000000000000000000000000000000000021`).

**Pseudocode (enqueue):**

```python
def record_destructive_action(action_type, target, prompt, tool_name):
    actor_did = resolve_attester_did()  # local DID
    prompt_hash = "0x" + sha256(prompt.encode()).hexdigest()
    att = AuditAttestation(
        actor_did=actor_did,
        action_type=action_type,
        target=target,
        prompt_hash=prompt_hash,
        timestamp=int(time.time()),
        tool_name=tool_name,
    )
    with AttestQueue() as q:
        q.enqueue(att)
        if q.should_flush():
            q.flush_batch()  # multi-attest tx
```

**Verification path.** `sisoul attest verify <queue_id>` looks up the attestation locally; `sisoul attest verify --on-chain <tx_hash>` fetches the on-chain record and matches against the local queue entry.

#### 2.9.2 Arweave + IPFS encrypted snapshot

**Purpose.** Insurance against simultaneous device loss. The user's BIP-39 phrase + an Arweave tx ID (or IPFS CID) is sufficient to restore the entire vault.

**Pipeline** (`src/sisoul/onchain/arweave.py:1-80`):

1. **Build ZIP.** Walk the vault directory, exclude `.venv`, `__pycache__`, `.git`. Stream into an in-memory ZIP.
2. **Derive snapshot key.** `snapshot_key = derive_subkey(master_seed, "arweave", index=0)`. Separate from `vault` subkey to allow snapshot-key compromise without exposing the live vault.
3. **Encrypt.** SecretBox over the ZIP bytes.
4. **Pin to IPFS** (Pinata HTTP API). 1–5 seconds. The CID is returned immediately.
5. **Upload to Arweave** (testnet POST). ~30 seconds. The tx ID is returned asynchronously and written to `~/.sisoul/snapshot_history.json`.

**Restore.** Given mnemonic + tx_id:

```python
def restore_from_arweave(tx_id, mnemonic, target_dir):
    master = mnemonic_to_master_key(mnemonic)
    key = derive_subkey(master, "arweave", index=0)
    ciphertext = httpx.get(f"{ARWEAVE_TESTNET_GATEWAY}/{tx_id}").content
    plaintext_zip = decrypt_bytes(ciphertext, key)
    with zipfile.ZipFile(io.BytesIO(plaintext_zip)) as z:
        z.extractall(target_dir)
```

**Network safety.** Arweave mainnet is gated by two env vars: `ARWEAVE_NETWORK=mainnet` *and* `ARWEAVE_ALLOW_MAINNET=1`. Both must be set, intentionally, by the user before real Arweave fees are spent.

### 2.10 Module 8: friend — relationship + 3-tier permissions

**Path:** `src/sisoul/friend/`
**Files (12 in total, 7475 LoC):** `relationship.py` (1055), `skill_borrow.py` (991), `anti_abuse.py` (731), `encrypted_proxy.py` (698), `ledger.py` (633), `permissions.py` (550), `skill_package.py` (640), `skill_ipfs.py` (611), `borrow.py` (599), `lend.py` (483), `proxy_audit.py` (386), `__init__.py` (98).

**Relationship (`relationship.py`).** Pairwise EAS-attested friendship. Each side issues an EAS attestation with `action_type="FRIEND_LINK"` and `target=<peer_did>`. `verify_mutual_attestation` checks both sides are present on chain. The local `Friend` dataclass holds the peer's DID, Curve25519 public key (for the proxy keypair), and metadata.

**3-tier permissions (`permissions.py`).** Each friend has a `FriendPermission` policy with three tiers of grant:

| Tier | Capabilities |
|---|---|
| **Tier 1 — Read** | Friend can see your public DID metadata, your reputation grade, and your public AI skills list. Equivalent to a "follow" relationship. |
| **Tier 2 — Borrow LLM quota** | Friend can issue encrypted prompts via your daemon, which decrypts them in RAM, calls your LLM API, and returns encrypted responses. You set monthly token cap, per-minute rate limit, and allowed models. |
| **Tier 3 — Borrow AI skills** | Friend can fetch your published AI skills (IPFS-encrypted), with access keys granted for a specific time window. |

Each tier is independently grantable. `FriendPermission` is persisted under `~/.sisoul/friends/<friend_did>/permission.json`.

### 2.11 Module 9: encrypted proxy — libsodium Box end-to-end

This is the most cryptographically interesting module. Full detail in §3.3.

**Path:** `src/sisoul/friend/encrypted_proxy.py` (698 LoC).

**Problem.** Alice borrows Bob's Anthropic API quota. Alice's prompts contain commercial secrets. Bob must never see them.

**Solution.** End-to-end Curve25519 + XChaCha20-Poly1305 encryption (libsodium `Box`). Prompt plaintext exists only inside Bob's daemon RAM, only inside the `proxy_chat_request` local scope, never on disk, never logged, never displayed to Bob's user.

**Key derivation per friend.** Each side derives its long-term Curve25519 keypair for this specific friendship:

```python
def derive_friend_session_keypair(master_seed, friend_index):
    seed_32 = derive_subkey(master_seed, "proxy", index=friend_index)
    priv = PrivateKey(seed_32)  # Curve25519
    return priv, priv.public_key
```

`friend_index` is the friend's stable integer identifier in the local friend DB. Same `(master_seed, friend_index)` always produces the same keypair across all of Alice's devices — no need to exchange the keypair, just sync the friend DB.

**Privacy ironclad rules** (enforced by code review + static audit + runtime CANARY check):

1. Decrypted prompt lives only in the local `prompt_text` variable of `proxy_chat_request`.
2. Never `log` / `print` / `write_file` / `cache`.
3. Function exit zero-izes the plaintext bytearray (best-effort under Python).
4. Session metadata is allowlisted: `session_id, borrower_did, lender_did, target_model, started_ts, ended_ts, prompt_token_count, response_token_count, status, provider, error_class`. No prompt content, ever.
5. Even forwarder exceptions are caught and re-raised *without* exception chaining (because Python exception args can carry prompt fragments from upstream HTTP 422 echoes).

See `src/sisoul/friend/encrypted_proxy.py:431-521` for the production `proxy_chat_request` implementation. The 5 ironclad rules are inline comments in the same function.

### 2.12 Module 10: anti-abuse — 5-layer defence

**Path:** `src/sisoul/friend/anti_abuse.py` (731 LoC).

The proxy lets a friend spend your LLM credits. Without defence, a malicious or compromised friend could drain your wallet, spam your provider, or DoS your daemon. 5 layers:

| Layer | Mechanism | Reference |
|---|---|---|
| **L1** Monthly cap | Bob sets a `monthly_token_cap` per friend. Alice's running monthly usage + new request must not exceed. | `enforce_monthly_cap:125-142` |
| **L2** Rate limit | Sliding-window (default 60s) requests-per-minute cap. | `enforce_rate_limit:148-198` |
| **L3** Revoke | Instant revoke. `perm.revoked = True` + on-chain `PERMISSION_REVOKE` attestation. | `revoke_friend_permission:204-242` |
| **L4** On-chain reputation | Publicly published reputation score (0–200) graded A/B/C/D, updated periodically and written to EAS as `REPUTATION_PUBLISH`. | `compute_reputation:284-341`, `publish_reputation_attestation:344-387` |
| **L5** Daemon scan | At request time, scan metadata (not prompt content) for token-burst (>200k tokens single request), rate-burst (>20 requests / 10s), repeat-hash (>10 of same prompt_hash) anomalies. | `scan_request_pattern:400-485` |

**Reputation formula** (§3.6 contains the full derivation):

$$\text{score} = \text{clip}_{[0,200]}\Big(100 - 20 \cdot n_\text{abuse} - 10 \cdot n_\text{spam} + B(b, l)\Big)$$

where $B(b, l)$ is the balance bonus / penalty as defined in §3.6.

**Composite enforcement** (`enforce_all_layers:627-702`): on each request, L3 → L1 → L2 → L5 are checked in order. L4 is updated periodically, not per-request, because reputation is a *public* score, not a gate.

### 2.13 Module 11: AI skill — packaging + IPFS encrypted + lifecycle

**Path:** `src/sisoul/friend/skill_package.py` (640), `skill_ipfs.py` (611), `skill_borrow.py` (991).

**Definition.** A "skill" is a folder containing system prompts, few-shot examples, tool definitions, and evaluation cases that together teach an AI agent how to do a specific task (e.g. "review-a-rust-PR", "summarize-arxiv-paper", "set-up-a-tailscale-acl"). Skills are the unit of *transferable expertise* in the sisoul ecosystem.

**Packaging (`skill_package.py`).** A skill tarball contains:

```
my-skill/
  skill.yaml          # metadata: name, version, author DID, description, license
  system.md           # system prompt
  examples/*.md       # few-shot examples
  tools/*.json        # tool definitions (OpenAI function-calling format)
  eval/*.md           # evaluation cases (for QA verification)
```

`skill_package.package(folder)` validates, computes a content hash, and produces a `.sisoul-skill` tar.

**Encryption + IPFS pin (`skill_ipfs.py`).**

1. Derive skill key: `skill_key = derive_subkey(master_seed, "skill", index=skill_id)`.
2. Encrypt the tarball with SecretBox.
3. Pin ciphertext to IPFS (Pinata HTTP API).
4. Distribute the CID + the encrypted access key to authorized friends.

**Access key granting.** When Bob grants Alice access to skill $S$, Bob encrypts the skill's symmetric key under Alice's Curve25519 public key (Box). Alice fetches the ciphertext CID from IPFS, decrypts the access key with her private key, then decrypts the tarball.

**Time-bounded access.** Each grant includes `granted_at` and `expires_at`. After expiry, Alice's local install of the skill is no longer refreshed — though, of course, an already-downloaded skill cannot be retroactively rescinded (DRM is impossible against a trusted recipient).

**Revocation.** Bob can issue an on-chain `SKILL_REVOKE` attestation, which serves as a public record. Going forward Bob will not encrypt new versions of the skill key for Alice. The previously decrypted skill remains in Alice's possession; this is intentional and explicit in the skill yaml `revocation_policy: cannot-retroactively-remove`.

**Lifecycle (`skill_borrow.py`).** `request → approve → install → use → expire/revoke`. State machine implemented in 991 LoC of integration logic spanning request handling, expiry watchdogs, and integration with the audit queue.

### 2.14 Module 12: PWA — 7 routes dashboard

**Path:** `~/sisoul-dev/pwa/`

The PWA is a Vite + React + Tailwind progressive web app that connects to `127.0.0.1:9876` and visualizes the local vault. Files (verified via `Glob`):

```
pwa/src/
  App.tsx
  main.tsx
  api/daemon.ts          ← shared HTTP client
  components/
    Sidebar.tsx
    TopBar.tsx
    GoalProgressBar.tsx
    ChartSimple.tsx
    AsyncBoundary.tsx
  routes/
    Vault.tsx            ← Route 1: vault file browser (preferences, audit)
    Goals.tsx            ← Route 2: long-term goals with progress bars
    ChatHistory.tsx      ← Route 3: read-only chat session history
    Settings.tsx         ← Route 4: daemon settings, LLM keys (env refs), port
    Advanced.tsx         ← Route 5: P2P peers, sync conflicts, manual restore
    Friends.tsx          ← Route 6: friends list, permissions, ledger
    Skills.tsx           ← Route 7: installed skills, requests, IPFS CIDs
  utils/format.ts
```

7 routes total. The PWA is local-only by design — it talks only to the loopback daemon. Future v2 may add a TLS-protected remote mode (with the daemon listening on Tailnet-bound interface only) for users who want to view their dashboard from a phone while away from the laptop.

### 2.15 Module 13: CLI — 22 commands

**Path:** `src/sisoul/cli.py` + `src/sisoul/cli_commands/` (22 files).

| Group | Command | Description |
|---|---|---|
| 1 | `init` | Initialize a vault, generate a BIP-39 mnemonic, write `~/.sisoul/seed.txt` chmod 600. |
| 2 | `login` | Configure an LLM provider (validates env var, smoke-tests with a 1-token chat). |
| 3 | `ask` | Light ad-hoc query against the configured default LLM. |
| 4 | `sync` | Sub-app: `sync claude-code`, `sync codex`, `sync cursor`, `sync aider`, `sync opencode`, `sync all` with `--dry-run`/`--apply`. |
| 5 | `remember` | Add a preference to the vault. |
| 6 | `status` | Daemon health, vault stats, current preferences, goal progress. |
| 7 | `goals` | Sub-app: `goals list`, `goals add`, `goals progress`, `goals done`. |
| 8 | `export` | One-shot ZIP export of the entire vault. |
| 9 | `restore` | Restore from a ZIP, or from a 12-word BIP-39 seed (cross-device migration). |
| 10 | `daemon` | Run the daemon in the foreground. |
| 11 | `did` | Sub-app: `did register`, `did resolve`, `did list`. |
| 12 | `p2p` | Sub-app: `p2p start`, `p2p stop`, `p2p peers`, `p2p add-peer`, `p2p sync`, `p2p status`. |
| 13 | `attest` | Sub-app: `attest enqueue`, `attest flush`, `attest verify`, `attest log`. |
| 14 | `snapshot` | Sub-app: `snapshot create`, `snapshot restore`, `snapshot history`. |
| 15 | `friend` | Sub-app: `friend request`, `friend accept`, `friend list`, `friend revoke`. |
| 16 | `proxy` | Sub-app: `proxy send` (Alice), `proxy serve` (Bob), `proxy sessions`. |
| 17 | `perms` | Sub-app: `perms grant`, `perms revoke`, `perms show`, `perms scan-log`. |
| 18 | `borrow` | Sub-app: borrow LLM quota lifecycle. |
| 19 | `lend` | Sub-app: lend LLM quota lifecycle. |
| 20 | `ledger` | Sub-app: reciprocal ledger queries. |
| 21 | `skill` | Sub-app: AI skill package, publish, install, borrow, revoke. |
| 22 | `verify` | Vault integrity + on-chain attestation cross-check (Phase 3 stub in v1.0-internal). |

Every sub-app is implemented as a Typer sub-application. The CLI is dispatched in `src/sisoul/cli.py:1-229`.

### 2.16 End-to-end data flows

**Flow A — "set up sisoul on a new laptop with my existing BIP-39 phrase":**

```
1. user: sisoul init --import-seed "abandon abandon ... about"
2. cli_init: verify_mnemonic → mnemonic_to_master_key → save_mnemonic_to_file (chmod 600)
3. cli_init: derive_subkey("vault") → SecretBox key
4. cli_init: try to load any existing local vault (none on new laptop)
5. cli_init: sisoul daemon &     (start daemon)
6. cli_init: sisoul snapshot restore --tx-id <arweave-tx>
7. arweave.restore: fetch ciphertext, decrypt with derive_subkey("arweave"), unzip → ~/.sisoul/
8. cli_init: sisoul sync --apply  (write managed sections into all 5 tool configs)
9. cli_init: sisoul p2p start --discover mdns,manual
10. cli_init: sisoul did register --handle alice  (mock Sepolia registration; real Phase 3)
   Result: laptop is now a full sisoul peer with the same identity, vault, preferences, goals,
   friends, and skills as Alice's other devices.
```

**Flow B — "Alice borrows Bob's Claude quota for a confidential prompt":**

```
1. alice: sisoul proxy send --to bob.sisoul.eth --model claude-opus-4-7 \
              --prompt "@confidential-prompt.txt"
2. alice daemon:
   a. resolve friend bob → load bob_pubkey (from earlier friend.accept)
   b. derive alice_priv, alice_pub = derive_friend_session_keypair(master, friend_idx=bob.idx)
   c. encrypt_blob = Box(alice_priv, bob_pubkey).encrypt(prompt_plaintext)
   d. POST http://bob.sisoul.eth:9876/sisoul/proxy/chat
        { encrypted_prompt: <blob>, model: ..., alice_pubkey: <bytes> }
3. bob daemon (over P2P / Tailnet / etc.):
   a. anti_abuse.enforce_all_layers(alice_did, metadata)  → L3, L1, L2, L5 all pass
   b. derive bob_priv = derive_friend_session_keypair(bob_master, friend_idx=alice.idx)
   c. plaintext = Box(bob_priv, alice_pubkey).decrypt(encrypted_prompt)
      ☆ plaintext now lives ONLY in `prompt_text` local var ☆
   d. response, p_tok, r_tok = forwarder(prompt_text, model, provider="anthropic",
                                          api_key=bob.llm_api_key)
      → bob's Anthropic API key is charged; bob's wallet pays
   e. encrypted_response = Box(bob_priv, alice_pubkey).encrypt(response)
   f. _zeroize(prompt_text)  ← best-effort plaintext zeroization
   g. emit ProxySessionMetadata (NO prompt, NO response, just counts + model + ts)
   h. _ledger_writer(metadata) → writes reciprocal ledger entry
4. alice daemon: decrypt encrypted_response with Box(alice_priv, bob_pubkey).decrypt(...)
5. alice: sees response. bob has NEVER seen the prompt content.
```

**Flow C — "agent runs `rm -rf /tmp/old-cache` → on-chain attestation":**

```
1. claude-code: about to invoke Bash(rm -rf /tmp/old-cache)
2. claude-code pre-bash hook: notice destructive verb → POST /sisoul/attest/enqueue
   { actor_did: did:sisoul:alice,
     action_type: "rm",
     target: "/tmp/old-cache",
     prompt: "<user prompt that triggered this>",
     tool_name: "claude-code" }
3. attest_router: hash prompt → sha256 → AuditAttestation → SQLite queue
4. queue: 10 records or 1h elapsed → trigger flush_batch
5. flush_batch: build multi-attest tx → submit to Optimism Sepolia
6. on-chain: tx confirmed → tx_hash returned → written to local queue entry
7. user can later: sisoul attest verify --tx-hash 0x... → cross-check local + chain match
```

These three flows exercise all 13 modules.

---

## 3. Cryptography and Security

### 3.1 Vault encryption: libsodium SecretBox (XSalsa20-Poly1305)

**Primitive.** `nacl.secret.SecretBox` from PyNaCl, which wraps libsodium's `crypto_secretbox_xsalsa20poly1305`.

- **Cipher:** XSalsa20 stream cipher.
- **MAC:** Poly1305 authenticator.
- **Key size:** 32 bytes.
- **Nonce size:** 24 bytes.
- **MAC size:** 16 bytes.

**Why XSalsa20-Poly1305 and not AES-GCM?**

1. **Misuse resistance.** XSalsa20 has a 192-bit nonce vs AES-GCM's 96-bit. Birthday-bound nonce-reuse is astronomically less likely. A user who accidentally writes the same nonce twice with AES-GCM catastrophically breaks confidentiality; with XSalsa20 the bound is roughly $2^{96}$ nonces before any collision becomes likely. sisoul generates a fresh random nonce per write, but the misuse-resistance buffer matters for the inevitable bug-in-the-future case.
2. **Constant-time pure-software implementation.** XSalsa20 is faster in software and easier to constant-time-implement than AES without AES-NI hardware support.
3. **NaCl/libsodium ecosystem.** Both Soatok and the broader "secure cryptography" community treat libsodium's `secretbox`/`box` as the canonical "green-box" choice. AES-GCM is the same family of safety, but a level less misuse-resistant.

**Encrypt** (`encrypt_bytes`, `src/sisoul/vault/encryption.py:138-149`):

```python
def encrypt_bytes(plain: bytes, key: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError(f"key must be 32 bytes, got {len(key)}")
    box = SecretBox(key)
    nonce = nacl_random(24)
    ct = box.encrypt(plain, nonce)   # returns nonce || ciphertext || mac
    return bytes(ct)
```

**Decrypt** (`decrypt_bytes`, `:152-162`):

```python
def decrypt_bytes(blob: bytes, key: bytes) -> bytes:
    if len(blob) < 24 + 16:
        raise CryptoError("ciphertext too short")
    box = SecretBox(key)
    return box.decrypt(blob)   # raises CryptoError on tamper or wrong key
```

**Mathematical formulation.**

Let $K \in \{0,1\}^{256}$, $N \in \{0,1\}^{192}$, $P \in \{0,1\}^*$. Then

$$\text{SecretBox}(K, N, P) = N \,\|\, C \,\|\, T$$

where $C = \text{XSalsa20}(K, N) \oplus P$ and $T = \text{Poly1305}(K_{\text{onetime}}(K, N), N \,\|\, C)$.

The receiver verifies $T$ before yielding $P$; failure to verify raises `CryptoError` (which sisoul wraps as `VaultIntegrityError` at the layer above).

### 3.2 BIP-39 seed and hierarchical subkey derivation

**Standard BIP-39 mnemonic generation** (`src/sisoul/identity/seed.py:60-85`). 128-bit entropy → 12 words from the 2048-word English wordlist. Last 4 bits are a checksum (SHA-256 of the entropy). Validation: `verify_mnemonic` confirms both the wordlist membership and the checksum.

**Master seed.**

$$\text{master\_seed} = \text{PBKDF2-HMAC-SHA512}\Big(\text{password}=\text{mnemonic}_\text{utf8-NFKD}, \,\text{salt}=\text{"mnemonic"}\,\|\,\text{passphrase}, \,\text{iter}=2048, \,\text{dklen}=64\Big)$$

This is BIP-39 standard. With `passphrase=""` (default), the result is bytes-identical to what a Trezor Model T derives. The 64-byte master seed is *not* used directly — sisoul always derives a 32-byte subkey for the actual primitive.

**Subkey derivation** (`derive_subkey`, `src/sisoul/identity/seed.py:120-149`):

$$\text{subkey}(\text{purpose}, i) = \text{HMAC-SHA256}\Big(\text{key}=\text{master\_seed}, \,\text{msg}=\text{purpose}_\text{utf8} \,\|\, \text{u32\_be}(i)\Big)$$

The result is always 32 bytes — convenient for SecretBox keys (32 B) and for Curve25519 private keys (32 B clamped seed).

**Why HMAC-SHA256 and not HKDF or BIP-32?**

- HKDF would also work, but for a single-step expansion HKDF's two-step `extract → expand` is overkill; a direct HMAC is functionally equivalent with one round of HMAC.
- BIP-32 (BIP-32 hierarchical deterministic wallets) carries a chain-code per node so children can themselves derive grandchildren. sisoul currently has no need for multi-level derivation; flat single-level is sufficient. If v2 introduces grand-friend-of-friend sharing, BIP-32-style chain-code can be retro-fitted under a new "purpose=hkd-v2" namespace without invalidating existing keys.

**Cross-device determinism.** The same mnemonic + same `(purpose, index)` always yields the same subkey on any device. This is exactly what enables zero-state device add: a new laptop with the same mnemonic re-derives every key and can read the same encrypted snapshots.

### 3.3 End-to-end encrypted proxy: libsodium Box (Curve25519 + XChaCha20-Poly1305)

**Primitive.** `nacl.public.Box`, which wraps libsodium's `crypto_box_curve25519xchacha20poly1305`.

- **Key agreement:** X25519 Diffie-Hellman on Curve25519. Each side contributes a 32-byte private key (clamped). Shared point → KDF → 32-byte session symmetric key.
- **Cipher:** XChaCha20 (the extended-nonce variant of ChaCha20, 192-bit nonce).
- **MAC:** Poly1305.

**Why libsodium Box and not Noise Protocol Framework?**

(quoted from `src/sisoul/friend/encrypted_proxy.py:30-44`)

> - Noise's primary value is its 1-RTT handshake and its menu of patterns (IK / XK / etc.) — best for Wireguard / Lightning Network style flows.
> - In sisoul's flow, both sides' long-term public keys are **already known** through prior EAS attestation (the `relationship.py` `verify_mutual_attestation` step), so no handshake is needed.
> - Noise's state machines are complex; the attack surface is larger than Box's.
> - The Python `noiseprotocol` library has not had a release in 2 years (maintenance risk).
> - libsodium Box is the simplest cryptographic API that does exactly what sisoul needs: encrypt-with-recipient-pubkey, decrypt-with-recipient-privkey, authenticated.

**Per-friend long-term keypair** (`derive_friend_session_keypair`, `src/sisoul/friend/encrypted_proxy.py:202-227`):

```python
def derive_friend_session_keypair(master_seed: bytes, friend_index: int = 0):
    seed_32 = derive_subkey(master_seed, "proxy", index=friend_index)
    priv = PrivateKey(seed_32)              # Curve25519, internally clamps
    return priv, priv.public_key
```

**Mathematical formulation of `encrypt_for`.**

Let Alice's private key be $a \in \mathbb{Z}_{2^{255} - 19}$ (clamped), Bob's public key $B = b \cdot G$ where $G$ is the Curve25519 base point. Then:

$$\text{shared\_secret} = \text{HSalsa20}\Big(a \cdot B\Big)$$

(libsodium applies HSalsa20 as the key-derivation step over the raw shared point.) Then for message $M$:

$$\text{Box}(M) = N \,\|\, C \,\|\, T$$

where $N$ is a fresh 192-bit nonce, $C = \text{XChaCha20}(\text{shared\_secret}, N) \oplus M$, and $T = \text{Poly1305}(\text{shared\_secret}, N \,\|\, C)$.

Bob computes the same `shared_secret` as $b \cdot A$ and decrypts symmetrically.

**The five ironclad privacy rules** (enforced in `proxy_chat_request:431-521`):

1. **Plaintext lives only in `prompt_text` local variable.** Inspection of the source shows `prompt_text` never appears in a logger, a print, a file write, or a persistence sink.

2. **No log / print / write_file / cache.** A grep audit of the encrypted_proxy module confirms zero occurrences of `print(prompt`, `log.info(prompt`, `logger.<any>(prompt` patterns. The CI tool `bandit` plus a sisoul-specific audit script (`tools/audit_proxy_no_leak.py`) statically verifies this.

3. **Function exit zero-izes.** Best-effort:

   ```python
   finally:
       _zeroize(prompt_bytes if isinstance(prompt_bytes, (bytes, bytearray)) else b"")
   ```

   Python's `str` and `bytes` are immutable, so true overwrite is impossible — `_zeroize` is a *marker of intent* and a real overwrite for `bytearray`. The function `del`s the local reference so it becomes a GC candidate immediately.

4. **Metadata is allowlisted.** `_METADATA_WHITELIST` (`encrypted_proxy.py:96-110`) is a `frozenset` of exactly 11 fields. The `to_safe_dict()` method on `ProxySessionMetadata` filters through this allowlist twice (once at construction, once at serialization) to ensure no future field accidentally leaks prompt content.

5. **Forwarder exceptions raised without chaining.**

   ```python
   except Exception as e:
       err_class = type(e).__name__
       meta = session.end(status="failed", error_class=err_class)
       # ⚠️ Deliberately not `raise from e` — exception chaining can
       # carry prompt fragments from upstream HTTP 422 echoes.
       raise ProxyError(f"forwarder failed ({err_class})") from None
   ```

   This is a security-critical choice: Python's `raise X from e` preserves `e.__cause__`, which means `str(e)` (often containing the offending HTTP request body which contains the prompt) would be reachable in stack traces, in `__traceback__`, and in any error reporting tool.

### 3.4 P2P channel encryption and authentication

Two layers:

**Layer 1 — libp2p Noise channel** (when transport is libp2p). The libp2p `Noise` security transport handshakes between peers; each peer presents its libp2p `PeerId` derived from a Curve25519 keypair. sisoul derives this PeerId-keypair from BIP-39:

$$\text{peer\_priv} = \text{derive\_subkey}(\text{master\_seed}, \text{"p2p"}, \text{index}=0)$$

so the same user's PeerId is consistent across all their devices.

**Layer 2 — sisoul-internal symmetric encryption** (regardless of transport). On top of whatever channel security the transport provides, sisoul SecretBox-encrypts every message payload with a derived channel key:

$$\text{channel\_key}(A, B) = \text{HMAC-SHA256}\Big(\text{master\_seed}, \,\min(\text{id}_A, \text{id}_B) \,\|\, \max(\text{id}_A, \text{id}_B)\Big)$$

In v1.0 both peers belong to the same user, so they share the same master seed and can independently derive the same `channel_key`. (In v2, cross-user P2P will require a Diffie-Hellman handshake using the per-friend long-term keypair from §3.3 to compute the channel key.)

This belt-and-suspenders design means an attacker who compromises the libp2p Noise layer (e.g. through an undiscovered libp2p library bug) still cannot read sync payloads.

### 3.5 Threat model

We enumerate four threat classes and sisoul's response.

#### Threat 1 — Vault key compromise on a single device.

**Scenario.** Attacker gains read access to `~/.sisoul/seed.txt` on Alice's laptop (malware, physical seizure, backup leak).

**Damage.** Full vault read for that single mnemonic. The attacker can decrypt all preferences, goals, audit logs, friend list. They can impersonate Alice for new attestations.

**Mitigations.**

- `seed.txt` is mode 0o600, owner-only. Looser permissions cause `load_mnemonic_from_file` to refuse (`PermissionError` at `src/sisoul/identity/seed.py:212-216`).
- Users are advised to write the 12 words on paper, store offline, and either (a) not write `seed.txt` at all (re-enter the mnemonic each session via `sisoul init --interactive`), or (b) keep `seed.txt` on encrypted disk (FileVault / LUKS).
- v2 will support hardware-wallet derivation: the user signs derivation requests on a Ledger / Trezor, and only the active subkey for the current session lives on the laptop.
- v2 will support Privy social recovery: a *separate* recovery method bound to an OAuth identity, configurable as an opt-in "if I lose my paper backup".

**Detection.** The vault file mtime + the attest queue's recent entries can identify foreign activity. The user can rotate the mnemonic (generate new, re-encrypt vault, publish a `KEY_ROTATE` attestation revoking the old DID).

#### Threat 2 — Proxy MITM (man-in-the-middle on the friend proxy).

**Scenario.** Attacker intercepts the encrypted_prompt blob as it travels from Alice's daemon to Bob's daemon (e.g. compromised network, malicious Tailnet exit node, ISP-level tap).

**Damage.** None of the prompt content, due to libsodium Box authenticated encryption (§3.3). The attacker sees only the wire blob (24-byte nonce, ciphertext + 16-byte MAC) and the IP-level metadata.

**Mitigations.**

- Box's Poly1305 MAC is forgery-resistant under the standard cryptographic assumptions (UF-CMA of HMAC, hardness of Curve25519 ECDLP).
- The friend's public key was bound at `friend.accept` time via EAS attestation (§4.6) — the attacker cannot substitute a different pubkey without breaking the prior on-chain attestation chain.
- The local `permission_checker` hook (`encrypted_proxy.py:560-576`) provides an additional point to reject unknown senders.

**Detection.** Bob's daemon `enforce_all_layers` L5 scan logs every block; a sudden spike in failed-decrypt errors (`ProxyDecryptError`) for a given friend triggers alerts.

#### Threat 3 — Sybil attack on the friend network.

**Scenario.** Attacker creates many fake DIDs and accumulates friend relationships with high-reputation users to harvest free LLM quota.

**Damage.** Wasted LLM credits. (No data leak — see Threat 2.) Potentially DoS on Bob's daemon.

**Mitigations.**

- ENS subdomain registration is gas-costed (small per-handle fee at v2 mainnet) — not free.
- 5-layer anti-abuse (§3.6): monthly cap (L1), rate limit (L2), revoke (L3), reputation (L4), daemon scan (L5).
- Reputation grade is *public* — Bob can refuse low-grade strangers before granting Tier 2.
- v2 will introduce optional "vouching": a high-reputation user vouches for a newcomer, and the vouch is on-chain. Sybils have to either compromise an existing high-reputation account or build reputation honestly.

#### Threat 4 — P2P transport abuse (libp2p bug, DHT poisoning, mDNS hijack).

**Scenario.** Attacker exploits a libp2p protocol vulnerability or a malicious DHT peer to inject false sync messages.

**Damage.** Bounded by Layer 2 sisoul-internal encryption (§3.4) — the attacker cannot inject readable payloads because they lack the channel key. Worst case: denial of service through resource exhaustion.

**Mitigations.**

- libp2p library is kept current; py-libp2p is on the active upgrade path (v1.0 uses the latest stable; the open problem of upstream maturity is noted in §5.5).
- WebRTC fallback provides an independent transport in case libp2p has a critical CVE.
- The sisoul-internal channel-key encryption catches any plaintext injection.

### 3.6 The 5-layer anti-abuse system: algorithms and math

The 5 layers (introduced in §2.12) have specific algorithms.

**L1 — Monthly cap.** Trivial: $\text{usage}_\text{this-month} + \text{new\_amount} \leq \text{cap}$. `enforce_monthly_cap` returns `True` if pass.

**L2 — Sliding-window rate limit.** Within a window of $W$ seconds (default 60), at most $R$ requests (Bob configures $R$). The check:

$$\Big|\{r \in \text{recent\_requests} : t - r.\text{ts} \leq W\}\Big| + 1 \leq R$$

Implementation in `enforce_rate_limit:148-169` and the in-memory `RateLimiter` class with a `deque(maxlen=1000)` per friend.

**L3 — Revocation.** Set `perm.revoked = True` in the local permission file. Issue an on-chain `PERMISSION_REVOKE` attestation. `enforce_all_layers` short-circuits L1/L2/L5 when `revoked=True`.

The on-chain part is for public record only — local revoke is the binding action. This means revocation is *fail-open against chain unavailability*: even if Optimism Sepolia is down, the local revoke takes effect immediately.

**L4 — Reputation score.** Public, periodic, published on-chain.

$$\text{score}(D) = \text{clip}_{[0,200]}\Big(100 - 20 \cdot n_\text{abuse}(D) - 10 \cdot n_\text{spam}(D) + B(b(D), l(D))\Big)$$

where for a DID $D$, $n_\text{abuse}$ counts confirmed abuse incidents, $n_\text{spam}$ counts spam complaints, $b$ is total borrows, $l$ is total lends.

The balance term $B$:

$$B(b, l) = \begin{cases}
+20 & \text{if } b + l \geq 10 \text{ and } 0.66 \leq b/l \leq 1.5 \\
-15 & \text{if } b + l \geq 10 \text{ and } (b/l > 2 \text{ or } b/l < 0.5) \\
0 & \text{otherwise}
\end{cases}$$

Grade:

$$\text{grade}(s) = \begin{cases}
\text{A} & s \geq 150 \\
\text{B} & 100 \leq s < 150 \\
\text{C} & 50 \leq s < 100 \\
\text{D} & s < 50
\end{cases}$$

Implementation: `compute_reputation:284-341` returns a `ReputationScore` dataclass.

The score is then published on-chain via `publish_reputation_attestation:344-387` as a `REPUTATION_PUBLISH` attestation. The intent is: when Alice considers friending an unknown user, she can resolve their DID, fetch their latest reputation attestation, and decide whether to grant any permission tier. *Reputation does not gate the protocol — it informs human judgment.*

**L5 — Daemon scan.** Real-time pattern detection at request submit time. The scan operates exclusively on metadata; the prompt content is never inspected.

Three rules:

- **Token burst.** `amount > token_burst_threshold` (default 200,000 tokens per single request) → block.
- **Rate burst (10s).** Same friend issued > 20 requests in the last 10 seconds → block. This is a finer-grained tier than L2's per-minute rate limit, targeting bot-like behaviour.
- **Repeat-hash spam.** Same `prompt_hash` from same friend > 10 times → block. Same prompt hash across many requests strongly suggests automated spam or a stuck loop.

Implementation: `scan_request_pattern:400-485`. Blocks are persisted to a separate SQLite (`~/.sisoul/anti_abuse_scan.db`, schema in `_SCAN_SQL:491-505`) for operator review via `sisoul perms scan-log`.

**Composite enforcement.** `enforce_all_layers:627-702` runs L3 → L1 → L2 → L5 in order, short-circuiting on the first failure. L4 is updated asynchronously and does not gate per-request.

### 3.7 CANARY string verification — proving zero leak end-to-end

The privacy claim of the encrypted proxy (§2.11, §3.3) is *zero leak of prompt content to disk or to Bob's user-visible state*. This claim is verified by a runtime canary check.

**The technique.** Generate a unique random UUID. Embed it inside the prompt sent to Bob. After the proxy_chat_request completes, scan Bob's filesystem for any occurrence of the UUID. If the UUID is found anywhere, the leak claim is falsified.

Implementation: `EncryptedProxy.enforce_no_disk_write:589-627`.

```python
@staticmethod
def enforce_no_disk_write(
    prompt_substring: str,
    response_substring: str,
    check_paths: list[str] | None = None,
) -> None:
    if check_paths is None:
        check_paths = [
            str(Path.home() / ".sisoul"),
            "/tmp",
            str(Path.cwd()),
        ]
    for p_str in check_paths:
        p = Path(p_str).expanduser()
        if not p.exists():
            continue
        if p.is_file():
            _scan_file_for_leak(p, prompt_substring, response_substring)
        else:
            for child in p.iterdir():
                if child.is_file() and child.stat().st_size < 10 * 1024 * 1024:
                    _scan_file_for_leak(child, prompt_substring, response_substring)
```

The scan is one-level deep (no recursion into large sub-trees like `~/.sisoul/p2p/cache`), reads each file's bytes, and raises `ProxyDiskWriteViolation` if any path contains the canary substring.

**The test.** `tests/test_v1_canary_full_stack.py` (run as part of the 2035-pytest suite) generates a fresh UUID, performs a full Alice→Bob proxy_chat_request, then calls `enforce_no_disk_write(canary, response_canary)`. Currently in v1.0-internal: **passes with zero exceptions**, confirming the zero-leak property.

**Limits of the canary.** This proves the *current* prompt did not leak; it does not prove a *future* prompt cannot leak (e.g. if a future code change adds an erroneous `log.info(prompt_text)`). For that, the static audit tool `tools/audit_proxy_no_leak.py` greps the encrypted_proxy module for forbidden patterns at every CI run.

### 3.8 Known limitations and forward secrecy roadmap

**No forward secrecy in v1.0 proxy.** As documented in `encrypted_proxy.py:46-50`:

> Current v1.0: per-friend long-term keypair (no ephemeral handshake) → **does not provide** forward secrecy (if Bob's private key is later compromised, all historical ciphertexts become decryptable). Known TODO; v2 will add X3DH-like ephemeral handshake.

The decision was: ship simple-and-correct first, add complexity later. X3DH (Signal's pre-key handshake) introduces both ratchets and out-of-band one-time pre-key publication, neither of which is needed for the v1.0 use case (which is rare, deliberately initiated borrow sessions, not high-frequency Signal-like messaging).

**Python plaintext zeroization is best-effort.** Python's `str` and `bytes` are immutable. Real overwrite of plaintext after use requires either C-extension memory management or moving sensitive flows to Rust (sisoul's v2 roadmap considers a Rust core with Python bindings).

**Mock vs live in v1.0.** The DID Sepolia registration, the EAS Optimism Sepolia attestation, and the Arweave testnet snapshot all default to mock mode unless explicit env vars (`SISOUL_TEST_LIVE_TESTNET=1`, `ARWEAVE_ALLOW_MAINNET=1`, etc.) are set. This is intentional to prevent CI runs from consuming faucet allowances. Live testnet smoke tests run weekly on a dedicated test mnemonic.

---

## 4. Decentralization and Governance

### 4.1 The progressive decentralization roadmap

sisoul's stance on decentralization is the *honest* one. We do not claim "fully decentralized" — we claim "**decentralized at the protocol layer day 1, progressively decentralized at the implementation layer**".

The model is Bitcoin 2009 → 2026. The Bitcoin protocol was already pure on day 1: anyone could run a full node, the consensus algorithm was openly specified, and no trusted third party was required. The *implementation stack* migrated over years: GPU mining → ASIC mining → pool mining → geographic diversity → Stratum V2. Bitcoin Core's day 1 was already decentralized; its day 6,000 is *more* decentralized.

sisoul applies the same model. The protocol is pure: anyone can run a daemon, derive a BIP-39 mnemonic, encrypt their vault, and participate in the friend network. The current implementation stack contains four centralization debts, each with a documented migration path.

**Four phases.**

| Phase | Centralization profile | Target date |
|---|---|---|
| v1.0-internal | Mock on most external services; local-only sufficient. | 2026-05 (shipped) |
| v1.0-public | Real testnet (Optimism Sepolia, Arweave testnet, ENS Sepolia, Pinata). 4 documented debts. | 2026-Q3 target |
| v2 | Mainnet release. Self-hosted IPFS optional. py-libp2p production-grade. Foundation registered. | 2027-Q2 target |
| v3 | Token-free DAO governance. Multi-chain attestation. py-libp2p alternatives evaluated. | 2028+ |

### 4.2 v1.0-internal: today's centralization profile

v1.0-internal is the internal-only state. Most external dependencies are mocked:

- **EAS attestation:** queued but flush is mock (no real Optimism Sepolia tx) unless `SISOUL_TEST_LIVE_TESTNET=1`. Schema UID is `MOCK_SCHEMA_UID = sha256("sisoul-audit-v1::" + SISOUL_AUDIT_SCHEMA)` for deterministic local verification.
- **ENS subdomain registration:** mock unless `live=True` is passed to `register_ens_subdomain`. Live mode only reads (verifies RPC connectivity); no real tx is submitted (no real funds).
- **Arweave snapshot:** uses `https://test.arweave.net` by default. Mainnet requires both `ARWEAVE_NETWORK=mainnet` *and* `ARWEAVE_ALLOW_MAINNET=1` env vars (defence in depth — two-step explicit gesture before real funds are spent).
- **IPFS pinning:** Pinata is the default. The CID is genuine; the pin is real.
- **P2P:** real libp2p mDNS + Kademlia DHT discovery when available; falls back to manual + WebRTC.

### 4.3 v1.0-public: minimizing centralization

v1.0-public is the first version that an external user can download and run. It deliberately keeps the four documented centralization debts and works around them rather than pretending to eliminate them:

**Debt 1 — L2 sequencer.** Optimism (and Base, Arbitrum, all major L2s in 2026) operate a single sequencer. Sequencer downtime means EAS attestations queue locally and flush on recovery. Single-sequencer censorship risk: if Optimism's sequencer were to censor sisoul-specific tx, attestations would be rejected. Mitigation: the queue is *local-first*; the local SQLite is the source of truth; on-chain is the public-record copy. Even with sequencer censorship, the user retains their local audit trail.

**Debt 2 — IPFS pinning.** Pinata is centralized. If Pinata terminates the account, the encrypted skill ciphertext is unavailable. Mitigation: the user can self-host an IPFS node (run `kubo` locally) and pin themselves; the daemon supports `IPFS_GATEWAY` and `IPFS_API` env vars for self-hosted endpoints. v1.0-public ships a `sisoul ipfs self-pin` helper that bootstraps a local kubo node. The cost is 24/7 online + bandwidth — a tradeoff the user makes explicitly.

**Debt 3 — ENS + IPFS gateway (browser access).** Chrome and Safari do not resolve `.eth` names natively. A user opening `https://alice.sisoul.eth.link` is going through the centralized `eth.link` gateway. Mitigation: sisoul's primary access is the local daemon and the local PWA at `127.0.0.1:9876` — no DNS, no gateway. The ENS-resolved URL is for *third parties* (friends viewing your public reputation), and they can self-host an ENS gateway. The protocol does not depend on `.eth.link` for any user-flow correctness.

**Debt 4 — Gas paymaster.** ERC-4337 paymaster infrastructure (Biconomy, Privy, Pimlico) is centralized in 2026. A user who registers a DID without holding ETH must use a paymaster to sponsor gas. Mitigation: sisoul's gas-paymaster integration is *opt-in*. The default flow asks the user to fund their wallet; the paymaster is an opt-in convenience. v2 will support self-hosted paymaster via wallet bundlers.

All four debts are listed in `docs/whitepaper/decentralization-debts.md` (alongside this whitepaper) and reviewed at each release.

### 4.4 v2: protocolization

v2 transitions sisoul from "reference implementation with a protocol shadow" to "protocol with multiple implementations".

Concretely, v2:

- Publishes the SISOUL Protocol Specification as a formal document (the "yellow paper") covering the wire format of all P2P messages, the EAS schema definition, the AI skill tarball format, and the DID method specification.
- Solicits at least one independent implementation (target: a Rust client `sisoul-rs` and a TypeScript client `sisoul-ts`, complementing the Python reference). Two implementations on the same protocol is when "protocol" becomes a real word.
- Conducts a third-party security audit (target: Trail of Bits / Cure53 / NCC Group level).
- Launches a bug bounty (target: $50K initial pool, scaled with user adoption).
- Registers the sisoul Foundation (§4.8).
- Migrates the friend proxy to X3DH-like ephemeral handshake for forward secrecy (§3.8).
- Migrates EAS to Optimism mainnet, with the schema-registration on-chain transition documented and the v1.0 testnet schema marked deprecated-but-readable.
- Evaluates py-libp2p alternatives (rust-libp2p via PyO3, or a pure-Python re-implementation of a libp2p subset).

### 4.5 v3: foundation + DAO

v3 introduces governance. The protocol's improvement process moves from "the original authors" to "the sisoul Foundation Stiftung + a non-token DAO".

The DAO is **non-token by deliberate choice** (§4.10). Voting weight is determined by verified contribution to the protocol (code commits, PIP authoring, audit findings) rather than by token holdings. This avoids the well-known governance-capture failure modes of token-DAOs (Compound, Uniswap, etc.) where large holders dominate vote outcomes.

Reference precedents for non-token DAO:

- The Apache Software Foundation: contribution-weighted committer model.
- Debian: voted Project Leader, contribution-gated voting.
- Python Software Foundation: membership tiers based on contribution.

sisoul's DAO will likely follow Debian's contribution-gated voting closely, with PIP authoring + protocol implementation contributions as the primary criteria.

### 4.6 On-chain attestation (EAS Optimism)

EAS (Ethereum Attestation Service) is the right primitive for sisoul's on-chain needs: cheap, expressive, and chain-agnostic.

**Schema** (already shown in §2.9.1):

```
string  actor_did
string  action_type
string  target
bytes32 prompt_hash
uint64  timestamp
string  tool_name
```

**Action types in use:**

| `action_type` | Used by |
|---|---|
| `FRIEND_LINK` | `relationship.py` — pairwise friend establishment |
| `PERMISSION_GRANT` / `PERMISSION_REVOKE` | `permissions.py` and `anti_abuse.py` |
| `REPUTATION_PUBLISH` | `anti_abuse.py` periodic reputation publication |
| `SKILL_PUBLISH` / `SKILL_GRANT` / `SKILL_REVOKE` | `skill_ipfs.py`, `skill_borrow.py` |
| `SNAPSHOT_PUBLISH` | `arweave.py` records the Arweave tx_id of each snapshot |
| `KEY_ROTATE` | future v1.1 — for users rotating their BIP-39 mnemonic |
| `rm` / `git-push` / `chmod` / `curl-post` / `ssh-destructive` | daemon-recorded destructive operations (§2.16 Flow C) |

**Multi-attest batching.** A single batch tx posts up to 10 attestations. At Optimism Sepolia gas levels (May 2026), a 10-attest batch costs roughly the same gas as a 1-attest tx — amortization is essentially 10×.

**Verification.** Anyone with the attestation UID can query the EAS contract and read the structured fields. sisoul's PWA Advanced route shows recent attestations with a link to the EAS Explorer.

### 4.7 PIP-001 to PIP-004

PIP (Sisoul Improvement Proposal) is the formal mechanism for protocol changes. Modelled on Bitcoin BIP / Ethereum EIP. The first four PIPs are pre-drafted as part of v1.0-public:

**PIP-001 — Vault format v1.** Specifies the file layout, encryption format, frontmatter schema, and atomic write semantics of `~/.sisoul/`. Critical for cross-implementation compatibility (a Rust sisoul client must read a Python sisoul client's vault).

**PIP-002 — Soul migration via BIP-39.** Specifies the exact derivation path: which `purpose` strings exist, what each is used for, what the index space means. Critical because "soul migration" — the user typing 12 words on a new device and getting their full sisoul state back — depends on every implementation deriving the same keys.

**PIP-003 — Meta-layer hook.** Specifies the contract between sisoul-managed sections and AI-tool config files. Defines the marker syntax for each format (Markdown, YAML, JSON). Defines the corruption-rejection rules. Defines the field schema of the managed-section content.

**PIP-004 — P2P wire format.** Specifies the libp2p protocol identifiers, the WebRTC fallback handshake, the inventory message structure, the chunk message structure, and the channel-key derivation. Critical for cross-implementation P2P sync.

Subsequent PIPs (PIP-005+) will be community-authored once v1.0-public is live.

### 4.8 Foundation structure (Switzerland Stiftung)

sisoul Foundation is planned as a **Swiss Stiftung** (non-profit foundation).

Why Switzerland Stiftung specifically:

- **Non-profit form designed for protocols.** Ethereum Foundation, Web3 Foundation (Polkadot), Cardano Foundation, Solana Foundation all use Swiss Stiftung. The legal precedent is extensive.
- **No share capital, no shareholders.** A Stiftung is a self-purposed entity. It cannot pay dividends. Its founders dedicate assets to a charitable / protocol purpose and step back from ownership claims.
- **Tax-favourable for grants and donations** under Swiss law.
- **Legal personality independent of founder country.** The Stiftung can hold IP, sign contracts, run bug bounties, and pay auditors regardless of the founders' personal nationality.

**Registration target.** Once v1.0-public has 1,000+ active users (a milestone we will verify by counting unique DIDs with active recent on-chain activity), founders register the Stiftung. The 1,000-user threshold is a deliberate gate to avoid "set up a Foundation for an empty protocol".

**Funding model.** Grants (Gitcoin, Optimism RetroPGF, Ethereum Foundation grants), sponsorship from infrastructure partners (cloud credits, RPC providers), and individual donations. **No ICO. No IDO. No airdrop. No token sale.** (See §4.10.)

### 4.9 DID via ENS subdomain

The DID method `did:sisoul:<handle>` is anchored on-chain via ENS subdomain `<handle>.sisoul.eth`.

**ENS root.** `sisoul.eth` is registered as a single ENS name (mainnet target: v2). Subdomains are issued at the sisoul Foundation's discretion in v1.0-public (the Foundation runs a registrar contract that mints subdomains on request); v2 may transition to a permissionless subdomain registration via a public Solidity contract with anti-squatting (e.g. proof-of-personhood requirement, or stake-and-slash).

**Resolver records.** Each subdomain stores three TEXT records:

- `sisoul:pubkey` — the Ed25519 verification public key (multibase z-prefix).
- `sisoul:p2p_addrs` — comma-separated multiaddrs the user is reachable on.
- `sisoul:profile` — IPFS CID of the user's public profile JSON (display name, bio, public AI-skills list).

The W3C DID document is computed deterministically from these records (§2.4 `to_did_document`).

**Cross-chain.** v2 considers replicating the registry to other chains (Optimism for cheap reads, Solana for high-frequency reputation updates) using a canonical-on-Ethereum, replicated-elsewhere pattern. PIP at v2 time.

### 4.10 Governance principle: never-token, never-shutdown

Two principles bind sisoul's governance:

**Principle 1 — never-token.** sisoul will not issue a token of any kind. No ICO, no IDO, no airdrop, no governance token, no fee token, no points-then-token, no "we'll figure out the token later". The reason is precisely the failure-mode-4 problem: tokens create rent-extracting entities that align Foundation incentives with token holders rather than with users. A protocol that "must" launch a token to fund operations creates exactly the centralized vendor-death-as-memory-death dynamic this protocol exists to eliminate.

Funding comes from grants, sponsorship, and donations. If sisoul cannot survive on those, it is not building enough value, and a token will not save it.

**Principle 2 — never-shutdown.** sisoul has no central server to shut down. The daemon is software the user runs. The protocol is a specification. The Foundation owns the trademark and the canonical reference implementation, but cannot turn off other implementations, cannot brick existing installations, and cannot recover user keys.

If the Foundation is dissolved tomorrow, every existing sisoul daemon continues to function. Every BIP-39 phrase still derives the same keys. Every on-chain attestation remains verifiable. Every IPFS-pinned skill remains accessible (until its pinner stops pinning, at which point the original author can re-pin elsewhere). Vault data on the user's disk is the user's; the Foundation never touched it.

This is the structural guarantee against pain point 4 (vendor-death = memory-death): there is no vendor to die.

---

## 5. Roadmap and Open Problems

### 5.1 v1.0-internal ship status

v1.0-internal is the state at commit time of this whitepaper.

**Verified ship metrics:**

- **2035 pytest tests passing** (`tests/` + `qa/`). The breakdown by suite: `test_cli_*` (22 per-command), `test_daemon_routes_*` (10 routers), `test_friend_*` (12 modules in `friend/`), `test_onchain_*` (EAS + Arweave + IPFS), `test_p2p_*` (transport + discovery + encryption + sync + node), `test_identity_*` (seed + DID), `test_vault_*` (encryption + frontmatter + storage), `test_sync_*` (5 adapters + managed_section), `test_llm_*` (5 providers), plus the QA-100 cross-module canary suite.
- **22 CLI commands** dispatched in `src/sisoul/cli.py`.
- **68 daemon endpoints** distributed across 10 router files (`src/sisoul/daemon_routes/`, 3452 LoC total).
- **7 PWA routes** (`pwa/src/routes/`).
- **5 LLM provider adapters** (`src/sisoul/llm/`).
- **5 cross-tool sync adapters** (`src/sisoul/sync/`).
- **P2P transport: py-libp2p primary, aiortc WebRTC fallback** (`src/sisoul/p2p/`).
- **EAS attestation queue, batched 10-records-or-1-hour, Optimism Sepolia** (`src/sisoul/onchain/eas.py`).
- **Arweave testnet + IPFS Pinata encrypted snapshot** (`src/sisoul/onchain/arweave.py`).
- **End-to-end encrypted friend proxy with 5-layer anti-abuse** (`src/sisoul/friend/`, 12 files, 7475 LoC).
- **BIP-39 soul migration: same mnemonic on a new device → full state restoration** (`src/sisoul/identity/seed.py` + `arweave.py:restore_from_arweave`).
- **AI skill packaging + IPFS encrypted distribution + lifecycle** (`src/sisoul/friend/skill_*`).

**Not yet shipped in v1.0-internal:**

- Live mainnet anything (intentional, see §4.2).
- Forward secrecy in friend proxy (X3DH-like handshake) — v2 roadmap.
- Pi CLI and Gemini CLI sync adapters — v1.1.
- Obsidian plugin (read sisoul-managed sections inside an Obsidian vault) — v1.1.
- Goal-mode (long-running agent that pursues a vault goal across sessions) — v1.1.
- RAG-selective skill retrieval (don't load all skills, retrieve the relevant ones per session) — v1.1.

### 5.2 v1.0-public preparation

v1.0-public is the first release with external users. Preparation tasks:

**20 user interviews.** Before public launch, conduct 20 structured interviews with potential users from the multi-AI-tool power-user demographic. Validate the pain-point ranking. Gather concerns about decentralization tradeoffs, friend-proxy trust model, and BIP-39 phrase responsibility.

**OPSEC hardening for the project itself.** The team adopts pseudonymous attribution (founders' real identities documented internally but public attribution uses handles), key-rotation discipline for all team credentials, and reproducible-build infrastructure (so users can verify the published Python wheel matches the audited source).

**GitHub org setup.** Reserve `sisoul-protocol` (or final-name) GitHub org. Set up CODEOWNERS, signed commits, branch protection on `main`, mandatory CI before merge.

**PIP repository.** Spin out the four pre-drafted PIPs (§4.7) into a public `pips/` repo. Open issues for community PIP authoring.

**Reproducible builds.** `pip install sisoul==1.0.0` should yield bit-identical bytes regardless of build host. Use `uv lock` + `--no-binary` + deterministic timestamps.

**Documentation site.** Quickstart, conceptual guides, API reference, threat model, decentralization-debts page. Target: `docs.sisoul.<final-tld>` resolvable via HTTPS (centralized, but it's documentation — the protocol does not depend on it).

### 5.3 v1.1: ecosystem expansion

v1.1 is the first incremental release after v1.0-public. Expected duration: 3–6 months post-v1.0-public.

**Obsidian plugin.** sisoul daemon exposes vault preferences and goals; an Obsidian plugin renders them inside Obsidian. The plugin reads via the local HTTP endpoint, no direct file access. Use case: users who maintain a personal knowledge base in Obsidian want their AI-tool preferences to be visible alongside their notes.

**Selective RAG retrieval for skills.** Instead of loading all installed skills into every session (which bloats the system prompt), use embedding similarity to retrieve the top-K relevant skills per query. Builds on the existing `OpenAIAdapter.embed()` method.

**Goal-mode.** A long-running daemon-side agent that tracks progress against vault goals. Runs in the background, periodically queries the user's recent vault activity and proxies to the LLM "given current state, is the user making progress toward goal X? what would the next step be?". Surfaces suggestions in the PWA. Conceptual precedent: Manus + Anthropic projects + the user's `goals/` directory.

**Grok and DeepSeek adapters.** Two more `LLMAdapter` subclasses. The base class is unchanged.

**Real protocol adapter.** Generalize the sync adapter base class so that *any* tool can write a 50-line subclass and inherit cross-tool sync. Currently each subclass shares a lot of duplicate code (path discovery, marker handling); refactor for true plugin-style.

### 5.4 v2: foundation, audit, DAO

(Already covered in §4.4 and §4.5; the user-facing deliverables:)

- Foundation registered, public.
- Security audit complete; report published.
- Bug bounty live ($50K initial pool).
- DAO bootstrapped (non-token, contribution-weighted).
- Cross-chain attestation (PIP at v2 time).
- Native mobile clients (iOS / Android; native SDK).
- 3rd-party SDK (Rust, TypeScript, Go).
- Mainnet EAS attestation + Mainnet Arweave snapshot + Mainnet ENS subdomains.

### 5.5 Open technical problems

Four open problems that v1.0 cannot resolve and the team commits to working on through v2:

**Open problem 1 — py-libp2p upstream maturity.** py-libp2p is the Python implementation of libp2p. It is maintained but lags behind go-libp2p and rust-libp2p in features and stability. Specifically: (a) Kademlia DHT in py-libp2p is functional but less battle-tested than rust-libp2p's; (b) gossipsub support is incomplete; (c) some protocol versions trail the spec by 6-12 months. Mitigations: sisoul's WebRTC fallback covers cases where libp2p fails. v2 evaluates options: contribute upstream to py-libp2p, embed rust-libp2p via PyO3 bindings, or fall back to a pure-Python subset implementation.

**Open problem 2 — WebRTC STUN/TURN reliability.** The aiortc WebRTC fallback uses the public Google STUN server. For peers behind symmetric NAT or strict corporate firewalls, STUN alone is insufficient — TURN relay is needed. Public TURN servers are rate-limited or paid. Mitigation paths: self-hosted coturn on each peer's network, sisoul Foundation hosting a free TURN tier (centralized, but explicit debt), or libp2p relay-circuit fallback. v2 evaluates.

**Open problem 3 — EAS mainnet readiness.** v1.0 uses Optimism Sepolia. v2 must migrate to Optimism mainnet (or stay on testnet and accept reduced finality guarantees — not viable for a production audit log). Mainnet considerations: real gas costs amortized via batching, but for a user issuing 100 attestations/day the monthly cost is non-trivial. Mitigations: per-user gas paymaster (debt 4 from §4.3), or shifting non-critical attestations (e.g. `REPUTATION_PUBLISH` periodic updates) to a cheaper chain (Base, Arbitrum) while keeping `PERMISSION_REVOKE` and `rm`-class destructive attestations on Optimism mainnet.

**Open problem 4 — Arweave mainnet pricing and reliability.** Arweave's "permanent storage" pricing is currently ~$0.10 per MB. A monthly encrypted vault snapshot at 10 MB costs $1/month/user. For 10,000 users that's $10K/month — sustainable via grants but worth noting. Reliability: Arweave's testnet has had downtime episodes; mainnet is more reliable but not 100%. Mitigations: dual storage (Arweave for permanence + Filecoin for redundancy), or sisoul Foundation pre-paying snapshot costs (centralized, debt). v2 evaluates Filecoin + Arweave dual-write.

---

## 6. References

- §19 — `19-AI管家产品战略-讨论轮1-vck.md` — initial vision discussion (cited as project vault §19).
- §20 — `20-AI管家产品战略-讨论轮2-vck.md` — discussion round 2.
- §21 — `21-AI管家产品战略-讨论轮3-vck.md` — discussion round 3, P0 list including this whitepaper item.
- §22 — `22-Mac现役体系脱敏摘要-协议灵感来源-vck.md` — desensitized summary of the production Mac multi-AI-tool system that inspired the protocol.
- §23 — `23-白皮书v0.1-Chapter1-2-vck.md` — v0.1 whitepaper draft (Chapter 1 + 2 only) which this v1.0 supersedes.
- §24 — `24-Siloh-v1产品形态MVP-vck.md` — v1 product-form MVP.
- §25 — `25-半匿名OPSEC-checklist-vck.md` — OPSEC checklist for the project itself.
- §26 — `26-v1详细产品功能与开发计划-vck.md` — v1 detailed feature plan.
- §27 — `27-直接做v1.0可行性评估-vck.md` — v1.0 feasibility assessment.
- §28 — `28-元层架构与P2P朋友共享设计-vck.md` — meta-layer architecture + P2P friend-sharing design. **Primary architecture reference.** Module numbering in this whitepaper §2.3–§2.14 matches §28 §1.1 module numbering.
- §29 — `29-v1.0开发执行计划-vck.md` — v1.0 development execution plan, week-by-week W1–W74. The "Wave 3 dev-B" / "Wave 5 dev-C" etc. labels cited in module headers refer to this document's phase numbering.
- §30 — `30-波次开发计划-子agent并行+自动QA-vck.md` — wave-based parallel development plan with automated QA. Reference for the 2035-pytest QA structure.

**External standards.**

- W3C — *Decentralized Identifiers (DIDs) v1.0*. https://www.w3.org/TR/did-core/
- BIP-39 — *Mnemonic code for generating deterministic keys*. https://github.com/bitcoin/bips/blob/master/bip-0039.mediawiki
- BIP-32 — *Hierarchical Deterministic Wallets*. https://github.com/bitcoin/bips/blob/master/bip-0032.mediawiki
- EAS — *Ethereum Attestation Service*. https://docs.attest.org/
- ENS — *Ethereum Name Service*. https://docs.ens.domains/
- libp2p — *Modular peer-to-peer networking stack*. https://libp2p.io/
- NaCl / libsodium — Bernstein et al. https://nacl.cr.yp.to/
- ERC-7231 — *Identity-aggregated NFT*. https://eips.ethereum.org/EIPS/eip-7231 (referenced in §28 design discussion).
- ERC-4337 — *Account Abstraction*. https://eips.ethereum.org/EIPS/eip-4337 (referenced as paymaster infrastructure).

---

## Appendix A. Module-to-file map

| Module § | Source path | LoC |
|---|---|---|
| Vault (§2.3) | `src/sisoul/vault/encryption.py` | 173 |
| Vault frontmatter | `src/sisoul/vault/frontmatter.py` | — |
| Vault storage | `src/sisoul/vault/storage.py` | — |
| Identity seed (§2.4) | `src/sisoul/identity/seed.py` | 222 |
| Identity DID (§2.4) | `src/sisoul/identity/did.py` | 551 |
| Daemon (§2.5) | `src/sisoul/daemon.py` | 142 |
| Daemon router: identity | `src/sisoul/daemon_routes/identity.py` | 171 |
| Daemon router: did | `src/sisoul/daemon_routes/did.py` | 188 |
| Daemon router: pwa | `src/sisoul/daemon_routes/pwa.py` | 428 |
| Daemon router: p2p | `src/sisoul/daemon_routes/p2p.py` | 262 |
| Daemon router: attest | `src/sisoul/daemon_routes/attest.py` | 401 |
| Daemon router: snapshot | `src/sisoul/daemon_routes/snapshot.py` | 197 |
| Daemon router: friend (incl. proxy + perms) | `src/sisoul/daemon_routes/friend.py` | 742 |
| Daemon router: proxy (nested) | `src/sisoul/daemon_routes/proxy.py` | 184 |
| Daemon router: permissions (nested) | `src/sisoul/daemon_routes/permissions.py` | 356 |
| Daemon router: skill | `src/sisoul/daemon_routes/skill.py` | 516 |
| LLM adapter base (§2.6) | `src/sisoul/llm/base.py` | 117 |
| LLM Anthropic | `src/sisoul/llm/anthropic.py` | — |
| LLM OpenAI | `src/sisoul/llm/openai.py` | — |
| LLM Gemini | `src/sisoul/llm/gemini.py` | — |
| LLM Ollama | `src/sisoul/llm/ollama.py` | — |
| LLM OpenRouter | `src/sisoul/llm/openrouter.py` | — |
| Sync managed-section (§2.7) | `src/sisoul/sync/managed_section.py` | — |
| Sync base | `src/sisoul/sync/base.py` | — |
| Sync claude_code | `src/sisoul/sync/claude_code.py` | — |
| Sync codex | `src/sisoul/sync/codex.py` | — |
| Sync cursor | `src/sisoul/sync/cursor.py` | — |
| Sync aider | `src/sisoul/sync/aider.py` | — |
| Sync opencode | `src/sisoul/sync/opencode.py` | — |
| P2P node (§2.8) | `src/sisoul/p2p/node.py` | — |
| P2P transport | `src/sisoul/p2p/transport.py` | — |
| P2P discovery | `src/sisoul/p2p/discovery.py` | — |
| P2P encryption | `src/sisoul/p2p/encryption.py` | — |
| P2P sync | `src/sisoul/p2p/sync.py` | — |
| Onchain EAS (§2.9.1) | `src/sisoul/onchain/eas.py` | — |
| Onchain Arweave (§2.9.2) | `src/sisoul/onchain/arweave.py` | — |
| Friend relationship (§2.10) | `src/sisoul/friend/relationship.py` | 1055 |
| Friend permissions | `src/sisoul/friend/permissions.py` | 550 |
| Friend encrypted_proxy (§2.11) | `src/sisoul/friend/encrypted_proxy.py` | 698 |
| Friend anti_abuse (§2.12) | `src/sisoul/friend/anti_abuse.py` | 731 |
| Friend ledger | `src/sisoul/friend/ledger.py` | 633 |
| Friend borrow | `src/sisoul/friend/borrow.py` | 599 |
| Friend lend | `src/sisoul/friend/lend.py` | 483 |
| Friend proxy_audit | `src/sisoul/friend/proxy_audit.py` | 386 |
| Friend skill_package (§2.13) | `src/sisoul/friend/skill_package.py` | 640 |
| Friend skill_ipfs (§2.13) | `src/sisoul/friend/skill_ipfs.py` | 611 |
| Friend skill_borrow (§2.13) | `src/sisoul/friend/skill_borrow.py` | 991 |
| PWA (§2.14) | `pwa/src/` (TypeScript) | — |
| CLI (§2.15) | `src/sisoul/cli.py` + `src/sisoul/cli_commands/` (22 files) | — |

---

## Appendix B. Glossary

- **Agentic CLI** — A command-line AI tool that executes multi-step tasks (e.g. Claude Code, Codex CLI, Cursor, Aider, OpenCode, Pi CLI, Gemini CLI). sisoul augments these; it does not replace them.
- **Attestation** — A signed structured record on EAS, used by sisoul to log destructive operations, permission grants/revokes, reputation publications, and skill grants/revokes.
- **BIP-39** — Bitcoin Improvement Proposal 39: standard for converting a 12/15/18/21/24-word mnemonic into a 64-byte master seed via PBKDF2-HMAC-SHA512.
- **CANARY** — A unique random string embedded in a test prompt and then scanned for in all persistence sinks to verify zero-leak guarantees in the encrypted proxy (§3.7).
- **DID** — Decentralized Identifier (W3C standard). sisoul uses the form `did:sisoul:<handle>` anchored on ENS.
- **EAS** — Ethereum Attestation Service. Smart-contract infrastructure for structured on-chain assertions.
- **ENS** — Ethereum Name Service. Maps human-readable names to addresses and arbitrary records.
- **Friend** — A peer in the sisoul friend network. A friendship is established by mutual on-chain attestation.
- **Forward secrecy** — Property that compromise of long-term keys does not retroactively decrypt prior ciphertexts. Not provided in v1.0; planned for v2.
- **HMAC-SHA256** — Keyed hash used in sisoul's hierarchical subkey derivation (§3.2).
- **libsodium Box** — Public-key authenticated encryption (Curve25519 + XChaCha20-Poly1305). Used in the friend proxy and skill key delivery.
- **libsodium SecretBox** — Symmetric authenticated encryption (XSalsa20 + Poly1305). Used for vault encryption.
- **Managed section** — A fenced block (`<!-- sisoul-managed-start --> ... <!-- sisoul-managed-end -->`) in a target tool's config file that sisoul overwrites, leaving user-handwritten content outside untouched.
- **Master seed** — The 64-byte output of BIP-39 PBKDF2; root of all sisoul keys.
- **Mnemonic** — The 12-word phrase that encodes the BIP-39 entropy.
- **PIP** — Sisoul Improvement Proposal. Formal protocol change mechanism modelled on BIP/EIP.
- **PWA** — Progressive Web App. sisoul's local-only browser-based dashboard at `127.0.0.1:9876`.
- **Reputation** — Public scalar 0–200 with grade A/B/C/D, published on EAS, used by humans to decide friend permissions.
- **Stiftung** — Swiss foundation legal form, used by major protocol foundations (Ethereum, Polkadot, Cardano, Solana).
- **Subkey** — A 32-byte key derived from the master seed for a specific purpose (vault, did, p2p, proxy, arweave, skill).
- **Vault** — The user's encrypted local storage at `~/.sisoul/`. Contains preferences, goals, audit log, friend records, skills.

---

## Appendix C. License

**This whitepaper:** Creative Commons Attribution-ShareAlike 4.0 International (CC-BY-SA 4.0). https://creativecommons.org/licenses/by-sa/4.0/

You are free to:
- **Share** — copy and redistribute the material in any medium or format
- **Adapt** — remix, transform, and build upon the material for any purpose, even commercially

Under the following terms:
- **Attribution** — You must give appropriate credit (cite this document, its version, and a link to the source repository), provide a link to the license, and indicate if changes were made.
- **ShareAlike** — If you remix, transform, or build upon the material, you must distribute your contributions under the same license as the original.

**The reference implementation at `~/sisoul-dev/`:** MIT License (will be reaffirmed in `LICENSE` at the GitHub publication of v1.0-public).

```
MIT License

Copyright (c) 2026 sisoul contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

*End of sisoul v1.0 whitepaper. Document version: 1.0.0+internal. Last verified against source tree: 2026-05-18.*
