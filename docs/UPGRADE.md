# sisoul Upgrade & Migration Guide

> Goal: the user types `sisoul update`, walks away for 30 seconds, and comes back
> to the new version running with all data intact.

## Status (v1.0.0-alpha)

| Component | Current upgrade path | Round 10 target |
|---|---|---|
| daemon (Python) | `pip install -U sisoul` manually | `sisoul update` one-liner, cosign-verified |
| PWA | Service Worker v1 (cache only) | SW v2 with `skipWaiting()` + reload toast |
| Mobile (Capacitor wrap) | App Store / Play Store update | `@capacitor/live-updates` for PWA layer OTA |
| Vault schema | `dna.json` has `schema_version` | `sisoul migrate` auto-runs registered scripts |

## Round 10 design

### 1. `sisoul update` CLI

```bash
sisoul update            # check, prompt, apply
sisoul update --check    # check only, exit 0 if up-to-date
sisoul update --yes      # apply without prompt
sisoul update --channel beta   # opt in to beta channel
```

Steps it executes:

1. Fetch latest version manifest from `https://api.github.com/repos/akige/sisoul/releases/latest`.
2. Compare with installed (`sisoul --version`).
3. If newer: download `.whl` from release assets.
4. Verify cosign signature against `cosign.pub` shipped with the existing install.
5. Install: `pip install --upgrade <downloaded.whl>` into the active venv.
6. Run `sisoul migrate` to apply any vault schema migrations.
7. Restart the daemon: stop → start (systemd / launchctl / manual).
8. Health check: `curl /sisoul/health` → 200 within 10 seconds, else rollback.

Failure modes & rollback:

- **Signature fails** — abort, never write.
- **pip install fails** — abort, no daemon restart.
- **daemon doesn't come up** — auto-rollback: `pip install --force-reinstall <previous.whl>`, restart, alert user.
- **Migration fails** — keep new code, do not start daemon, restore vault from auto-snapshot (`~/.sisoul/snapshots/pre-migration-<timestamp>.tar.zst`).

### 2. PWA Service Worker v2

Current `pwa/public/sw.js` caches assets. Round 10 enhancements:

```js
// On new SW install:
self.addEventListener('install', e => {
  self.skipWaiting();   // don't wait for tabs to close
});

// On activate:
self.addEventListener('activate', e => {
  e.waitUntil(clients.claim());
  // Notify all open tabs
  self.clients.matchAll().then(clients => {
    clients.forEach(c => c.postMessage({ type: 'sw-updated' }));
  });
});
```

PWA listens for `sw-updated` and renders a toast: "新版本已就绪 · 刷新" with a
button that reloads the active tab. No user data loss — vault is on the daemon,
PWA only renders.

### 3. Mobile (Capacitor) live updates

For the PWA-wrapped iOS / Android app (`mobile-pwa/`), shipping a new PWA
version normally requires App Store / Play Store re-submission. To avoid that
for non-native changes:

```bash
npm install @capacitor/live-updates
```

Workflow:

- New PWA build pushed to a CDN (or GitHub release asset).
- Mobile app checks for update on launch.
- If found, downloads + applies the new web bundle, prompts user to reload.
- Native code changes still require Store re-submission (Capacitor plugin
  updates, native crypto changes, etc).

OTA scope: only the SolidJS PWA layer. Anything in `ios/` / `android/` native
projects → Store re-submit.

### 4. Vault schema migrations

`~/.sisoul/dna.json` carries `schema_version` (currently `2`). When daemon
starts, it compares with the schema version it expects:

```
expected = SisoulVault.SCHEMA_VERSION   # currently 2
actual = read(dna.json).schema_version

if actual < expected:
    for migration in find_migrations(from=actual, to=expected):
        snapshot_vault()       # tar.zst into ~/.sisoul/snapshots/
        run_migration(migration)
        update_schema_version(actual + 1)
elif actual > expected:
    abort("vault is from a newer sisoul version; downgrade not supported. "
          "Restore snapshot or upgrade sisoul.")
```

Migrations live in `src/sisoul/vault/migrations/`. Each is named
`v<N>_to_v<N+1>_<description>.py`, exports an `apply(vault_dir)` function, is
idempotent, and creates a snapshot before running.

### 5. Config migrations

`~/.sisoul/config.yaml` is migrated similarly. New keys get defaults; removed
keys are warned about but not deleted (preserve user intent).

### 6. Compatibility commitments

- **v1.x patch releases** (`1.0.1` → `1.0.2`): no migrations, no API changes.
- **v1.x minor releases** (`1.0` → `1.1`): backward-compatible API, automatic
  vault migration if needed.
- **v2.x major releases** (`1.x` → `2.0`): may require manual config edits,
  migrations still automatic. Two months of v1.x security patches continue
  after v2.x release.

## CI/CD release flow (round 10)

```
git tag v1.x.y
  ↓
.github/workflows/release.yml
  ↓ build wheel + sdist (Python)
  ↓ sign with cosign (sigstore keyless)
  ↓ build PWA dist
  ↓ build Capacitor live-update bundle
  ↓ publish GitHub Release with assets:
      - sisoul-1.x.y-py3-none-any.whl
      - sisoul-1.x.y-py3-none-any.whl.sig
      - cosign.pub
      - pwa-1.x.y.tar.gz
      - live-update-bundle-1.x.y.zip
      - SHA256SUMS
  ↓ publish to PyPI: pypi.org/project/sisoul
  ↓ trigger PWA Pages deploy (github.io)
  ↓ trigger Capacitor live-update push to CDN
```

## What's NOT auto-upgraded

- **BIP-39 mnemonic** — never touched.
- **vault DID private key** — never touched.
- **Custom system prompts in `vault/prompts/`** — preserved, never overwritten.
- **Friend records** — preserved.
- **Case graph** — preserved (only added to, never deleted by migration).
- **Chat history** — preserved (encrypted, untouched).

The user's identity and memory survive every upgrade.

## Anti-patterns

- **No silent telemetry** — `sisoul update --check` does not report success.
- **No background auto-update** — only when user runs `sisoul update`.
- **No "we changed your settings"** — migrations log every change to
  `~/.sisoul/upgrade.log` with a `--review` flag.
- **No "you must accept new ToS"** — we have no ToS to update.

---

🤖 Upgrade is a feature. Forced upgrade is a bug.
