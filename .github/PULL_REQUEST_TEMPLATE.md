<!--- Thanks for contributing! Read CONTRIBUTING.md first. -->

## Summary

(1-2 sentence: what + why)

## Type

- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change (requires version bump)
- [ ] Docs
- [ ] CI / tooling
- [ ] Refactor (no functional change)

## Roadmap target

(which phase does this land in? alpha / beta / v1.0 stable / v2.0 / v3.0)

## Test plan

- [ ] `.venv/bin/pytest tests -q --tb=line --ignore=tests/test_v1_integration_full_user_journey.py` — all pass
- [ ] `cd pwa && npm run build` — succeeds
- [ ] Manual test: ...

## Checklist

- [ ] Code follows project style (`ruff format`, `mypy` clean)
- [ ] New code has tests (foundation tests OK for skeletons)
- [ ] No new shellcheck warnings (`shellcheck -S warning ops/*.sh`)
- [ ] No new commits with `--no-verify` (hooks bypassed)
- [ ] Co-authored-by tag if AI-assisted: `Co-Authored-By: ... <noreply@anthropic.com>`
- [ ] CHANGELOG.md updated (if user-visible)
- [ ] README.md / docs updated (if needed)

## Screenshots / demo

(if UI change)

## Related issues

Closes #...
