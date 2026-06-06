# Borrow-LLM Incentive Design (Q6 + Q7)

> Why a stranger on V2EX would let you borrow their Claude key, and what it
> costs you to ask. Designed to be honest about the gap between the alpha
> reality and the v1.0-stable goal, and compatible with §4.10 never-token.

## The problem in one paragraph

Borrowing LLM quota only makes sense if **(a)** the borrower has an incentive
to ask the right friend, and **(b)** the lender has an incentive to say yes.
In the alpha v1.0 today, neither side has a monetary lever. We rely on
reciprocity + social reputation. That works between real friends. It does
not work between V2EX strangers who joined an hour ago.

We solve this by separating "friend tier" from "incentive type" and giving
the user three explicit modes to pick from, layered on top of the existing
`permissions.py` 3-tier consent model.

## Three-track incentive model

| Track | Who it's for | Lender gets | Borrower pays | §4.10 compatible? |
|---|---|---|---|---|
| **Gift** (default for `strong-tie-auto`) | Real friends, family, close colleagues | Reputation +20 if balanced, social gratitude | Zero direct cost. Public on-chain ledger entry. | ✅ — no value transferred |
| **Kudos** (default for `per-request`) | Acquaintances, V2EX-recruited testers | Kudos counter `+amount` per token lent | Kudos counter `-amount` per token borrowed. **Kudos cannot be sold, exchanged for fiat, or withdrawn.** Decays at 5% / month if unused (prevents hoarding). | ✅ — kudos is a reciprocity counter, not a security |
| **Stablecoin micropay** (default for `emergency-only` & cross-strangers) | Strangers; emergencies; "I need 100k tokens right now" | Direct USDT-TRC20 payment to lender's wallet | Borrower pays lender ~$0.01 / 1000 tokens (configurable per lender). **sisoul takes 0% — no fee, no rake, no escrow.** | ✅ — USDT is issued by Tether, not by sisoul; sisoul is just routing the request. We never touch the money. |

### Why "gift / kudos / micropay" instead of "free / token / cash"

- **Gift** is what already works between close friends. Don't break it.
- **Kudos** solves the V2EX-stranger problem without minting a token. It is
  a per-user counter stored in `~/.sisoul/kudos.db`, signed by the lender,
  attested on-chain (EAS). It cannot be transferred to a third party. It
  is not a security. It is closer to a Stack Overflow rep score than to a
  cryptocurrency.
- **Stablecoin** is the escape hatch when kudos isn't enough — emergencies
  and one-off requests from strangers who don't want to bootstrap a
  reciprocity relationship. We don't get to control the money, which is
  the point.

### Why sisoul does not take a cut

§4.10 is the load-bearing principle. The moment sisoul takes 1% of any
transaction, sisoul has a token-like incentive, and the protocol's purpose
shifts from "serve the user" to "extract rent". This is the failure mode of
Compound / Uniswap / MakerDAO governance capture, in slow motion. We
intentionally exclude ourselves from the value flow.

How we pay rent instead: `docs/GOVERNANCE.md §Funding` — Optimism RetroPGF,
Gitcoin Grants, EF Grant, direct sponsorship, individual donations to the
maintainer's USDT-TRC20 wallet (`TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn`).

## How a lender decides which track to enable

A lender's per-friend permission in `~/.sisoul/permissions/<did>.yaml`:

```yaml
friend_did: did:key:z6Mk...
relationship_tier: strong-tie         # strong-tie / acquaintance / stranger
llm_quota_share:
  monthly_token_cap: 100_000           # hard cap
  rate_limit: 10                       # N requests / minute
  models: []                           # empty = all; or e.g. [claude-opus-4-7]
  incentive_mode: gift                 # gift / kudos / micropay
  # for kudos:
  kudos_required_per_1k_tokens: 1.0    # ignored if incentive_mode != kudos
  # for micropay:
  usdt_per_1k_tokens: 0.01             # ignored if incentive_mode != micropay
  usdt_payout_address: T...            # ignored if incentive_mode != micropay
consent_mode: strong-tie-auto          # strong-tie-auto / per-request / emergency-only
```

Defaults applied by `sisoul friend add`:

| Relationship tier (Alice picks at add time) | Default `incentive_mode` | Default `consent_mode` |
|---|---|---|
| strong-tie (`--tier close`) | gift | strong-tie-auto |
| acquaintance (`--tier known`) | kudos | per-request |
| stranger (`--tier stranger`) | micropay | emergency-only |

## How a borrower sees it

`sisoul borrow run did:key:bob skill_llm 5000 --prompt "..."` looks at Bob's
permission file, sees `incentive_mode: kudos, kudos_required_per_1k_tokens: 1.0`,
and shows:

```
Borrowing 5000 tokens of claude-opus-4-7 from @bob
  Cost: 5 kudos (you have 12)
  Lender consent: per-request (Bob will get a PWA notification)
  Continue? [y/N]
```

If `incentive_mode: micropay`:

```
Borrowing 5000 tokens of claude-opus-4-7 from @bob
  Cost: 0.05 USDT-TRC20 to TXxxx...xxx
  Network fee: ~1 TRX (~$0.20) — paid by you
  Lender consent: emergency-only (Bob's daemon will auto-approve emergencies)
  Wallet balance: 12.34 USDT  Continue? [y/N]
```

## Reputation grade is a separate layer

Reputation grade A/B/C/D in `anti_abuse.py:compute_reputation` is computed
from:

- abuse incidents (-20 each)
- spam complaints (-10 each)
- imbalance ratio (-15 if > 2:1 or < 0.5:1)
- sustained reciprocity (+20 if 10+ interactions in [0.66, 1.5] ratio)

This grade is published on-chain via EAS `REPUTATION_PUBLISH`. It influences
trust, not access — a lender can still say no to an A-grade stranger, and a
borrower can still pay an F-grade lender in USDT. Grade is signal, not gate.

## Implementation status (2026-06-06)

| Component | Status | Notes |
|---|---|---|
| `permissions.py` schema with monthly_token_cap, rate_limit, models | ✅ done | 550 LOC |
| 3-tier consent (auto / per-request / emergency) | ✅ done | tested in M4 |
| ReciprocityLedger SQLite | ✅ done | 633 LOC |
| `compute_reputation` algorithm + EAS publish | ✅ done | Sepolia testnet only |
| **`incentive_mode: gift`** | ✅ done | this is just the current behaviour |
| **`incentive_mode: kudos`** | ❌ design only | need: `kudos.db`, `kudos.transfer()`, decay job, PWA UI |
| **`incentive_mode: micropay`** | ❌ design only | need: USDT-TRC20 watcher (read-only), lender wallet field in permission, signed receipt before borrow, refund-on-failure |
| Decay job for kudos | ❌ not started | nightly LaunchAgent / systemd timer, 5% / month |
| PWA: lender sees "Bob borrowed 5k tokens, you earned 5 kudos" | ❌ not started | UI lives in `pwa/src/routes/borrow/` |
| Stranger micropay end-to-end test | ❌ not started | needs 2 testnet TRX wallets |

## V2EX-readiness checklist

Before posting to V2EX, the following pieces of this doc need to land in
the codebase:

- [x] Doc itself (this file)
- [x] Reference from `README.md` and `GOVERNANCE.md`
- [x] Reference from `docs/V2EX-LAUNCH-POST.md`
- [ ] `incentive_mode: kudos` MVP (no UI, CLI only)
- [ ] `incentive_mode: micropay` MVP (manual USDT, no automated watcher)
- [ ] Updated `sisoul friend add --tier` flag

The honest V2EX position: **today, only `gift` is implemented.** Kudos and
micropay are the next two PRs. Strangers borrowing from strangers does not
yet work end-to-end — it's planned, designed, and partially implemented.
Promise this in the post, don't pretend it already ships.

## What I deliberately did not propose

- **Sisoul-issued token**: violates §4.10.
- **Sisoul-operated escrow**: introduces a custody role we don't want.
- **Auto-converted credits**: any 1:1 conversion between kudos and money
  makes kudos a security under most jurisdictions.
- **Optimism / Base L2 native gas payment**: gas costs at borrow-time would
  be ~$0.01 per request, ten times the token cost. TRC20 is dirt-cheap for
  the same role.
- **Lightning Network**: complexity vs benefit doesn't justify it at alpha.
  Re-evaluate at v2.0.
