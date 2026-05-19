# Sisoul Meta-Layer — Obsidian Plugin

Surfaces your local **sisoul daemon** (FastAPI, `127.0.0.1:9876`, 68 endpoints) inside Obsidian:

- Bottom **status bar**: daemon connectivity · vault encryption · last sync.
- Left **ribbon panel**: current DID · friends list · skills.
- **Managed sections** inside any markdown note: auto-refreshing `<!-- sisoul-managed-start -->` blocks for identity / friends / skills / goals / preferences / sync.
- **Command palette**: `Sisoul: Sync now`, `Sisoul: Show DID`, `Sisoul: Toggle RAG inject`, plus per-note / vault-wide managed-block refresh.

## Installation

The plugin is desktop-only (uses Obsidian's `requestUrl` to call loopback HTTP).

```bash
# 1. build
cd ~/sisoul-dev/obsidian-plugin
npm install
npm run build           # emits ./main.js (~50 KB)

# 2. install into a vault
VAULT=~/Obsidian/MyVault
mkdir -p "$VAULT/.obsidian/plugins/sisoul"
cp manifest.json main.js styles.css "$VAULT/.obsidian/plugins/sisoul/"

# 3. enable
#    Open Obsidian → Settings → Community plugins → toggle "Sisoul Meta-Layer".
```

Make sure the sisoul daemon is running first:

```bash
curl -s http://127.0.0.1:9876/sisoul/health | jq
```

## Settings

| Setting | Default | Meaning |
|---|---|---|
| Daemon URL | `http://127.0.0.1:9876` | Endpoint of local sisoul daemon. |
| API token | _empty_ | Bearer token (only needed if daemon enables auth). |
| Auto-sync interval (min) | `15` | `0` disables. POSTs `/sisoul/sync` on this cadence. |
| Show vault status | `on` | Render the bottom status-bar widget. |
| Enable selective RAG | `off` | Annotates managed sections with retrieved sisoul context. |
| Auto-update managed blocks | `on` | Refresh `<!-- sisoul-managed-* -->` blocks after each sync. |

## Managed sections

Inside any note, drop a managed block. The plugin will keep its body in sync with the daemon. Content **above and below the markers is preserved exactly**.

````markdown
## My profile (auto)

<!-- sisoul-managed-start -->
<!-- sisoul:{"kind":"identity"} -->
**DID**: `did:key:z6Mkw…`
**Daemon**: ok · v0.4.2
**Vault**: encrypted

_synced 2026-05-18T10:22:11.034Z_
<!-- sisoul-managed-end -->

My free-form notes below the block survive every refresh.
````

Supported `kind` values: `identity` · `friends` · `skills` · `goals` · `preferences` · `sync`. Optional `"limit": N` caps the number of rows (default 10).

Trigger a refresh with the command palette: **Sisoul: Update managed blocks in current note** or **… across vault**.

## Screenshots

> Drop PNGs into `docs/screenshots/` and replace the placeholders.

1. `docs/screenshots/01-status-bar.png` — bottom status bar (`Sisoul · live · enc · synced 2m ago`).
2. `docs/screenshots/02-ribbon-panel.png` — left ribbon panel showing DID + friends + skills.
3. `docs/screenshots/03-managed-block.png` — managed section auto-rendering identity card.
4. `docs/screenshots/04-settings.png` — plugin settings tab.
5. `docs/screenshots/05-command-palette.png` — `Sisoul:` commands in the palette.

## Files

```
obsidian-plugin/
├── manifest.json
├── package.json
├── tsconfig.json
├── esbuild.config.mjs
├── styles.css
├── src/
│   ├── main.ts
│   ├── settings.ts
│   ├── daemon-client.ts
│   ├── status-bar.ts
│   ├── ribbon-view.ts
│   ├── managed-section.ts
│   ├── commands.ts
│   └── types.ts
└── README.md
```

## Troubleshooting

- **"Sisoul daemon unreachable"**: confirm `curl http://127.0.0.1:9876/sisoul/health` works, then re-open Obsidian.
- **401 / 403**: paste the token into *Settings → Sisoul → API token*.
- **5xx**: check `~/sisoul-dev/ops/logs/daemon.log` for stack traces.
- **Managed block disappeared**: the plugin only rewrites between the markers; user text above/below is preserved. If the block lost its `<!-- sisoul:{...} -->` meta line, edit it back manually (defaults to `identity`).
