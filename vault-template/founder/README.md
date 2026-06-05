# `vault-template/founder/` — Founder Agent vault seed

This directory is the initial contents of `~/.sisoul/founder/` when the
founder agent is first initialized via `sisoul founder init`.

It is NOT the live founder vault — that lives in the user's `~/.sisoul/`
directory after init. This is the **seed** distributed with the source repo.

## Contents

- `system_prompt.md` — founder agent persona seed (RSI mutates a copy in
  the live vault, leaving this seed untouched)
- `cases/*.json` — distilled sprint history, design decisions, and
  rationale; ~200 entries from obs §63 through §77 plus PR descriptions
- `lessons/*.json` — distilled principles ("don't reach for tokens just
  because Web3 expects them") from the sprint
- `eval_prompts.json` — RSI evaluator's test set: prompts an `@founder`
  should answer correctly to keep its mutation

## How to use

```bash
# As an end user wanting their own copy of @founder:
sisoul founder init --from $SISOUL_REPO/vault-template/founder/
```

This copies the seed into `~/.sisoul/founder/`, derives a fresh did:key
(distinct from the user's primary sisoul DID), and starts the daemon.

## How updates work

When the sisoul repo bumps the seed (more sprint history, refined persona),
existing founder daemons can pull deltas:

```bash
sisoul founder sync-seed
```

This appends new `cases/` and `lessons/` from the repo, but **does not
overwrite** the live `system_prompt.md` (which has been mutated by RSI).
The user can `sisoul founder reset-prompt` to overwrite if they want.

## Boundaries

- This seed is public. Anything you add to it becomes part of the
  founder-agent's distributed knowledge. Do not put credentials here.
- Sprint history that contains private credentials has been filtered out.
  See `cases/SCRUB-MANIFEST.md` for what was redacted and why.
