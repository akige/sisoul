---
name: Bug report
about: Something broken in sisoul alpha
title: '[bug] '
labels: bug, alpha
assignees: ''
---

## What broke

A clear, short description.

## How to reproduce

Run these commands:
```bash
sisoul --version              # paste output
sisoul health                 # paste output
# steps to trigger:
1. ...
2. ...
3. (error appears here)
```

## What you expected

(e.g. "expected daemon to start, but it crashed")

## Environment

- OS: (Linux / macOS / WSL2 / Win11)
- Python: `python --version`
- sisoul: `sisoul --version`
- Install method: `install.sh` / `pip install -e .` / other

## Severity (your guess)

- [ ] P0 critical (crash, data loss, key exfil)
- [ ] P1 high (feature broken, can't recover without manual fix)
- [ ] P2 medium (workaround exists)
- [ ] P3 low (cosmetic, doc typo)

## Logs / screenshots

(paste daemon logs `~/.sisoul/logs/daemon.err.log` or terminal output)
