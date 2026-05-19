---
pip: 3
title: Meta-Layer Hook Specification for AI Tool Sync
author: Sisoul Working Group
status: Draft
type: Standards Track
category: Interface
created: 2026-05-19
requires: PIP-001
replaces: (none)
discussions-to: https://github.com/sisoul/pips/discussions/3
---

# PIP-003: Meta-Layer Hook Specification

## Abstract

This PIP specifies how the per-device Sisoul daemon injects soul state
into, and harvests new soul state out of, third-party AI tools — without
replacing any of them. The daemon defines three hook points
(`pre-prompt`, `post-response`, `cross-tool-sync`) and a single
managed-section convention based on HTML-comment markers that lets the
daemon edit configuration files in-place without disturbing other
content the user or the tool has written.

In v1 the protocol concretely covers five surfaces:

1. **Claude Code** (CLI) — `~/.claude/CLAUDE.md` plus per-project
   `CLAUDE.md` files plus the daemon's HTTP context-injection hook.
2. **Codex CLI / OpenAI Codex** — `~/.codex/AGENTS.md` and the
   `~/AGENTS.md` cwd-fallback.
3. **Cursor IDE** — `.cursor/rules/sisoul.mdc` per workspace.
4. **OpenCode CLI** — `~/.opencode/AGENTS.md`.
5. **Custom HTTP** — any tool that speaks the Sisoul daemon's local
   REST API (`/v1/context/get`, `/v1/context/write`).

The protocol is designed so that a sixth and seventh tool can be added
by writing one Python module against `src/sisoul/sync/base.py` without
touching the daemon core.

Reference implementation: `src/sisoul/sync/` and the PWA route
`src/sisoul/daemon_routes/pwa.py`.

## Motivation

Sisoul's central thesis: every popular AI tool already has its own
notion of "the user told me to remember this", and the user has been
forced to repeat themselves to each one. The vault (PIP-001) gives us
a canonical place to store that information; the missing piece is a
disciplined way to *project* that information back into each tool's
native configuration surface, and to harvest any new information the
user added directly inside a tool back into the vault.

We deliberately do **not** replace these tools. They are excellent at
what they do; their CLIs, prompts, and IDE integrations are mature.
Sisoul is the meta-layer that makes them coherent.

Design goals:

* **Non-destructive.** Sisoul edits a clearly delimited slice of each
  config file. The user's own hand-written content outside that slice
  MUST never be touched.
* **Tool-agnostic.** New tools join by implementing a small adapter,
  not by changing the protocol.
* **Idempotent.** Running sync twice produces the same file state as
  running it once.
* **Crash-safe.** A partially-completed sync MUST leave each config
  file in a valid state from each tool's parser perspective.
* **Round-trip-faithful.** Content harvested from a tool, written
  into the vault, and re-emitted into the tool's config MUST be
  byte-identical for content that did not change semantically.
* **Auditable.** The user can inspect, at any time, exactly what
  bytes the daemon will write before it writes them
  (`sisoul sync --dry-run`).

Non-goals:

* Real-time bidirectional streaming (sync is event-driven and batched).
* Conflict resolution across two users editing the same managed
  section simultaneously (not a meaningful scenario; one user, one
  soul).
* Schema translation for tool-specific advanced features (e.g.,
  Cursor's MDC frontmatter); each adapter is responsible for its own
  surface.

## Specification

### 1. Hook points

The daemon exposes exactly three hook points. Tools or wrappers MAY
invoke them; the daemon also invokes them itself on filesystem events.

#### 1.1 `pre-prompt`

Triggered immediately before an AI tool is about to construct the
prompt it will send to its model.

Inputs (from caller):

```
{
  "tool": "claude-code",
  "context_hint": "user is in <repo-root>",
  "prompt_text_so_far": "<optional, up to 4 KiB>"
}
```

Outputs (from daemon):

```
{
  "managed_blocks": [
    {
      "target_path": "<claude-config-dir>/CLAUDE.md",
      "start_marker": "<!-- sisoul-managed-start -->",
      "end_marker":   "<!-- sisoul-managed-end -->",
      "content":      "...rendered Markdown...",
      "checksum":     "blake2b-256:..."
    }
  ],
  "ephemeral_context": "...one-shot text to be prepended to the prompt...",
  "ttl_ms": 60000
}
```

The "managed_blocks" entries are written to disk by the daemon during
this same call (atomic per file, see §3.4). The "ephemeral_context" is
returned to the caller as a string; the caller decides where to inject
it (typically as a system-prompt prefix). The TTL is advisory: the
caller MAY cache the response, but MUST refresh after `ttl_ms`.

The daemon SHALL fulfill a `pre-prompt` call in ≤ 50 ms on warm cache,
≤ 250 ms on cold cache (PBKDF2 already paid at unlock).

#### 1.2 `post-response`

Triggered after a model response has been shown to the user. The
caller passes a structured payload describing what happened:

```
{
  "tool": "claude-code",
  "session_id": "2026-05-19-abc",
  "turn_index": 7,
  "user_text": "...",
  "assistant_text": "...",
  "tool_calls": [ ... ],
  "annotations": {
    "user_marked_remember": ["I prefer pytest over unittest"],
    "user_marked_forget":   []
  }
}
```

The daemon's behavior:

1. Append a compact summary entry to `chat-history.enc` (PIP-001 §5.4).
2. For each `user_marked_remember` item, merge it into either
   `preferences.enc` or `goals.enc` according to a small classifier
   (`src/sisoul/sync/classifier.py`); the classifier is deterministic
   and rule-based, NOT an LLM call, so this hook stays offline.
3. For each `user_marked_forget`, remove matching entries.
4. Schedule a deferred `cross-tool-sync` (see §1.3) so other tools
   pick up the change at their next opportunity.

The hook MUST return within 100 ms; classifier and disk writes happen
synchronously, but the deferred cross-tool sync is non-blocking.

#### 1.3 `cross-tool-sync`

Triggered (a) at daemon startup; (b) on any vault mutation; (c) on
filesystem change to any managed config file (inotify / FSEvents /
ReadDirectoryChangesW); (d) on explicit `sisoul sync` invocation.

The hook walks every registered tool adapter and:

1. Computes the desired managed-section content from current vault
   state.
2. Reads the target config file.
3. Extracts the existing managed section bytes between markers.
4. If extracted content differs from desired content:
   - If the extracted content's checksum matches the last-known
     desired checksum (stored in
     `~/.sisoul/state/sync-<tool>.json`), this is a vault→tool
     update: write the new desired content using §3.4 atomic write.
   - Otherwise, the user (or the tool itself) modified the managed
     section between syncs. This is the "harvest" path: parse the
     modifications back into vault deltas, write them via PIP-001,
     and then re-emit the canonical content. If parsing fails, the
     daemon SHALL NOT overwrite the user's content; it SHALL log a
     `SyncConflictError` and leave the file alone until manual
     `sisoul sync --resolve <tool>`.

This is the "managed section" semantic: the daemon owns those bytes,
but if the user grabs them anyway and writes something coherent the
daemon understands, the daemon will absorb it rather than fight.

### 2. Managed-section markers

The canonical marker syntax is:

```
<!-- sisoul-managed-start v1 soul=<short-id> -->
... content ...
<!-- sisoul-managed-end -->
```

The opening marker carries:

* `v1` — managed-section format version.
* `soul=<first-8-chars-of-base58-soul_id>` — identifier so a user
  with multiple souls can tell which one wrote this block.

The content between markers SHALL be valid Markdown by default. For
file formats that do not accept HTML comments (e.g. JSON-based
configs, plain `.env`), each adapter SHALL define an equivalent
comment-marker pair using the host format's comment syntax (e.g.
`# sisoul-managed-start v1 soul=... #` for shell config).

A file MAY contain at most one managed section per `(soul_id, role)`
pair. The `role` is the adapter-specific purpose (e.g.
`global-rules`, `project-rules`). If multiple sections are needed,
each one's opening marker carries an additional `role=<name>` field:

```
<!-- sisoul-managed-start v1 soul=5DGmf2yz role=project -->
```

Markers SHALL appear on their own line. The daemon parses with a
strict regex against full lines; it does not attempt to recover from
inline markers or alternate comment styles within the same surface.

### 3. Adapters

#### 3.1 Adapter interface

```python
# src/sisoul/sync/base.py
class SyncAdapter(Protocol):
    name: str                                  # "claude-code" etc.
    config_files: list[ConfigTarget]           # one or more files

    def render(self, soul_state: SoulState) -> dict[str, ManagedBlock]:
        """Return desired managed-section content per target."""

    def harvest(self, target: ConfigTarget, raw_section: str) -> Delta:
        """Parse a user-modified section back into a vault delta."""
```

`SoulState` is a read-only snapshot of relevant vault frontmatter and
body fragments. `Delta` is the JSON-Patch-like vault mutation
described in PIP-001 implicitly through `vault.merge` calls.

#### 3.2 Adapter: Claude Code

Targets:

* `~/.claude/CLAUDE.md` — global, always written, contains
  preferences + active goals + identity bootstrap (soul_id).
* `<project>/CLAUDE.md` — written per project when the daemon
  observes a Claude Code session running in that working tree.

Managed section structure (rendered):

```
<!-- sisoul-managed-start v1 soul=5DGmf2yz role=global -->
## Sisoul-managed identity & preferences

soul_id: did:sisoul:5DGmf2yz...

### Preferences
- reply_language: zh-CN
- units: metric
- timezone: Asia/Shanghai
- preferred_editor: nvim

### Active goals (top 3)
1. ship sisoul v1.0 public — P0, deadline 2026-06-30
2. write 4 PIPs — P0, child of #1
3. ...

<!-- sisoul-managed-end -->
```

The adapter additionally consumes Claude Code's `UserPromptSubmit`
hook (a feature of Claude Code itself) so that the daemon's
`pre-prompt` is called at the right moment; this wiring is documented
in `qa/sync/claude-code-wire.md`.

Harvest path: if the user adds a free-text line inside the managed
section starting with `pref:` or `goal:`, the harvester recognizes
those shorthands and merges them. Any line not matching a recognized
shorthand is preserved verbatim on next render (as a "user-pinned"
sub-block within the managed section, between
`<!-- pinned-start -->` and `<!-- pinned-end -->` inner markers).

#### 3.3 Adapter: Codex CLI

Targets:

* `~/.codex/AGENTS.md` — long-form, recommended location.
* `~/AGENTS.md` — fallback for invocations from `cwd=$HOME`.

Both files receive identical managed sections. The Codex CLI does not
have a `UserPromptSubmit` hook, so `pre-prompt` here means "the
daemon's filesystem watcher noticed the user opened a Codex session"
(detected via process scan + open-file scan), at which point the
daemon re-renders. This is best-effort; if missed, Codex picks up the
content next time it reads its AGENTS.md, which is at session start.

#### 3.4 Adapter: Cursor IDE

Targets:

* `<workspace>/.cursor/rules/sisoul.mdc` — one file per workspace
  that the daemon observes opened by Cursor.

MDC frontmatter:

```mdc
---
description: Sisoul-managed soul context
globs: ["**/*"]
alwaysApply: true
---
<!-- sisoul-managed-start v1 soul=... role=cursor-workspace -->
... content ...
<!-- sisoul-managed-end -->
```

Cursor honors MDC files automatically; no further wiring needed.

#### 3.5 Adapter: OpenCode CLI

Targets: `~/.opencode/AGENTS.md`. Identical content to the Codex
adapter; OpenCode uses the same convention.

#### 3.6 Adapter: Custom HTTP

Any tool MAY consume the Sisoul daemon directly via:

```
GET  http://127.0.0.1:8788/v1/context/get?tool=<name>
POST http://127.0.0.1:8788/v1/context/feedback  (body: post-response payload)
```

The response from `/v1/context/get` is the same `ManagedBlock`
structure as §1.1, suitable for any tool to render into its own
prompt. Authentication is via a per-tool token issued by
`sisoul tool grant <name>` and stored in
`~/.sisoul/state/tool-tokens.json`.

The daemon binds 127.0.0.1 only by default; remote access requires
the operator to manually edit `~/.sisoul/daemon.toml` and is out of
scope for v1 conformance.

### 4. Atomic file edits

Every managed-section write SHALL use the same atomic-rename pattern
as PIP-001 §6:

```
1. Read target file fully.
2. Locate <!-- sisoul-managed-start v1 soul=... [role=...] -->
   and matching <!-- sisoul-managed-end -->.
3. If markers not found: append a new managed section at end of file,
   preceded by exactly one blank line.
4. Substitute the new content (between markers).
5. Write to <target>.tmp.<pid>.<rand>; fsync; close.
6. rename(tmp, target); fsync(dirname(target)).
```

If step 2 finds an unbalanced or duplicate marker, the daemon SHALL
NOT write; instead it SHALL emit a `SyncMarkerError` and leave the
file unmodified. The user resolves manually.

If the target file does not exist, the daemon creates it with
permissions `0644` (or `0600` if the parent directory is `0700`),
containing only the managed section.

### 5. State files

Per-tool sync state lives in `~/.sisoul/state/sync-<tool>.json`:

```json
{
  "tool": "claude-code",
  "last_rendered_at": "2026-05-19T07:21:00Z",
  "last_rendered_checksum": "blake2b-256:...",
  "targets": {
    "<claude-config-dir>/CLAUDE.md": {
      "size": 12345,
      "mtime_ns": 1716000000000000000,
      "last_known_section_checksum": "blake2b-256:..."
    }
  }
}
```

These files are *plaintext*. They contain no soul secrets — only file
paths, sizes, mtimes, and checksums of public content. Their purpose
is to detect "did the user (or another process) edit the managed
section behind our back" without re-reading every file at every tick.

### 6. Concurrency and ordering

* The daemon serializes all sync operations through a single asyncio
  task per tool. Cross-tool ordering: vault writes complete before
  any sync runs (PIP-001 §6 → notify → sync queue).
* If two vault writes happen within 50 ms, the second supersedes the
  first; only one sync per tool runs per quiet window. The quiet
  window defaults to 50 ms (configurable in `daemon.toml`).
* A `sisoul sync --force` bypasses the quiet window and runs all
  adapters immediately.

### 7. Multi-device

The sync hook in this PIP is **device-local**: it projects the local
vault into local tool config files. Cross-device replication of the
*vault itself* is handled by an out-of-band sync layer that operates
on the encrypted vault bytes (typically rsync over Tailscale or a
cloud-blob mirror). The daemon's filesystem watcher fires
`cross-tool-sync` automatically when the vault changes, regardless of
whether the change originated locally or arrived from a peer.

A future PIP may specify a CRDT or operational-transform layer for
the vault. v1 ships with last-writer-wins per class, with a 1 s clock
skew tolerance, and a `sisoul sync diff` command that surfaces
conflicting writes for manual reconciliation.

### 8. Dry-run and audit

`sisoul sync --dry-run` SHALL print, for each tool:

* The exact file paths it would write.
* The exact bytes it would write (the desired managed-section body).
* Any harvest deltas it would apply to the vault.

It MUST NOT touch any file or vault state in dry-run mode. This is
how a user verifies what the daemon is about to do before granting
permission to a new tool adapter.

### 9. Telemetry

Sisoul's reference daemon emits zero telemetry over the network by
default. Local-only metrics (Prometheus textfile under
`~/.sisoul/state/metrics.prom`) include:

* `sisoul_sync_runs_total{tool}`
* `sisoul_sync_harvest_events_total{tool,class}`
* `sisoul_sync_errors_total{tool,type}`

Operators MAY opt into shipping metrics to their own monitoring stack
via the standard Prometheus scrape endpoint at `127.0.0.1:8788/metrics`
(disabled by default; enable in `daemon.toml`).

## Rationale

### Why three hook points and not more?

We considered finer-grained hooks (`on-model-stream-chunk`,
`on-tool-call-pending`, etc.) and rejected them: they would push the
daemon into hot paths where its latency budget would be measured in
milliseconds and where tool-specific quirks would dominate. The
chosen three sit at the natural punctuation marks of a conversation
("about to talk", "just finished talking", "background reconciliation")
and are sufficient to express every observed sync use-case in our
internal beta.

### Why managed sections instead of full-file ownership?

Two reasons. First, every one of the five target surfaces *already*
has the user putting hand-written content in the same file (Claude
CLAUDE.md, Codex AGENTS.md, Cursor rules); taking over the whole file
would either delete that content or require a complex back-merge
dance. Second, the managed-section pattern is the de-facto Unix
convention (think `/etc/hosts` and Tailscale, `~/.ssh/authorized_keys`
and any provisioning tool); users already understand it.

### Why HTML-comment marker syntax?

Because all five target surfaces are Markdown or Markdown-like, and
HTML comments are the one syntax all Markdown parsers ignore in their
output. The same marker is also a valid Markdown comment, a valid
HTML comment, and parses cleanly with a one-line regex across all
languages. We considered fenced code blocks with a special info
string, but those render visibly in some viewers and would mislead the
user about whether the daemon-managed bytes are "code".

### Why classifier as code, not LLM call?

Two reasons. First, the daemon must work offline. Second, an LLM
classifier introduces non-determinism into a path where the user
expects "what they marked, the daemon stored". A small rule-based
classifier covers the observed shorthand patterns
(`pref:`, `goal:`, `forget:`, `friend:`, `skill:`) and an unknown
shorthand falls through to a user-pinned block (§3.2) where it is
preserved verbatim.

### Why TTL on `pre-prompt`?

Because some tools may aggressively cache the daemon's response
(e.g., Claude Code's `UserPromptSubmit` hook fires on every prompt;
the daemon doesn't want to re-render frontmatter at 60 Hz). The TTL
hint lets the caller cache for up to a minute but no longer; vault
mutations within that minute will not be reflected, which is
acceptable since the user just made them and is unlikely to expect
them re-injected into the same prompt they're writing.

### Why one adapter per tool instead of a generic adapter?

Because each tool's config file has its own conventions (Cursor's MDC
frontmatter, Codex's `~/AGENTS.md` cwd fallback, Claude's
per-project hierarchy) and a generic adapter would either miss those
conventions or grow more knobs than three concrete adapters together.
The adapter interface is small enough (`render` + `harvest`) that
adding a sixth adapter is a 100-line module.

## Backwards Compatibility

Internal-1.0 (the pre-PIP private build) used a single monolithic
`sisoul-rules` block injected via `~/.claude/sisoul.local.md`. A
one-shot migrator translates that into the canonical managed-section
within `~/.claude/CLAUDE.md` and removes the orphan file. Users who
have not run the migrator will see two blocks for one cycle; the
second sync removes the duplicate after detecting both checksums.

## Test Cases

### TV-1: Marker round-trip

Given a `~/.claude/CLAUDE.md` containing:

```
# User's own notes

Some hand-written content.

<!-- sisoul-managed-start v1 soul=5DGmf2yz role=global -->
old content
<!-- sisoul-managed-end -->

More hand-written content.
```

After `sisoul sync` with new desired content `"new content"`, the
file MUST contain exactly:

```
# User's own notes

Some hand-written content.

<!-- sisoul-managed-start v1 soul=5DGmf2yz role=global -->
new content
<!-- sisoul-managed-end -->

More hand-written content.
```

Bytes outside the markers MUST be byte-identical to the input,
including trailing newlines.

### TV-2: Missing markers

If the target file does not contain markers, `sisoul sync` SHALL
append the managed section after one blank line at end of file,
preserving any existing trailing newline.

### TV-3: Unbalanced markers

If the file contains only `<!-- sisoul-managed-start ... -->` with no
matching end, the daemon MUST NOT write; it MUST log `SyncMarkerError`
with the file path and continue with other tools.

### TV-4: Harvest of user-added preference

User adds the line `pref: ui_dense=false` inside the managed section.
After `sisoul sync`:

1. `preferences.enc` MUST contain `ui_dense: false`.
2. The managed section MUST be re-rendered without the `pref:` line
   (the preference moved into vault).
3. The blake2b-256 of the new section MUST be recorded in
   `~/.sisoul/state/sync-claude-code.json`.

### TV-5: Cross-tool propagation

Given vault `preferences.reply_language = "zh-CN"`, after
`sisoul sync` all five target files SHALL show that preference inside
their managed section within 200 ms wall time on the reference
hardware.

### TV-6: Crash mid-write

A `SIGKILL` between the tempfile write and the rename MUST leave the
target file unchanged from its pre-sync state. A stale `<file>.tmp.*`
MAY remain; the daemon's startup pass garbage-collects any matching
its expected naming pattern older than 5 minutes.

### TV-7: Dry-run isolation

`sisoul sync --dry-run` MUST NOT touch any file (verified by mtime
comparison) nor any vault byte (verified by manifest mtime).

### TV-8: HTTP adapter authentication

A request to `GET /v1/context/get?tool=foo` without a valid token
MUST return HTTP 401. A request with a valid token MUST return a
`ManagedBlock` JSON object as in §1.1.

### TV-9: Pinned sub-block preservation

A pinned block:

```
<!-- pinned-start -->
This is the user's hand-written context they want preserved.
<!-- pinned-end -->
```

inside the managed section MUST survive verbatim across all
subsequent syncs, even when the surrounding daemon-rendered content
changes.

### TV-10: Quiet-window debounce

Two `vault.merge` calls within 50 ms MUST result in exactly one sync
run per tool, with the final desired content reflecting both merges.

## Reference Implementation

```
src/sisoul/sync/
├── __init__.py
├── base.py              # SyncAdapter Protocol, ManagedBlock, ConfigTarget
├── managed_section.py   # parse / emit marker pairs, atomic write
├── claude_code.py       # adapter
├── codex.py             # adapter
├── cursor.py            # adapter
├── opencode.py          # adapter
├── aider.py             # adapter (experimental, not v1-mandated)
└── classifier.py        # shorthand → vault class router

src/sisoul/daemon_routes/
└── pwa.py               # /v1/context/get, /v1/context/feedback
                        # + the PWA's HTTP surface for the web UI
```

The reference daemon is a single asyncio event loop. Each adapter's
`render` and `harvest` are pure functions of `SoulState`, which makes
them trivial to unit test in isolation. Integration tests under
`tests/sync/` spin up a sandbox `$HOME` with all five target files
present and exercise the full hook lifecycle.

## Security Considerations

### Confidentiality of projected content

Managed sections are written to *plaintext files on the user's local
disk*. By design, this is the same trust boundary the AI tools
already require: Claude Code reads `CLAUDE.md` in cleartext; Cursor
reads `.cursor/rules/*.mdc` in cleartext; etc. The daemon does not
escalate the disclosure risk beyond what the tool already implies.

However, several real-world risks remain:

* **Cloud-synced workspace** (e.g. user's project lives in iCloud
  Drive): the managed section will be replicated to that cloud. The
  daemon SHALL emit a warning the first time it writes to a path
  that contains a known cloud-sync root (`~/Library/CloudStorage/`,
  `~/Dropbox/`, `~/OneDrive/`, etc.), and recommend the user move
  the project or accept the disclosure.
* **Shared dotfiles repo**: users who symlink `~/.claude/CLAUDE.md`
  into a public dotfiles git repo would publish the managed section.
  The daemon SHALL detect symlinks pointing outside `$HOME` and warn.
* **Process-list disclosure**: nothing here puts secrets on argv.

### What never goes into managed sections

* The mnemonic. Ever.
* Vault sub-keys. Ever.
* Private keys (curve25519 / Ed25519) from any other layer. Ever.
* Friend-only data (the contents of `friends.enc` body) by default;
  only the user's own `did:sisoul:...` is projected, not those of
  friends, unless the user explicitly opts in per friend in the UI.
* Chat-history transcripts; only counts and summaries.

### Hostile tool

A misbehaving AI tool might overwrite its config file
arbitrarily (the daemon and the tool both have write permission).
The daemon's response:

1. Detect on next sync via checksum mismatch.
2. Attempt to harvest any recognizable shorthand the user might have
   added; preserve unrecognized content verbatim in a pinned block.
3. Re-emit the desired section; the tool's other content survives.

The daemon does NOT attempt to lock-out the tool from its own config
file: doing so would either fail (the tool runs as the user) or break
the tool's normal operation.

### Hostile harvest payload

`harvest` parses user-provided content. To resist injection (e.g.
`goal: do_something; rm -rf /`), each adapter's harvester MUST:

* Treat all parsed values as opaque strings (no shell, no eval).
* Enforce a per-field max length (preferences: 256 bytes; goals: 4
  KiB; skills: 1 KiB; friends: must be a valid `did:sisoul:...`).
* Reject control characters except `\n` and `\t`.
* Reject any YAML construct that requires `!!python/object` or any
  custom tag.

The reference implementation uses `yaml.safe_load` everywhere and
the test suite includes a corpus of YAML-injection payloads
(`tests/sync/yaml-injection/`).

### Token theft (HTTP adapter)

The custom HTTP adapter's per-tool token grants read+write access to
the daemon's `/v1/context/*` endpoints. The daemon SHALL:

* Bind 127.0.0.1 only by default.
* Store tokens with `0600` permissions.
* Rotate tokens on `sisoul tool revoke <name>` and refuse the old
  token immediately.
* Refuse tokens older than 90 days (forces re-grant).

A token does NOT grant access to the raw vault; it grants only the
documented projection surface.

### Defense in depth

* The daemon refuses to write a managed section larger than 64 KiB
  (hard cap) to prevent runaway adapters from filling the config
  file with gigabytes of generated content.
* Filesystem watcher events are rate-limited to 10 Hz per file; a
  busy editor's autosave does not DoS the daemon.
* Every adapter's `render` output is validated to start with the
  expected open marker and end with the expected close marker before
  writing; this catches the bug class where an adapter accidentally
  returns content that lacks markers.

## Appendix A: Worked example, end-to-end

```
T+0    User edits ~/.claude/CLAUDE.md to add inside managed section:
         pref: timezone=Europe/Berlin

T+200ms FSEvents fires; daemon's sync loop wakes.

T+202ms claude-code adapter computes:
         - section checksum has changed since last render
         - parse shorthand → delta {preferences.timezone: "Europe/Berlin"}

T+204ms daemon writes vault preferences.enc (PIP-001 §6 atomic).

T+205ms cross-tool-sync fires for all five tools.

T+206ms codex adapter: ~/.codex/AGENTS.md updated.
        cursor adapter: workspace .cursor/rules/sisoul.mdc updated.
        opencode adapter: ~/.opencode/AGENTS.md updated.
        custom HTTP adapter: pushes to subscribed clients.

T+210ms claude-code adapter re-renders its own section (removing the
        pref: shorthand line; replacing with the canonical structured
        form). Bytes outside the markers untouched.

T+210ms All sync state files updated with new checksums.
```

Total observed latency on reference hardware: 10–15 ms once warm.

## Appendix B: Adapter authoring guide

To add a new adapter named `foo`:

1. Create `src/sisoul/sync/foo.py` implementing `SyncAdapter`.
2. Register it in `src/sisoul/sync/__init__.py` REGISTRY.
3. Add integration tests under `tests/sync/test_foo.py` that:
   - Use a tmp `$HOME` fixture.
   - Pre-populate the target file with a known content.
   - Run a vault mutation and assert the marker round-trips per TV-1.
4. Document the tool's quirks in `qa/sync/<foo>-wire.md`.

The reference repo's `aider.py` adapter (experimental) is the
recommended template; it is ~120 lines including tests.

## Appendix C: Why PWA route lives in `daemon_routes/pwa.py`

The Sisoul daemon ships with a small Progressive Web App at
`http://127.0.0.1:8788/` that provides a GUI for vault inspection,
sync state, friend management, and skill borrow flows. The PWA shares
the same HTTP server as the `/v1/context/*` endpoints of this PIP;
they live together in `src/sisoul/daemon_routes/pwa.py` because both
are surface-level concerns and share the per-tool token model.

The PWA is **not** part of the meta-layer protocol; it is a
reference UI. Future implementations MAY ship without it (the daemon
is fully usable from CLI).

## Appendix D: Token grant flow

```
$ sisoul tool grant my-custom-tool
A new token has been generated for "my-custom-tool".

Token: stk_5f3a...c91d_2026-08-17    (90-day TTL)

Export it where the tool can read it. Recommended:
  export SISOUL_TOOL_TOKEN=stk_5f3a...c91d_2026-08-17

The tool may now call:
  GET  http://127.0.0.1:8788/v1/context/get?tool=my-custom-tool
  POST http://127.0.0.1:8788/v1/context/feedback

Revoke at any time with:
  sisoul tool revoke my-custom-tool
$
```

The token format is `stk_<32-char-base58>_<exp-yyyy-mm-dd>`. The
expiration is encoded in the token itself but ALSO stored in
`~/.sisoul/state/tool-tokens.json` for server-side enforcement; an
attacker who edits the token cannot extend its TTL because the daemon
trusts only its own state file.

## Copyright Waiver

Copyright and related rights for content in this document are waived
via [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/).
