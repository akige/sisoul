# sisoul Architecture

> Code layout + module dependencies + data flow + extension points.
> For users: see `docs/QUICK-START.md`. For contributors: continue here.

## Directory layout

```
sisoul/
├── src/sisoul/                        # Python core
│   ├── __init__.py                    # version + phase
│   ├── cli.py                         # typer app (20 top-level commands)
│   ├── banner.py                      # ASCII banner on daemon start
│   ├── daemon.py                      # FastAPI factory create_app()
│   ├── cli_commands/                  # Individual CLI command modules
│   │   ├── init.py                    # 5-step wizard
│   │   ├── login.py / ask.py          # LLM provider
│   │   ├── stats.py / health.py       # daemon visibility
│   │   ├── demo.py / cheatsheet.py    # demo + reference
│   │   ├── invite.py / completion.py  # alpha UX
│   │   ├── v2_case.py / v2_debate.py  # v2.0 client CLI
│   │   ├── friend_discover.py         # mDNS + petname-aware
│   │   ├── backup.py                  # one-command vault backup
│   │   ├── qr.py                      # QR add-friend
│   │   ├── chat.py                    # Signal-grade chat CLI
│   │   ├── friend.py / borrow.py / lend.py
│   │   ├── skill.py / dao.py
│   │   ├── snapshot.py / restore.py / export.py
│   │   └── ...
│   ├── daemon_routes/                 # FastAPI routers (17 v2/* + 1 metrics)
│   │   ├── v2_case.py / v2_skill.py / v2_more.py
│   │   ├── metrics.py                 # Prometheus exposition
│   │   ├── friend.py / proxy.py / chat.py
│   │   ├── identity.py / did.py / pwa.py
│   │   ├── snapshot.py / attest.py / rag.py / goal.py / dao.py / notify.py
│   │   ├── permissions.py / skill.py / openai_compat.py / p2p.py
│   │   └── ...
│   ├── v2/                            # v2.0 智能体网络 modules
│   │   ├── __init__.py
│   │   ├── case_graph/                # Case Graph (T+8-10m ship)
│   │   │   ├── schema.py              # Case dataclass + CaseRetrieval
│   │   │   ├── store.py               # CaseStore (vault/cases/)
│   │   │   └── vector_index.py        # TfIdfIndex (foundation; v2.0 ship → ChromaDB)
│   │   ├── personal_lora/             # Personal LoRA (T+12m)
│   │   │   ├── schema.py              # LoRAAdapter + TrainingConfig
│   │   │   ├── trainer.py             # PersonalLoRATrainer (stub)
│   │   │   └── federated.py           # FederatedLoRAAggregator (FedAvg stub)
│   │   ├── provenance/                # Provenance Chain (T+10m citations / T+15m SIS)
│   │   │   ├── schema.py              # Citation + ProvenanceChain
│   │   │   ├── attester.py            # build_chain + ProvenanceAttester
│   │   │   └── eas_client.py          # EASClient (Optimism Sepolia/Mainnet/mock)
│   │   ├── skill_marketplace/         # Skill Marketplace (T+11m)
│   │   │   ├── schema.py              # SkillManifest + SkillInstallResult
│   │   │   ├── installer.py           # SkillInstaller (mock IPFS pull)
│   │   │   └── publisher.py           # SkillPublisher (sha256 + mock IPFS CID)
│   │   ├── debate.py                  # Multi-Agent Debate (T+15m, 3-round)
│   │   ├── reputation.py              # ReputationRouter (top-K + 20% exploration design)
│   │   ├── memory_compaction.py       # MemoryCompactor + Lesson (T+11m)
│   │   ├── growth.py                  # GrowthLogger + DailyGrowthSnapshot
│   │   └── pipeline.py                # V2AskPipeline (end-to-end ask integration)
│   ├── chat/                          # Signal-grade chat (P2-G subagent ship)
│   │   ├── double_ratchet.py          # Open Whisper Systems protocol
│   │   ├── pqxdh.py                   # X25519 + ML-KEM-1024 hybrid handshake
│   │   ├── session.py                 # ChatSession state machine
│   │   └── transport.py               # KuboGossipSubTransport + MemoryTransport
│   ├── friend/                        # Friend + mDNS + Petname
│   │   ├── mdns.py                    # zeroconf _sisoul._tcp.local. service
│   │   ├── petname.py                 # ~/.sisoul/petnames.json
│   │   ├── borrow.py / lend.py        # P2P LLM borrow flow
│   │   └── encrypted_proxy.py         # libsodium box E2E proxy
│   ├── p2p/                           # kubo IPFS + GossipSub
│   │   ├── ipfs_kubo.py               # kubo daemon embed
│   │   ├── transport.py               # KuboTransport
│   │   ├── node.py / sync.py
│   │   └── ...
│   ├── identity/                      # did:key + BIP-39 + Shamir
│   │   ├── seed.py / shamir.py / did_key.py
│   │   └── ...
│   ├── vault/                         # Markdown vault
│   ├── providers/                     # 9 LLM provider adapters
│   ├── llm/                           # LLM client wrappers
│   ├── onchain/                       # Helios + EAS + Arweave
│   ├── dao/                           # SisoulGov + Snapshot + Optimism timelock
│   ├── goal/                          # Goal-mode scheduler
│   ├── rag/                           # RAG selective inject
│   ├── rpc/                           # JSON-RPC
│   └── sync/ vsync/                   # Vault sync (W7-W10 + Wave M P2P)
├── pwa/                               # SolidJS + Vite PWA
│   ├── src/
│   │   ├── App.tsx / main.tsx
│   │   ├── routes/                    # 15 routes (6 new v2)
│   │   ├── api/v2.ts                  # /v2/* TypeScript client
│   │   ├── components/                # Sidebar + TopBar
│   │   └── utils/
│   ├── vite.config.ts                 # base '/sisoul-pwa/' for gh-pages
│   ├── package.json
│   └── .github/workflows/deploy-gh-pages.yml
├── contracts/                         # Solidity (DAO + SIS Token + Airdrop + RPGF)
├── tests/                             # 2069 pytest cases
│   ├── test_alpha_launch_e2e.py       # alpha real-use scenarios
│   ├── test_alpha_daemon_smoke.py     # real uvicorn subprocess + httpx
│   ├── test_v2_*.py                   # v2.0 foundation + daemon + pipeline + cli + demo
│   └── ...
├── docs/                              # Public documentation
│   ├── QUICK-START.md / ARCHITECTURE.md / FAQ.md
│   ├── ALPHA-LAUNCH-PLAYBOOK.md / ALPHA-LAUNCH-ANNOUNCEMENT-DRAFTS.md
│   ├── whitepaper/sisoul-whitepaper-v1.0.md   # 14 chapters
│   └── internal/                      # dev notes (gitignored on launch)
├── ops/                               # Install + autostart
│   ├── install.sh / install-dev.sh / test-install.sh
│   └── init/                          # systemd + launchd templates
├── .github/                           # CI + templates
│   ├── workflows/{ci.yml, release.yml}
│   ├── ISSUE_TEMPLATE/{bug_report.md, feature_request.md}
│   └── PULL_REQUEST_TEMPLATE.md
├── sdk/                               # Third-party client libraries (TS/Python/Rust)
├── obsidian-plugin/                   # Optional Obsidian plugin
├── mobile/                            # iOS / Android skeleton
├── README.md / SECURITY.md / CONTRIBUTING.md / CHANGELOG.md
├── RELEASE-NOTES-v1.0-alpha.md / ALPHA-LAUNCH-CHECKLIST.md / USER-WAKEUP-SUMMARY.md
├── VERSION  pyproject.toml  uv.lock
└── README-internal.md                 # legacy internal README
```

## Module dependency graph

```
                ┌─────────┐
                │  CLI    │ (18+ commands)
                └────┬────┘
                     │
        ┌────────────┴────────────┐
        ▼                          ▼
   ┌─────────┐               ┌───────────┐
   │ Vault   │               │  Daemon   │ (FastAPI :9876)
   │ + Init  │               └─────┬─────┘
   └────┬────┘                     │
        │                          ▼
        ▼              ┌───────────────────┐
   ┌─────────┐         │  daemon_routes/   │ (17 v2/* + metrics)
   │ Identity│◄────────┤  v2_case          │
   │ + Seed  │         │  v2_skill         │
   └─────────┘         │  v2_more          │
        │              │  metrics          │
        ▼              │  ...              │
   ┌─────────┐         └─────────┬─────────┘
   │ P2P     │                   │
   │ (kubo)  │◄──────────────────┘
   └────┬────┘                   ▼
        │              ┌───────────────────┐
        ▼              │  v2/* modules     │
   ┌─────────┐         │  case_graph       │
   │ Friend  │◄────────┤  personal_lora    │
   │ + mDNS  │         │  provenance       │
   │ + Pet   │         │  skill_marketplace│
   └────┬────┘         │  debate           │
        │              │  reputation       │
        ▼              │  memory_compaction│
   ┌─────────┐         │  growth           │
   │ Chat    │         │  pipeline         │
   │ (PQXDH+ │         └───────────────────┘
   │ Ratchet)│
   └─────────┘
```

## Data flow: end-to-end ask

```
sisoul ask "How to fix Rust async deadlock?"
  │
  ▼
CLI → daemon /v2/case/search (TfIdf retrieval)
  │
  ▼ retrieved cases
daemon → V2AskPipeline.ask()
  │
  ▼
provider adapter (Anthropic / OpenAI / borrowed friend)
  │
  ▼ answer text
ProvenanceChain (citations + EAS attest)
  │
  ▼ attestation UID (mock or Optimism L2)
new Case auto-written to vault/cases/<id>.json
  │
  ▼
TfIdfIndex.add(case) → searchable next time
  │
  ▼
ReputationRouter.update(cited authors, +0.05) → topic rep climbs
  │
  ▼
return AskResponse (answer + citations + attestation_uid)
```

## Extension points (where to add code)

| You want to | Edit |
|---|---|
| Add new CLI command | `src/sisoul/cli_commands/<name>.py` + wire in `cli.py` |
| Add HTTP API endpoint | `src/sisoul/daemon_routes/<name>.py` + include in `daemon.py` |
| Add LLM provider | `src/sisoul/providers/<name>.py` extends `ProviderAdapter` |
| Add v2 module | `src/sisoul/v2/<name>.py` + foundation tests |
| Add PWA route | `pwa/src/routes/<Name>.tsx` + register in `App.tsx` |
| Add daemon route to PWA client | `pwa/src/api/v2.ts` |

## Test layering

```
tests/
├── unit (per-module)           # *.py specific
├── test_v2_foundation.py       # v2 schemas + skeletons
├── test_v2_daemon_routes.py    # FastAPI TestClient (no real daemon)
├── test_v2_pipeline.py         # cross-module integration
├── test_alpha_launch_e2e.py    # alpha real-use scenarios (mock + light fixtures)
├── test_alpha_launch_e2e_full.py   # daemon.create_app TestClient
└── test_alpha_daemon_smoke.py  # real uvicorn subprocess + httpx (highest fidelity)
```

Bottom is most realistic; top is fastest. Each layer adds latency but catches different bugs.

## State on disk

```
~/.sisoul/
├── dna.json                    # vault DNA (version, master_key_hash)
├── did_key.json                # did:key + private_key
├── petnames.json               # local nickname mappings
├── seed.txt                    # BIP-39 12-word (chmod 600)
├── friends/                    # friend records JSON
├── cases/                      # vault/cases/<id>.json (v2 Case Graph)
├── lessons/                    # vault/lessons/<id>.json (v2 Memory Compaction)
├── growth/                     # vault/growth/<date>.json (v2 Growth Logger)
├── cases_index.json            # TfIdfIndex metadata
├── skills/                     # installed skill packages
├── chat/sessions/<peer-did>.json  # SecretBox-encrypted Double Ratchet state
├── chat/prekeys/<peer-did>.json   # pre-key bundles (24h refresh)
├── lora/personal-v3.safetensors   # personal LoRA adapter (v2.0)
├── logs/daemon.{out,err}.log   # daemon logs (launchd)
└── kubo/                       # kubo IPFS repo (200-400MB)
```

## Build and release flow

```
git commit → git push → GitHub Actions
  │
  ├─► ci.yml (pytest matrix 3.11/3.12 + PWA build + shellcheck)
  │
  └─► release.yml (on tag push v*)
       │
       ├─► python -m build (wheel + sdist)
       ├─► cosign sign-blob --yes (keyless OIDC + Rekor)
       ├─► tar release/sisoul-{tag}-linux-x86_64.tar.gz
       └─► softprops/action-gh-release@v2 publish GitHub Releases
```

## Rollback procedures

| Failure | Recovery |
|---|---|
| Bad release published | `git revert <bad-commit>` + push + tag `v1.0.1-alpha` |
| daemon won't start | `~/.sisoul/seed.txt` + `sisoul restore --from-seed` |
| Vault corruption | `sisoul restore --from-zip ~/sisoul-backup-*.zip` |
| Friend offline | LLM borrow fallback: borrow from another friend (multi-provider routing v2.0) |
| sigstore release MITM detected | Foundation 3-of-5 multisig issues revocation announcement; users `cosign verify` fails → install.sh aborts |

## Performance baselines (alpha, foundation)

| Operation | Latency | Memory |
|---|---|---|
| daemon startup | ~3s (FastAPI + import) | ~250MB RES |
| /sisoul/health | <10ms | — |
| /v2/case (add) | ~5ms | — |
| /v2/case/search (100 cases TfIdf) | ~50ms | — |
| /v2/debate/run (3-round mock) | ~30ms | — |
| sisoul demo (8 steps) | ~2-3s end-to-end | — |
| pytest full suite | 75-80s (2069 tests) | — |

v2.0 ship (ChromaDB + real LLM): expect +500ms/query (sentence-transformer encode) + +1-3s/query (real LLM).

---

🤖 Generated as part of alpha launch readiness. Updated whenever architecture shifts.
