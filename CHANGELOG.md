# Changelog

All notable changes to sisoul are documented here. Format: [Keep a Changelog](https://keepachangelog.com/) + [SemVer](https://semver.org/).

## [1.0.0-alpha] — 2026-06-04

Initial public alpha release.

### Added

#### Core protocol (Wave A-M, pre-sprint)
- did:key identity (Ed25519 self-sovereign, no signup)
- BIP-39 12-word mnemonic seed + Shamir 3-of-5 backup
- Vault encrypted markdown (XChaCha20-Poly1305 + ML-KEM hybrid)
- kubo IPFS daemon embedded + GossipSub P2P transport
- 9 LLM provider adapters (Anthropic / OpenAI / Gemini / Copilot OAuth / Ollama / Akash / Bittensor / Hyperbolic / LiteLLM Generic)
- Cross-NAT P2P borrow LLM (E2E encrypted proxy via friend)
- mDNS LAN friend discovery + Petname local nicknames + QR add-friend
- 5-step `sisoul init` wizard
- Signal-grade chat: Double Ratchet + PQXDH (X25519 + ML-KEM-1024 hybrid post-quantum)

#### v2.0 智能体网络 foundation (6h sprint副产)
- `src/sisoul/v2/case_graph/` — Case schema + TfIdfIndex + CaseStore
- `src/sisoul/v2/personal_lora/` — LoRAAdapter schema + PersonalLoRATrainer (stub) + FederatedLoRAAggregator (FedAvg stub)
- `src/sisoul/v2/provenance/` — Citation + ProvenanceChain + ProvenanceAttester + EASClient (Optimism Sepolia/Mainnet/mock)
- `src/sisoul/v2/skill_marketplace/` — SkillManifest + SkillInstaller + SkillPublisher (sha256 + mock IPFS CID)
- `src/sisoul/v2/debate.py` — DebateAgent + MultiAgentDebate (3-round mock)
- `src/sisoul/v2/reputation.py` — TopicReputation + ReputationRouter (top-K weighted, 20% exploration design)
- `src/sisoul/v2/memory_compaction.py` — Lesson distill + Arweave archive trigger
- `src/sisoul/v2/growth.py` — DailyGrowthSnapshot + GrowthTrend + GrowthLogger
- `src/sisoul/v2/pipeline.py` — V2AskPipeline (case retrieve → LLM mock → attest → write → rep update)

#### HTTP daemon endpoints (17 v2/* + 1 metrics)
- `POST/GET /v2/case` + `/v2/case/search` + `/v2/case/{id}` + list (4 routes)
- `POST/GET/DELETE /v2/skill` (3 routes)
- `POST /v2/provenance/attest` + `POST /v2/debate/run` + `POST /v2/reputation/{update,top-k}` (4 routes)
- `POST /v2/growth/{write,last}` + `POST /v2/lesson/distill` (3 routes)
- `GET /sisoul/metrics` Prometheus exposition (new)

#### CLI top-level (20 commands)
- New in alpha: `stats`, `debate`, `health`, `demo`, `invite`, `cheatsheet`, `completion`, `friend-discover`, `backup`
- Existing: `init`, `login`, `ask`, `remember`, `status`, `export`, `restore`, `verify`, `daemon`, `sync`
- Sub-apps: `case` (list/search/show/add), `skill`, `chat`, `friend`, `borrow`, `lend`, `goals`

#### PWA (15 routes total, 6 new v2)
- `/dashboard/v2` — growth curve + case/skill stats
- `/ask` — case retrieval + EAS attest UI
- `/debate` — multi-agent debate UI with agent rep sliders
- `/skills/v2` — skill marketplace browse + install
- `/stats` — Prometheus metrics auto-refresh (15s)
- `/cheatsheet` — CLI quick reference in browser
- TopBar: live daemon health indicator (15s poll)
- Sidebar: 6 v2 routes exposed

#### Tests (5 new test suites, 2069 total cases)
- `test_alpha_launch_e2e.py` 12 + extended 12 (24 alpha real-use scenarios)
- `test_alpha_daemon_smoke.py` 13 (subprocess uvicorn + real HTTP)
- `test_v2_foundation.py` 60 (9 v2 module schemas + skeletons)
- `test_v2_daemon_routes.py` 18 (HTTP API smoke)
- `test_v2_pipeline.py` 8 (end-to-end pipeline)
- `test_v2_federated_demo.py` 5 (v3.0 full workflow demo)
- `test_v2_cli_commands.py` 22 (all CLI smoke)

#### Documentation
- `README.md` — public version
- `RELEASE-NOTES-v1.0-alpha.md`
- `USER-WAKEUP-SUMMARY.md` (sprint dashboard)
- `ALPHA-LAUNCH-CHECKLIST.md` (launch day procedure)
- `docs/ALPHA-LAUNCH-PLAYBOOK.md` (10 sections)
- `docs/ALPHA-LAUNCH-ANNOUNCEMENT-DRAFTS.md` (6 platforms: HN/Twitter/Reddit/Farcaster/Discord/中文)
- `docs/QUICK-START.md` (10-step user guide)
- `CONTRIBUTING.md` (dev setup + PR flow)
- `SECURITY.md` (threat model + disclosure policy)
- `ops/init/README.md` (systemd + launchd install/uninstall)
- obs documents §63-§68 (sprint reports + v2/v3 spec + alpha launch SOP + L1-L4 KPI)

#### Install + Release
- `ops/install.sh` — one-line `curl|bash` install with sigstore verify
- `ops/install-dev.sh` — dev mode (uv + launchd + systemd)
- `ops/init/{sisoul-daemon.service,com.sisoul.daemon.plist,install-autostart.sh,README.md}` — autostart template
- `.github/workflows/ci.yml` — pytest matrix (Python 3.11/3.12) + PWA build + shellcheck
- `.github/workflows/release.yml` — auto build wheel + sdist + sigstore keyless OIDC sign + GitHub Releases publish
- `pwa/.github/workflows/deploy-gh-pages.yml` — auto deploy PWA to akige.github.io/sisoul/

### Tests
- 2069 pytest pass / 0 fail / 24 skip
- PWA build PASS (15 lazy chunks)
- shellcheck clean (install.sh, ops/init/install-autostart.sh)

### Known limitations (set expectations)
- Multi-agent debate runs 3-round mock synthesize (v3.0 ship T+15m: real LLM via GossipSub fanout)
- LoRA training is schema-only (v2.0 末 T+12m: real PEFT)
- ChromaDB embedding deferred (v2.0 ship T+8-10m: replaces TfIdf foundation)
- Optimism mainnet DAO + SIS token testnet only (v1.0 stable T+6m: real chain)
- No native Android/iOS apps (beta v1.1 T+1m: F-Droid + AltStore)
- Skill hot-load not implemented (v2.0 ship T+11m: importlib + Wasm sandbox)

### Security
- All releases sigstore-signed (cosign keyless OIDC + Rekor transparency log)
- Chat E2E: forward secrecy via Double Ratchet + post-quantum via ML-KEM-1024
- daemon binds 127.0.0.1 by default (no remote access)
- See `SECURITY.md` for full threat model and disclosure policy

## Sprint stats (6h window, 2026-06-04 14:50 EDT)

- 51 commits
- 80+ new files
- 13000+ LOC added
- 20 CLI commands (9 new this sprint)
- 15 PWA routes (6 new v2)
- 17 v2 daemon HTTP endpoints
- 6 obs documents
- 5 changelog 5-field entries
- 3 opus subagent worktree pairs (P2-CD mDNS+Petname / P2-EF QR+wizard+install+PWA / P2-G Signal chat) all 100% PASS, 0 regression
- T+~6h main session usage

## [Pre-1.0] Wave A-M (2026-04 → 2026-05)

Wave-by-wave dev log (16 modules ship): see obs §31-§46 in repo.

---

🤖 Developed primarily with [Claude Code](https://claude.com/claude-code). PR contributions welcome.
