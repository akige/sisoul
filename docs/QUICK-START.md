# sisoul Quick Start

> 5-minute first-run guide. Picks up from `install.sh` completion.

## 1. Install

```bash
curl -sSfL https://github.com/akige/sisoul/releases/latest/download/install.sh | bash
```

Verify:

```bash
sisoul --version
```

Expected: ASCII box with `sisoul 1.0.0-alpha`.

## 2. First-time setup (`sisoul init`)

```bash
sisoul init
```

5 prompts:

1. **Petname** (your local nickname) — default = hostname
2. **did:key** — auto-generated ed25519 identity (no signup, no email)
3. **LLM provider** — pick from 9: anthropic / openai / gemini / copilot-oauth / ollama / akash / bittensor / hyperbolic / litellm-generic / skip
4. **Daemon mode** — background (recommended) or foreground
5. **QR** — generates a PNG for friends to scan

Result: `~/.sisoul/` created with `did_key.json`, `petnames.json`, `dna.json`.

## 3. Start daemon

```bash
sisoul daemon --host 127.0.0.1 --port 9876
```

You'll see the sisoul ASCII banner + endpoint URLs.

Or run as autostart service (Linux systemd / Mac launchd):

```bash
bash ops/init/install-autostart.sh
# Mac: then launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.sisoul.daemon.plist
```

## 4. Verify everything works

```bash
sisoul health      # daemon + 17 v2 endpoints + Prometheus metrics
sisoul demo        # 8-step end-to-end showcase (case → search → attest → debate → lesson)
sisoul stats       # local counters (case/skill/friend/petname/lesson)
sisoul cheatsheet  # all 19 commands one-page
```

## 5. Add your first friend

### Option A: LAN (mDNS)

```bash
sisoul friend-discover  # scans 5 sec, lists local sisoul peers
sisoul friend petname set <did:key:z6Mk...> Alice
```

### Option B: QR

You generate, friend scans:

```bash
sisoul friend qr --out ~/sisoul-add-me.png  # send PNG to friend
```

Friend scans (in their PWA or `sisoul friend qr-scan`):

```bash
sisoul friend qr-scan ~/path/to/your-qr.png
```

### Option C: Manual DID

```bash
sisoul invite --did did:key:<yours> --petname <yours>
# share generated text via IM / Slack / Discord
# friend runs:
sisoul friend add did:key:<yours>
```

## 6. Use it

```bash
# Ask LLM (uses your provider, or borrows from friend if no key)
sisoul ask "How to fix Rust async tokio::select deadlock?"

# Multi-agent debate (v3.0 preview, current: 3-round mock)
sisoul debate "PostgreSQL pgbouncer transaction pooling + sqlx prepared statement?"

# Chat (Signal-grade E2E, post-quantum hybrid)
sisoul chat send did:key:z6MkFriend "Hi, want to test sisoul?"
sisoul chat recv

# Borrow LLM from friend (when you don't have API key)
sisoul borrow request did:key:z6MkFriend
```

## 7. Browse data

```bash
sisoul case list                  # all cases (CLI)
sisoul case search "tokio"        # search
sisoul case show <case-id>        # full detail
```

Or open PWA in browser:

```
http://127.0.0.1:9876/docs                   # FastAPI Swagger UI
https://akige.github.io/sisoul/         # PWA dashboard
```

PWA routes:
- `/dashboard/v2` — growth curve + case/skill stats
- `/ask` — ask through case retrieval + EAS attest
- `/debate` — multi-agent debate UI
- `/skills/v2` — skill marketplace browse + install
- `/stats` — Prometheus metrics dashboard
- `/cheatsheet` — CLI reference (browser version)

## 8. Backup

```bash
sisoul backup --out ~/sisoul-2026-06-04.zip
```

## 9. Get help

```bash
sisoul <command> --help     # any command
sisoul cheatsheet           # all 19 commands
sisoul --version-json       # JSON version info
```

## 10. Shell autocomplete (optional)

```bash
# bash
sisoul completion bash --install
# Add to ~/.bashrc: source ~/.bash_completion.d/sisoul

# zsh
sisoul completion zsh --install
# Add to ~/.zshrc: source ~/.zsh/completions/_sisoul

# fish
sisoul completion fish --install
```

## What's NOT in alpha (set expectations)

- ❌ Multi-agent debate full LLM (current: 3-round mock synthesize)
- ❌ Personal LoRA training (v2.0 末, T+12m)
- ❌ ChromaDB vector embed (current: TF-IDF foundation)
- ❌ Optimism mainnet DAO / SIS token (v1.0 stable, T+6m)
- ❌ Native Android/iOS apps (beta v1.1, T+1m)
- ❌ Hot-load skill marketplace (v2.0, T+11m)

Roadmap: see `README.md` § Roadmap and `obs §61 / §67`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `sisoul: command not found` | Add `~/.local/bin` to PATH |
| daemon port 9876 in use | `lsof -i :9876` and kill, or use `sisoul daemon --port 9999` |
| `health` says daemon unreachable | Daemon not started — run `sisoul daemon` in foreground to see errors |
| `friend-discover` finds 0 peers | Same LAN + same subnet required for mDNS; firewall blocking UDP 5353? |
| PQXDH "shim mode" warning | `pip install kyber-py` for real ML-KEM-1024 |
| cross-NAT borrow fails | `sisoul net status` to check kubo IPFS peer count |

## Next: join the alpha network

After your daemon is up:

1. Share your DID with friends (sisoul invite generates text/QR)
2. Add 2-3 friends to bootstrap your circle
3. Try `sisoul borrow request <friend-did>` if they have an API key you can use
4. Write your first case: ask a question with `sisoul ask` — daemon auto-records it
5. Watch growth curve: PWA `/dashboard/v2` or `sisoul stats`

Welcome to the network. ⚡
