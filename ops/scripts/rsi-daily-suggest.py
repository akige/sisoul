#!/usr/bin/env python3
"""Daily RSI suggester — Path B (suggest, don't auto-apply).

Reads vault/founder/system_prompt.md, asks LLM for N mutation candidates,
writes them to vault/founder/rsi/candidates-<date>.json for human review.

The human can then `sisoul founder edit-prompt` to apply a candidate, or
ignore. This stays safe (no auto-write) while still letting RSI run daily.

Run via launchd (macOS) or systemd timer (Linux). See:
    ops/init/sisoul-rsi-daily.plist
    ops/init/sisoul-rsi-daily.service

Env:
    SISOUL_VAULT, SISOUL_NEWAPI_API_KEY, SISOUL_NEWAPI_BASE_URL,
    SISOUL_NEWAPI_MODEL (default copilot-gpt-4.1),
    SISOUL_RSI_N_CANDIDATES (default 3),
    SISOUL_RSI_REFLECTION (default: directness + zh-first + cite-sources).
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    vault_root = Path(os.environ.get("SISOUL_VAULT", "~/.sisoul")).expanduser()
    founder_dir = vault_root / "founder"
    seed_file = founder_dir / "system_prompt.md"
    if not seed_file.exists():
        print(f"[rsi-daily] no founder vault at {founder_dir}, skip")
        return 1

    n = int(os.environ.get("SISOUL_RSI_N_CANDIDATES", "3"))
    reflection = os.environ.get(
        "SISOUL_RSI_REFLECTION",
        "Optimize for: directness, Chinese-first when user types Chinese, cite source files.",
    )

    from sisoul.llm.newapi import NewapiAdapter
    from sisoul.v3.rsi.godel_agent import GodelAgent

    seed = seed_file.read_text()
    adapter = NewapiAdapter()  # reads SISOUL_NEWAPI_* from env

    agent = GodelAgent(daemon_ref=None, llm_adapter=adapter, seed_prompt=seed)

    t0 = time.time()
    candidates = agent.propose_prompt_mutation(reflection, n=n)
    elapsed = time.time() - t0

    out_dir = founder_dir / "rsi"
    out_dir.mkdir(parents=True, exist_ok=True)
    date = time.strftime("%Y-%m-%d", time.gmtime())
    out_file = out_dir / f"candidates-{date}.json"

    payload = {
        "date": date,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds": round(elapsed, 2),
        "seed_length": len(seed),
        "seed_sha256_prefix": __import__("hashlib").sha256(seed.encode()).hexdigest()[:16],
        "reflection": reflection,
        "provider": adapter.provider_name,
        "model": adapter.model,
        "n_requested": n,
        "n_returned": len(candidates),
        "candidates": [
            {
                "index": i,
                "length": len(c),
                "content": c,
            }
            for i, c in enumerate(candidates)
        ],
        "note": "These are SUGGESTIONS, not applied. To apply: cp this candidate's content to vault/founder/system_prompt.md (back up first).",
    }
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"[rsi-daily] wrote {len(candidates)} candidates to {out_file}")
    print(f"[rsi-daily] elapsed {elapsed:.1f}s, provider={adapter.provider_name}, model={adapter.model}")
    print(f"[rsi-daily] review with: sisoul founder review-rsi {date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
