# sisoul v1.0.0-alpha Launch Checklist

> One-pass checklist for the day you push GitHub + launch alpha.
> Each step has copyable command + expected output + rollback.

## Pre-launch (T-0:30)

- [ ] `cd ~/sisoul-dev && git status` — working tree clean
- [ ] `git tag | grep v1.0.0-alpha` — tag exists
- [ ] `git log --oneline 4d42a45^..HEAD | wc -l` — ≥ 43 commits
- [ ] `.venv/bin/pytest tests -q --tb=line --ignore=tests/test_v1_integration_full_user_journey.py | tail -1` — expect `≥2060 passed / 0 failed`
- [ ] `cd pwa && npm run build && cd ..` — exit 0
- [ ] `shellcheck -S warning ops/install.sh ops/init/install-autostart.sh` — 0 warnings
- [ ] `.venv/bin/sisoul --version` — shows 1.0.0-alpha box

## GitHub repo creation (T-0:15)

Manual (avoid scripted `gh repo create` initial — sets visibility wrong):

- [ ] Open https://github.com/new
- [ ] name=`sisoul`, owner=`sisoul` (or personal), visibility=`public`, init empty
- [ ] `git remote add origin git@github.com:akige/sisoul.git` (or HTTPS+PAT)
- [ ] `git push -u origin main`
- [ ] `git push origin v1.0.0-alpha` — triggers `.github/workflows/release.yml`
- [ ] Visit https://github.com/akige/sisoul/actions — watch release workflow
- [ ] Visit https://github.com/akige/sisoul/releases — verify v1.0.0-alpha appears with assets

## PWA gh-pages deploy (T-0:10)

- [ ] GitHub repo Settings → Pages → Source = `GitHub Actions`
- [ ] `git push` triggers `pwa/.github/workflows/deploy-gh-pages.yml` automatically
- [ ] Visit https://akige.github.io/sisoul/ — should load index.html
- [ ] Open browser console — no 404s on lazy chunks

## Verify install path (T-0:05)

On a clean machine (or `--dry-run`):

- [ ] `curl -sSfL https://github.com/akige/sisoul/releases/latest/download/install.sh | bash` (skip if you trust ops/install.sh shellcheck PASS)
- [ ] OR install from source: `pip install -e ~/sisoul-dev` then `sisoul --version`
- [ ] `sisoul init` — wizard 5 steps complete without error
- [ ] `sisoul daemon start --background` — daemon up on :9876
- [ ] `sisoul health` — exit 0, daemon ok, v2 routes 10+
- [ ] `sisoul demo` — all 8 steps print ✓
- [ ] `sisoul stats` — shows cases/skills counters

## Announcement (T+0)

In priority order (use `docs/ALPHA-LAUNCH-ANNOUNCEMENT-DRAFTS.md` templates):

- [ ] Discord (low-risk): post in ETHGlobal / IPFS / Optimism `#general`
- [ ] Farcaster: cast in `/sisoul` channel
- [ ] Twitter: thread (5 tweets, include PWA screenshot)
- [ ] (T+1 day) V2EX 中文社区: 1-line title + body
- [ ] (T+1 week) HN Show HN: Tuesday/Wednesday 8-10am PT
- [ ] (T+1 week) Reddit r/selfhosted

## Monitoring (T+1h ~ T+1week)

- [ ] `gh release view v1.0.0-alpha --json assets` — check `download_count`
- [ ] `gh issue list --label critical` — daily check P0
- [ ] Watch GitHub stars + PWA gh-pages traffic (Settings → Insights)
- [ ] Daemon `sisoul health` from your own machine — sanity baseline

## Rollback / hotfix path

If alpha breaks for users:

- [ ] `git revert <bad-commit>` + `git push`
- [ ] `git tag v1.0.1-alpha` + `git push origin v1.0.1-alpha` — new release
- [ ] Pin GitHub release as "Latest" again
- [ ] Twitter/Discord 1-tweet advisory: "v1.0.0-alpha → v1.0.1-alpha bug fix"

## What's NOT in alpha (intentionally — set expectations early)

- ❌ Multi-agent debate full impl (v3.0 ship, T+15-18m) — `sisoul debate` runs but synthesize is mock
- ❌ Mainnet DAO + SIS token (v1.0 stable, T+6m) — alpha uses Sepolia testnet stub
- ❌ Personal LoRA training (v2.0 末, T+12m) — schema only
- ❌ ChromaDB embed retrieval (v2.0 ship, T+8-10m) — current uses TfIdf foundation
- ❌ Native Android/iOS apps (beta v1.1, T+1m) — only PWA
- ❌ Hot-load skill marketplace (v2.0 ship, T+11m) — install + list works, hot-load TBD

## Success criteria (L1 alpha, target 90% prob)

- T+1 week: ≥ 50 install.sh downloads
- T+1 month: ≥ 100 active users
- 0 P0 critical bugs persisting 7 days
- 5 alpha real-use scenarios ≥ 80% PASS rate (from user feedback)

(L2/L3/L4 success criteria: see obs §61 + §67)
