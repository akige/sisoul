# Contributing to sisoul

Thanks for your interest in sisoul. This guide covers dev setup, testing, and pull-request flow.

## Dev setup

```bash
git clone https://github.com/akige/sisoul.git
cd sisoul
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,daemon,llm,chat]"
```

### Run tests

```bash
# Full suite (~70 sec, 2061+ cases)
.venv/bin/pytest tests -q --tb=line --ignore=tests/test_v1_integration_full_user_journey.py

# Specific module
.venv/bin/pytest tests/test_v2_foundation.py -v
.venv/bin/pytest tests/test_alpha_daemon_smoke.py -v   # spawns real daemon
```

### PWA dev

```bash
cd pwa
npm install
npm run dev    # vite dev server on :5173
npm run build  # production build
```

## Architecture overview

```
sisoul/
├── src/sisoul/
│   ├── cli.py              # typer app, 18 top-level commands
│   ├── cli_commands/       # individual command modules
│   ├── daemon.py           # FastAPI factory
│   ├── daemon_routes/      # HTTP API routers (17 v2/* + metrics)
│   ├── v2/                 # v2.0 智能体网络 modules
│   │   ├── case_graph/     # case retrieval (TfIdf foundation)
│   │   ├── personal_lora/  # LoRA stub
│   │   ├── provenance/     # EAS attest client
│   │   ├── skill_marketplace/  # IPFS skill install/publish
│   │   ├── debate.py       # multi-agent debate stub
│   │   ├── reputation.py   # topic-weighted routing
│   │   ├── memory_compaction.py
│   │   ├── growth.py
│   │   └── pipeline.py     # end-to-end ask
│   ├── chat/               # Signal Double Ratchet + PQXDH
│   ├── friend/             # mDNS + Petname
│   ├── p2p/                # kubo IPFS + GossipSub
│   └── ...
├── pwa/                    # SolidJS PWA (vite + tailwind)
├── tests/                  # 2061+ pytest cases
├── docs/                   # whitepaper, playbook, announcement drafts
└── ops/                    # install.sh, systemd, launchd
```

## Coding conventions

- Python: ruff format, mypy clean (`pip install ".[dev]"` for tools)
- TypeScript: tsc --noEmit must pass
- Bash: `shellcheck -S warning` 0 issues
- Commits: imperative mood, prefix with phase (`P2/P3 module-name: ...`)
- Co-author tag: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` when AI-assisted

## Pull request flow

1. Fork + branch off `main`
2. Run full pytest (must be green, 0 fail)
3. Run `cd pwa && npm run build` (must succeed)
4. Open PR with: what changed / why / test plan
5. CI must pass (pytest matrix Python 3.11/3.12 + PWA build + shellcheck)
6. Maintainer review + merge

## Roadmap (where contributions land)

| Phase | Target | Help wanted |
|---|---|---|
| alpha v1.0 | NOW | bug reports, install.sh fixes, doc improvements |
| beta v1.1 (T+1m) | Android F-Droid + iOS AltStore | mobile devs (Gradle / fastlane) |
| beta v1.2 (T+2m) | Win11 native + i18n | translators (zh/ja/es/de) |
| v1.0 stable (T+6m) | Optimism mainnet + DAO | Solidity audit, security review |
| v2.0 (T+12m) | Case retrieval + LoRA + Provenance + Skill marketplace | ML eng (PEFT), ChromaDB |
| v3.0 (T+18m) | Multi-Agent Debate + Federated LoRA + SIS micropay | distributed systems, FedAvg |
| 涌现 (T+36m) | 10K+ MAU | community growth, RPGF |

## Security

- DO NOT commit secrets / API keys (use env vars)
- DO NOT run `launchctl bootout` (known to corrupt plist — use `kickstart -k` instead)
- Sigstore signing is mandatory for releases (set up by .github/workflows/release.yml)
- Report vulnerabilities to <security@sisoul.io> (not GitHub issues)

## Communication

- GitHub Issues: bugs + feature requests
- Discussions: general questions
- Discord (after launch): real-time chat
- Twitter / Farcaster: announcements

## License

Apache-2.0. By contributing you agree your contributions are licensed under the same.

## Code of Conduct

Be kind. Assume good intent. Critique ideas, not people. Disagreements settled by maintainer veto (benevolent dictator model until v1.0 stable, then DAO governance).

---

🤖 Most of sisoul was developed with [Claude Code](https://claude.com/claude-code). PRs from AI agents welcome — please tag with `Co-Authored-By:` and disclose in PR body.
