# Sisoul Release Tooling

Clean-room build + desensitization pipeline for publishing internal `~/sisoul-dev/`
to four public-facing GitHub repos.

## Files

| File | Purpose |
|------|---------|
| `clean-room-build.sh` | rsync → desensitize → git init → `gh repo create` |
| `desensitize.py` | Regex-based redaction + 2-pass verification + report |
| `desensitize-blacklist.yaml` | Patterns: usernames, paths, hosts, Tailscale IPs, API key prefixes |

## Repo Split

| Public repo | Sources (from `~/sisoul-dev/`) |
|-------------|--------------------------------|
| `sisoul/sisoul-cli`      | `src/` + `tests/` + `qa/` + `pyproject.toml` + `VERSION` |
| `sisoul/sisoul-protocol` | `contracts/` + `sdk/` + `pip/` |
| `sisoul/sisoul-pip`      | `docs/pip/` + `pip/` |
| `sisoul/sisoul-docs`     | `docs/whitepaper/` + `docs/*` (内部 README 已剔除) |

## Workflow

### 1. Dry run (inspect, no writes)

```bash
ops/release/clean-room-build.sh --dry-run
```

Outputs:
- `/tmp/sisoul-release-build/<repo>/.desensitize-report.txt` per repo — counts only

### 2. Real run (writes, git init, gh repo create)

```bash
ops/release/clean-room-build.sh
```

- Replaces matched strings in place inside `/tmp/sisoul-release-build/<repo>/`
- 2nd-pass verification fails (`exit 1`) if any pattern still has a hit
- Runs `git init` + initial commit per repo
- Runs `gh repo create sisoul/<repo> --private --source=.` per repo
- Does NOT push — last step is yours

### 3. Final push (manual)

```bash
cd /tmp/sisoul-release-build/sisoul-cli && git push -u origin main
cd /tmp/sisoul-release-build/sisoul-protocol && git push -u origin main
cd /tmp/sisoul-release-build/sisoul-pip && git push -u origin main
cd /tmp/sisoul-release-build/sisoul-docs && git push -u origin main
```

## Customizing the blacklist

`desensitize-blacklist.yaml` ships safe public-only patterns. To redact real names,
internal IPs, real API key prefixes, copy to `desensitize-blacklist.private.yaml`
(gitignored) and pass via env:

```bash
# Not yet supported in the script; for now edit blacklist directly or fork.
```

## Env vars

- `SISOUL_GH_ORG` — GitHub org (default `sisoul`)
- `SISOUL_BUILD_DIR` — build root (default `/tmp/sisoul-release-build`)

## Flags

| Flag | Effect |
|------|--------|
| `--dry-run` | Run desensitize in count-only mode; no git, no gh |
| `--skip-gh` | Skip `gh repo create` (use when offline / already exists) |

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All four repos built + desensitized + committed |
| 1 | Desensitize 2nd-pass verification found residual matches |
| 2 | Missing config / blacklist / `--root` not a directory |
