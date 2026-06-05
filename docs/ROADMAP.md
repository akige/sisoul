# sisoul Public Roadmap

> Living doc. Updated quarterly. Last updated: 2026-06-05.

## Status legend

- ✅ shipped
- 🚧 in progress  
- 🎯 next quarter target
- 🔮 vision (T+12m+)
- ❓ open question (community input wanted)

## v1.0-alpha (Today, T+0)

✅ did:key Ed25519 identity + BIP-39 backup + Shamir 3-of-5 secret-sharing
✅ Friends (QR exchange + mDNS LAN discovery + DID-based)
✅ 9 LLM provider adapters (Anthropic + OpenAI + Google + Ollama + Grok + DeepSeek + Mistral + Cohere + Replicate)
✅ Borrow LLM cross-NAT P2P (Alice borrows Bob's API key, signed + PQXDH-encrypted)
✅ Signal-grade chat (Double Ratchet + PQXDH X25519+ML-KEM-1024 hybrid)
✅ SolidJS PWA (15 routes incl. Vault/Friends/Borrow/Lend/Skills/Chat/Stats/Cheatsheet)
✅ kubo IPFS embedded (GossipSub + Circuit Relay v2 + AutoNAT + DCUtR)
✅ sigstore cosign signed releases
✅ Docker stack (daemon + Prometheus + Grafana)
✅ 21 CLI commands + 19 v2 HTTP endpoints
✅ Apache-2.0 license + Threat model + Protocol spec for 3rd-party impls

## v1.x maintenance (T+0 to T+1m)

🚧 Tag v1.0.0-alpha → public GitHub
🎯 5-10 alpha testers feedback loop
🎯 IPFS bootstrap reliability (median connect time < 10s)
🎯 PWA accessibility audit (WCAG 2.1 AA)
🎯 i18n: zh-CN + en done; ja + es + fr next
🎯 Mobile PWA install flow polish (iOS + Android)
🎯 Bug bounty program (Immunefi or HackerOne)

## v2.0 智能体网络 (T+12m)

> Focus: turn solo agent into networked knowledge graph.

🔮 **Case Graph (real)** — ChromaDB + sentence-transformers, semantic case retrieval (currently TfIdf foundation only)
🔮 **Personal LoRA** — PEFT/Unsloth real fine-tune on user's chat history (currently rule-based stub)
🔮 **Provenance Chain** — EAS mainnet attestation per response, cite sources
🔮 **Skill Marketplace** — IPFS skill bundles + cosign-verified install, search/discover
🔮 **MLS group chat** — Messaging Layer Security for >2 participants
🔮 **Cold-storage Shamir UI** — PWA wizard prints 5 QR codes for offline backup
🔮 **Hardware key vault unlock** — YubiKey + WebAuthn

## v3.0 超级智能体 (T+18m)

> Focus: many agents debating + economy.

🔮 **Multi-Agent Debate** — 3-7 personas debate, judge picks winner, log to provenance chain
🔮 **Federated LoRA** — share gradient updates (not raw data) across friends to improve local model
🔮 **SIS micropayment** — on-chain credit for Borrow LLM (currently free among friends)
🔮 **Reputation graph** — verifiable reviews of skills/agents
🔮 **Onion routing for Borrow** — Tor/Nym for prompt anonymity from lender

## L4 涌现 (T+36m, probability target 25-35%)

> What "涌现" looks like: behaviors no one designed, emerging from the network.

🔮 Cross-user knowledge graphs no single user could build
🔮 Skills auto-improving via federated feedback
🔮 Emergent dialect of agent-to-agent communication
🔮 Self-healing P2P topology
🔮 Self-funded SIS treasury supporting protocol dev

**Probability target**: 25-35% by T+36m. Not guaranteed; we will report quarterly on signals.

## Open questions ❓ (community input)

❓ Should default IPFS bootstrap include sisoul-operated nodes? (Trade-off: bootstrap reliability vs zero-server promise)

❓ Vault portability: SQLite vs JSON files? (Currently JSON for grep-ability + portability)

❓ Onion routing default-on for Borrow? (Privacy vs latency: Tor adds 200-1000ms)

❓ SIS token launch timing: with v3.0 (T+18m) or sooner? (Regulatory + community readiness)

❓ Mobile: PWA forever, or native iOS/Android in v2.0? (Push notif + biometric vault unlock are PWA-limited)

Vote / discuss via GitHub Discussions when repo is live.

## Anti-roadmap (intentionally NOT building)

❌ sisoul-operated centralized server (any service)
❌ Email/SMS account (use did:key)
❌ Built-in LLM inference (use any provider via adapter)
❌ Custom blockchain (use Optimism L2 / EAS / ENS)
❌ Closed-source binaries (Apache-2.0 forever)
❌ Telemetry / analytics on user behavior
❌ Ads / sponsorship / freemium tiers
❌ Phone home / auto-update without consent

## Release cadence

- **alpha**: every 2 weeks while < 100 users
- **beta** (after 100 users): monthly
- **stable** (after security audit): quarterly LTS

## Funding model

- v1: maintainer (current). No company, no fundraise yet.
- v2+: open to grants (Optimism RetroPGF, IPFS Impact, etc.)
- v3+: SIS treasury (1% per transaction) funds dev (if community votes for it)

Never accept funding with strings (e.g. close-source obligation, telemetry, jurisdiction lock).

---

🤝 **Get involved**: GitHub Discussions / Issues / PRs. THANKS.md tracks all contributors.
