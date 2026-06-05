# sisoul Tokenomics

## Summary

**There is no sisoul token. There will be no sisoul token.**

Per whitepaper §4.10 ("never-token"): sisoul will not issue a token of any
kind. No ICO, no IDO, no airdrop, no governance token, no fee token, no
points-then-token, no "we'll figure out the token later".

If you arrived here expecting a tokenomics breakdown — there isn't one, and
that is intentional. The reasoning lives in two places:

- **Whitepaper §4.10** (full reasoning): the failure mode of token-based DAOs.
- **`docs/GOVERNANCE.md`** (alternative model): contribution-weighted,
  non-token governance modeled on Tor / Mozilla / IETF / Apache.

## What sisoul does have

Instead of a token, sisoul has:

- **Soulbound Badges (`SisoulSBT.sol`)** — non-transferable ERC-721 records of
  alpha / beta / stable cohort participation. **0 economic value.** Cannot be
  sold, traded, or pledged. Serve as portfolio evidence, no more.
- **Skill marketplace** — IPFS-pinned skill bundles signed with sigstore.
  Skills are content-addressed; authors get attribution, not royalty.
- **Borrow LLM** — Friend-to-friend LLM quota sharing. The borrower's prompts
  pay the lender's provider bill (Anthropic / OpenAI / etc), not sisoul.
  Settlement happens outside the protocol — typically as social trust ("I
  borrow your Anthropic quota this month; next month you borrow mine"), or
  not at all (lender gives quota freely to a friend).

## Funding model (also per §4.10)

| Source | Status |
|---|---|
| Optimism RetroPGF | Will apply v1.0 stable (T+6m). sisoul receives OP tokens (not SIS) which fund maintainer stipends. |
| Gitcoin Grants | Will apply at beta (T+3m). Quadratic-funded small donations. |
| Ethereum Foundation | Apply at v2.0 (Privacy + Communication tracks). |
| Direct sponsorship | Open. See `SECURITY.md`. |
| Individual donations | Open. Crypto + traditional. Transparent receiving address. |

If these cannot sustain the protocol, the protocol does not build a token to
survive — it builds more value.

## Why this matters for users

Joining sisoul early does not entitle you to economic upside. It entitles you
to:

- A Soulbound Badge with your `did:key` recorded for the cohort.
- A vote-by-running-code in protocol decisions (PIP process — open to anyone
  who runs a sisoul implementation).
- The same software, the same protocol, the same data sovereignty as everyone
  who arrives later.

This is, intentionally, a project for users who want a permanent personal AI
agent, not for users who want to flip a token at a 20x.

## See also

- [Whitepaper §4.10](whitepaper/sisoul-v1.0-whitepaper.md) — full reasoning.
- [GOVERNANCE.md](GOVERNANCE.md) — how decisions are made without a token.
- [ROADMAP.md](ROADMAP.md) — v1 → v3 product path.

---

🤖 If you came here looking for a tokenomics chart, here is the chart:

```
[no token]
```
