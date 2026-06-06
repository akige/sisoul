# sisoul Governance (non-token, contribution-weighted)

> Per whitepaper §4.10: **sisoul will not issue a token of any kind**. No ICO, no
> IDO, no airdrop, no governance token, no fee token, no points-then-token.
> Funding comes from grants, sponsorship, and donations. This document explains
> how decisions are made anyway.

## Why no token

Token-based DAOs (Compound, Uniswap, MakerDAO) all converge to the same failure
mode: large holders dominate vote outcomes, and the Foundation aligns its
incentives with token holders rather than with users. sisoul targets the opposite
position — a protocol for users, fundable by grants, with governance decided by
verified contribution.

Reference precedents for non-token governance:

| Project | Lifespan | Model | Funding |
|---|---|---|---|
| **Tor** | 22 years | Tor Project (501c3) + active-relay weighted council | Grants (US Naval / OTF / private donors) |
| **Mozilla / Firefox** | 25 years | Mozilla Foundation + technical contributor merit | Search-engine revenue share + grants |
| **IETF** | 40 years | RFC process — rough consensus + running code | Sponsorship + ISOC |
| **Apache Software Foundation** | 25 years | Project Management Committees, voted in by merit | Donations + sponsorship |
| **Linux kernel** | 33 years | Linus + subsystem maintainers, signed-off-by chain | Maintainer salaries from companies (Red Hat / Intel / Google) |

sisoul governance copies the IETF + Linux kernel hybrid: PIP process for
protocol-spec changes (RFC-style), maintainer merit for reference-impl PRs.

## Three layers of decisions

### Layer 1 — Protocol Specification (decentralized)

The wire format, the DID method, the chat protocol, the EAS schema — these
constitute the **sisoul protocol spec** (`docs/PROTOCOL.md`). Anyone can:

- Write a third-party implementation (Rust, Go, Swift, anything that speaks the
  wire format).
- Propose changes via **PIP** (sisoul Improvement Proposal, RFC-style, modeled
  after BIP / EIP / RIP).

PIP process:

1. Draft `pip-NNNN-title.md` in `pip/` directory of the spec repo.
2. Open a PR. Maintainers + interested implementers review.
3. Rough consensus (no formal vote) — if no sustained objection from any
   implementer for 30 days, PIP moves to `Last Call`.
4. Last Call period (14 days) — final review.
5. PIP merged to `Final`. Spec bumped, implementers expected to update.

No token-weighted vote. Implementers (Rust impl, Go impl, this repo) have equal
say. End users participate by raising objections during Last Call.

### Layer 2 — Reference Implementation (akige/sisoul, this repo)

This GitHub repo is the **reference implementation**. Maintainership lives here:

- **Initial maintainer**: akige (project founder).
- **Path to commit-bit**: contribute meaningful PRs (5+ merged), get nominated
  by an existing maintainer, no sustained objection from any maintainer in 14
  days → granted.
- **PR review**: 1 maintainer approval required for non-trivial changes; 2 for
  protocol-affecting or security-affecting changes.

PR sources:

- **Human contributor**: fork repo, open PR, signed-off-by line per Linux DCO.
- **RSI-suggested patch**: a user's local sisoul daemon, while running RSI,
  may generate a prompt or code improvement. The user can choose to lift that
  suggestion into a PR (manually, with attribution). RSI agents themselves
  cannot push directly — human review is mandatory.

Maintainers may delegate review to long-time contributors but the responsibility
for the merge decision remains with maintainers.

### Layer 3 — Each User's Local Daemon (fully decentralized)

Every sisoul daemon is autonomous on the user's machine. RSI (Recursive
Self-Improvement) lets the daemon evolve its own system prompt, retrieved skills,
and configuration, without anyone else's permission. The safety boundary:

- RSI only mutates **prompts and configs**, never `src/sisoul/`.
- RSI runs in `dry_run=true` mode by default; user must opt in to live mutation.
- `SELF_PATH_GUARD = "src/sisoul/v3/rsi/"` — Gödel guard, the RSI module cannot
  modify itself.
- pytest gate — any RSI-proposed change must pass the existing test suite.

A user's RSI changes never propagate to anyone else automatically. To share an
RSI-discovered improvement, the user lifts it into Layer 2 (PR) or Layer 1
(PIP if it's protocol-affecting).

## Decision matrix

| Decision type | Layer | How decided | Who decides |
|---|---|---|---|
| Wire format change | 1 | PIP rough consensus | Implementers + open community |
| New CLI command | 2 | PR | Reference-impl maintainers |
| Daemon route addition | 2 | PR | Reference-impl maintainers |
| Documentation fix | 2 | PR | Any maintainer (single approver OK) |
| Security fix | 2 | PR + 2-approval | Two maintainers, expedited |
| `system_prompt` for this user's daemon | 3 | RSI loop or manual edit | The user, alone |
| Skill marketplace listing | 3 (per-user) → 1 (registry schema) | Self-publish; schema via PIP | Each publisher |
| Token launch / airdrop | — | **Not on table** (§4.10) | N/A |

## Alpha-era contribution recognition (Soulbound Badge)

sisoul has no token to give early users, but it does have an immutable on-chain
record of who showed up early. Each `did:key` that satisfies a contribution
threshold during the alpha period receives a non-transferable Soulbound Badge
(SBT), minted on Optimism L2 by the maintainer:

- **`SisoulSBT.sol`** (ERC-721, transfer-disabled).
- 0 economic value. Cannot be sold, traded, or used as collateral.
- Records the cohort label (`alpha-2026`, `beta-2026`, …) and a contribution
  signature (number of cases / friends / chat sessions during the period).
- Serves as: portfolio evidence, conference speaker bio, recognition.

Contribution thresholds (per alpha cohort `alpha-2026`):

| Tier | Requirements |
|---|---|
| Alpha Tester | install + 7 days of active daemon uptime + at least 1 friend handshake |
| Alpha Builder | submit a merged PR / a published skill / a translated docs page |
| Alpha Sentinel | report a confirmed security issue OR triage 5 issues |

SBT does **not** confer governance vote, token allocation, or any future
economic benefit. It is a record of presence, nothing more. Per §4.10, this is a
hard constraint.

## Funding (per §4.10)

| Source | Status | Notes |
|---|---|---|
| Optimism RetroPGF | Will apply at v1.0 stable (T+6m) | sisoul receives OP tokens (not SIS) which fund maintainer time |
| Gitcoin Grants | Will apply at beta (T+3m) | Quadratic funding round, small donations matched |
| Ethereum Foundation | Eligible for Privacy + Communication tracks | Apply at v2.0 (MLS ship) |
| Direct sponsorship | Currently open | Contact: see SECURITY.md · USDT (TRC20) `TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn` |
| Individual donations | Currently open | USDT (TRC20): `TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn` · transparent on-chain · more channels coming |

### Donation address (transparent, on-chain verifiable)

- **USDT (TRC20)**: `TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn`
  - Network: TRON (TRC-20)
  - Verify on chain: https://tronscan.org/#/address/TNesE1mJZ11ogsrPC7tsG2he7UJ7iKSmKn
  - Maintainer-controlled. All inflows accounted for in the alpha quarterly
    Foundation report (starts T+3m, post-Stiftung incorporation).

**If grants + sponsorship + donations cannot sustain sisoul, the protocol does
not "need" a token to survive — it needs to build more value.**

## Foundation (post-alpha)

After alpha (T+1m), a non-profit entity will be incorporated:

- **Form**: Stiftung (Swiss foundation, like Ethereum Foundation) or 501(c)(3)
  (US, like Tor Project / Mozilla Foundation) — to be decided based on legal
  consultation.
- **Mission**: maintain protocol spec, reference implementation, and dispute
  resolution. Hold trademark.
- **Cannot**: turn off any daemon, recover any user key, censor any chat,
  control any token (because there is none).
- **Funding flow**: grants + sponsorship → Foundation operating budget → core
  maintainer stipends + infrastructure (bootstrap nodes, CI, security audits).

## "Never-shutdown" companion principle

§4.10 also encodes "never-shutdown": if the Foundation is dissolved tomorrow,
every existing sisoul daemon continues to function. The protocol is a
specification, not a service. This is a structural protection against
vendor-death = memory-death.

## How this differs from "we'll figure out token later"

We will not. The decision is permanent. Anyone proposing a token in a future
PIP is requesting a fundamental change to §4.10, which would require:

1. A formal PIP modifying §4.10.
2. Rough consensus from all current implementers.
3. Last Call period of 90 days (longer than normal, because the change is
   foundational).
4. Public objection from any one Implementer or Foundation board member is a
   veto. (Normal PIPs need rough consensus, not unanimity, but §4.10 is
   different.)

This veto is the only known mechanism to prevent governance capture in
practice. It is the same protection IETF uses for its core principles.

## Open questions

- How to handle disagreement between maintainers when a maintainer leaves?
  Probably similar to Linux subsystem maintainer succession.
- What if a Foundation board member becomes unreachable? Multisig threshold +
  predefined recovery process. Spec at v2.

---

🤖 Governance lives by use. The procedures above are version 1.
Improvements: open a PIP.
