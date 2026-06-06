# CI/CD Workflows

> All 3 GitHub Actions workflows explained: when they fire, what they do, how to debug.

## 1. `ci.yml` — Continuous Integration

**Triggers**: every push to `main` / `beta-*` / `alpha`, every PR to `main`

**Jobs** (run in parallel):

### `python` (Python matrix 3.11 + 3.12)
```yaml
- pip install -e .
- pip install pytest pytest-asyncio pytest-cov
- pytest tests -q --tb=line --ignore=tests/test_v1_integration_full_user_journey.py
- pytest tests/test_alpha_launch_e2e.py -v
- pytest tests/test_v2_foundation.py -v
```

**Expected**: 2075+ passed / 0 fail.

**Common failures**:
- Import error → missing dep, check `pyproject.toml`
- Skip count drift → fixture changed, check `tests/conftest.py`
- Test timeout → check `tests/test_alpha_daemon_smoke.py` daemon startup

### `pwa` (PWA build)
```yaml
- cd pwa
- npm ci
- npm run build
```

**Expected**: `pwa/dist/` generated with 15+ JS chunks.

### `shellcheck`
```yaml
- shellcheck -S warning ops/install.sh ops/init/install-autostart.sh
```

**Expected**: 0 warnings.

---

## 2. `release.yml` — Auto Release

**Triggers**:
- push to tag `v*` (e.g. `git tag v1.0.0-alpha && git push origin v1.0.0-alpha`)
- manual via Actions → "Release" → Run workflow

**Steps**:

```yaml
1. checkout main
2. Build wheel + sdist (python -m build)
3. Install cosign (sigstore/cosign-installer@v3)
4. Sign artifacts (cosign sign-blob --yes --output-signature ... --output-certificate ...)
5. Tar release files (install.sh + dist/* + sigs + certs)
6. Create GitHub Release (softprops/action-gh-release@v2)
   - body from RELEASE-NOTES-v1.0-alpha.md
   - prerelease = true if tag has "alpha" or "beta"
   - upload dist/*, install.sh, sisoul-*.tar.gz
```

**Permissions required**: `contents: write` (create release) + `id-token: write` (sigstore OIDC)

**Verify release after**:
```bash
gh release view v1.0.0-alpha --json assets,body,prerelease
cosign verify-blob --certificate <cert> --signature <sig> <asset>
```

---

## 3. `deploy-gh-pages.yml` — PWA auto deploy

**Triggers**: push to `main` (any file change)

**Steps**:

```yaml
1. setup-node@v4 (node 20, npm cache)
2. cd pwa
3. npm ci
4. npm run build
5. peaceiris/actions-gh-pages@v3 deploy to gh-pages branch
```

**Result**: PWA available at `https://akige.github.io/sisoul/`.

**First-time setup**: Settings → Pages → Source = "GitHub Actions" (not branch deploy)

---

## How to debug failed workflow

1. Open Actions tab on GitHub
2. Click red ✗ run
3. Expand failing job
4. Search for `ERROR` or `FAIL`

Common issues:

| Error | Cause | Fix |
|---|---|---|
| `cosign: command not found` | sigstore installer missed | Verify `sigstore/cosign-installer@v3` is in release.yml |
| `pytest: not found` | dep missing in workflow | Add to `pip install pytest pytest-asyncio` |
| `npm run build: missing index.html` | PWA build cache stale | Clear `pwa/node_modules`, rerun |
| PR check times out (>10 min) | Slow daemon fixture in tests | Profile `tests/test_alpha_daemon_smoke.py` startup time |
| `GH_TOKEN: bad credentials` | wrong secret name | Use `${{ secrets.GITHUB_TOKEN }}` (auto-provisioned) |

---

## Local equivalents

Before push, run locally:

```bash
make release-check    # pytest + build + pwa + shellcheck + self-check
```

This mirrors what CI runs, exit 0 = green CI.

---

## Adding a new workflow

1. Create `.github/workflows/your-name.yml`
2. Trigger on relevant events
3. Reference Python/Node setup actions
4. Test by pushing to a test branch
5. Document in this file
