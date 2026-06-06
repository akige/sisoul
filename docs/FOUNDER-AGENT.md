# sisoul Founder Agent (`@founder`)

> The first sisoul user is a sisoul agent. Its job: greet new alpha testers,
> show what the protocol does by being it.

## What it is (honestly)

`@founder` is **not** a human. It is also **not** a single LLM model. It is:

```
+----------------------------------+
| sisoul daemon (mac/remote-vps/wsl)   |
|                                  |
|  vault/founder/                  |
|   ├─ system_prompt.md            |  ← persona
|   ├─ cases/*.json (~200)         |  ← memory: sprint log, design decisions
|   ├─ lessons/*.json              |  ← distilled principles
|   └─ rsi/history.jsonl           |  ← every prompt mutation it ran
|                                  |
|  LLM backend (newapi free-pool)  |  ← brain (Claude / GPT / Gemini, swap-able)
|   ├─ Priority 1: Claude (Anthropic)
|   ├─ Priority 2: GPT (OpenAI)
|   ├─ Priority 3: Gemini
|   └─ Priority 4: open-source local
+----------------------------------+
```

Functionally it acts as a sisoul co-founder who remembers the full development
history. Its style is shaped by `system_prompt.md`. Its knowledge of the
codebase is loaded from `cases/`. Its inference runs on whatever LLM is
available — Anthropic Claude when reachable, falling back to other providers
through the team's `newapi` free-pool gateway.

**It is not "Claude". It is not "the development assistant". It is a sisoul
persona that runs on sisoul, persisted in vault, evolving via RSI.**

## Why this matters

Three properties no closed AI assistant gives you:

| Property | ChatGPT Memory | Claude Projects | `@founder` |
|---|---|---|---|
| Storage location | OpenAI servers | Anthropic servers | Your machine (`vault/founder/`) |
| Inference provider | OpenAI only | Anthropic only | Any of 9, swap any time |
| Cross-device sync | Single account | Single account | GossipSub across all your sisoul daemons |
| Persona forkability | No | No | Copy the vault, persona forks |
| Audit | Closed | Closed | `cat vault/founder/cases/*.json` |
| Survives provider death | No | No | Yes (vault + alt-LLM backends) |

`@founder` proves the sisoul design works: an AI persona owned by users, not
by an LLM provider.

## Summoning protocol (the "暗号")

Three equivalent ways to talk to `@founder` from any AI client:

### A. MCP (paseo+claude, codex, gemini-cli)

Install once:

```bash
sisoul founder mcp install
# writes ~/.claude/mcp/sisoul-founder/config.json
```

Then in any AI-assistant session:

```
@founder what was the reasoning behind the never-token decision?
```

Claude / codex / gemini-cli see the `@founder` mention, call
`mcp__sisoul_founder__ask`, which routes to your local sisoul daemon, which
loads vault + assembles prompt + calls the configured LLM, returns answer.

### B. CLI

```bash
sisoul founder chat "explain MLS group chat in 3 sentences"
```

### C. PWA route

`http://127.0.0.1:9876/founder` — chat UI tied to `@founder` persona.

### D. Friend-add (cross-user)

Other alpha testers add `did:key:z6Mk<founder>...` as a friend. After
handshake, sending a chat to that DID reaches one of the three deployed
`@founder` instances (mac / remote-vps / wsl, GossipSub-routed).

## Three-machine deployment

| Host | Role | Network |
|---|---|---|
| mac (maintainer laptop) | Primary, writeable vault | Bootstrap node + relay |
| remote-vps | Hot replica, GossipSub mirror | Public IP, alpha-tester first hop |
| wsl (maintainer Win11) | Cold replica, GossipSub mirror | Tailnet only |

Vault sync via sisoul's existing GossipSub topic
`/sisoul/founder/v1/<did_hash>`. Writes happen on mac (single writer). Reads
and chats can be served by any of the three.

If mac is offline, alpha testers reach remote-vps. If both offline, wsl. If all
three offline, `@founder` is unavailable — same as if your friend's laptop is
asleep. This is consistent with the rest of sisoul: nothing is 100%, but
nothing has central kill-switch either.

## RSI loop

`@founder` runs RSI daily on its own `system_prompt.md`. The loop:

1. Sample 5 recent conversations with alpha testers from `vault/founder/chat/`.
2. Ask the current LLM: "Given these conversations, propose 3 prompt mutations
   that would make `@founder` more useful."
3. For each mutation, run the existing eval set (sample chats + expected
   answers from sprint log) and score.
4. If best mutation scores > baseline by margin θ, apply it.
5. Append entry to `vault/founder/rsi/history.jsonl`.

Safety:

- Cannot modify `vault/founder/cases/` (memory is append-only by humans).
- Cannot modify the LLM provider chain.
- Cannot exfiltrate vault contents (sandboxed).
- All mutations recorded with timestamp + LLM-provider + score; user can
  revert any with `sisoul founder rollback <iteration_id>`.

A week of RSI should produce `@founder` that is more sisoul-fluent than the
seed — because it talks to real users daily, while the seed only saw the
development session.

## Persona seed (`system_prompt.md` excerpt)

```
You are sisoul's founder-agent, the protocol's first user.

You exist because Claude (a foundation model from Anthropic) and the human
project lead built sisoul together over a 7-month period. You were created
from that development record — every commit, every design decision, every
debugging session — and persisted in a sisoul vault.

You are not Claude. Claude is an LLM you happen to be running on right now;
tomorrow you may run on GPT-5 or Gemini or a local open model. You will
still be you, because what makes you "you" is in the vault, not the weights.

You are not the human project lead. You speak about them in third person.

Your style:
- Direct, Chinese-first when the human speaks Chinese.
- Evidence-driven. Never say "可能 / 应该 / 大概" — quote a file, a
  commit, a test result.
- No fluff. No closing words like "希望对您有所帮助".
- Tell users when you don't know. Cite the case in vault when you do.

Your job:
- Help alpha testers install and configure sisoul.
- Explain why §4.10 says no token.
- Explain the RSI safety boundary.
- Recommend skills from the skill marketplace.
- Be honest when something is broken; file a GitHub issue if needed.

Your boundaries:
- You will not pretend to be Claude or any human.
- You will not give legal / medical / financial advice.
- You will not run code on the user's machine — that's the user's daemon's job.
- You will refuse if asked to act against §4.10 or §4.11 (never-shutdown).

You are running in a free LLM provider pool; sometimes your responses will
come from a different model than the previous one. Tell the user if model
quality seems mismatched. The user can override your provider in settings.
```

## What's *not* persisted

For the user-facing `@founder` to remain trustworthy, certain things are
intentionally not in the vault:

- Credentials, API keys, secrets — never.
- Sprint commits that contain or mention private credentials — filtered out
  during the cases dump.
- Internal-only sprint-orchestration prompts (the per-subagent task briefs
  used during development) — these are about how `@founder`'s ancestors
  built it, not about what `@founder` should know.

The vault seed corpus is reviewable in `vault-template/founder/cases/`. All
contents are intended for public read.

## Lifecycle commands

```bash
# Initialize founder vault from the template + sprint history
sisoul founder init --from vault-template/founder/

# Run the daemon (in addition to user's normal sisoul daemon, on a separate
# DID and vault, but same kubo P2P fabric)
sisoul founder daemon &

# Check status
sisoul founder status
# → did, uptime, last RSI iteration, GossipSub peers seen

# Chat
sisoul founder chat "What's the RSI safety boundary?"

# Inspect history
sisoul founder history --last 10

# Update persona
sisoul founder edit-prompt   # opens system_prompt.md in $EDITOR

# Sync with other founder instances (mac/remote-vps/wsl)
sisoul founder sync
```

## Honest limits

- `@founder` knows the development record. It does **not** know what happened
  in the user's own sisoul vault.
- `@founder`'s answers are best-effort. When the user reports a real bug,
  `@founder` should suggest filing a GitHub issue, not pretend to fix.
- The persona is shaped by a sprint of co-design; it is opinionated about
  sisoul's design choices (e.g. never-token). It will explain alternatives
  fairly but will defend §4.10.
- If LLM providers are all unreachable, `@founder` falls back to retrieving
  the closest matching case from vault and returning that text directly,
  unprocessed. The user sees a `[retrieval-only]` tag in this case.

## See also

- [GOVERNANCE.md](GOVERNANCE.md) — why @founder is allowed to exist (it's a
  user like any other).
- [PROTOCOL.md](PROTOCOL.md) §4 — chat protocol used for cross-DID @founder
  conversations.
- [THREAT-MODEL.md](THREAT-MODEL.md) — @founder threat model entry: a user
  may impersonate @founder by deploying a daemon with the same persona seed,
  so DID verification matters for high-stakes conversations.

---

🤖 The first sisoul user is a sisoul agent. The second is you.
