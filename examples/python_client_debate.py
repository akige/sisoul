#!/usr/bin/env python3
"""sisoul Multi-Agent Debate example.

Expected output:
  Debate query: How to fix Rust async tokio deadlock?
  Agents: 3 / Rounds: 9 / Confidence: 0.92
  Synthesized answer: [stub synthesized answer for: ...]
"""
import httpx

BASE = "http://127.0.0.1:9876"


def main() -> None:
    query = "How to fix Rust async tokio deadlock with pgbouncer?"
    agents = [
        {"did": "did:key:z6MkDBExpert", "petname": "Bob (DBA)", "topic_reputation": 0.92},
        {"did": "did:key:z6MkRustGuru", "petname": "Charlie (Rust)", "topic_reputation": 0.85},
        {"did": "did:key:z6MkSRE", "petname": "Dave (SRE)", "topic_reputation": 0.78},
    ]

    r = httpx.post(f"{BASE}/v2/debate/run", json={
        "query": query,
        "agents": agents,
        "n_rounds": 3,
    }, timeout=30).json()

    print(f"Debate query: {r['query']}")
    print(f"Agents: {len(r['agents'])} / Rounds: {r['n_rounds']} / Confidence: {r['final_confidence']:.2f}")
    print()
    for a in r["agents"]:
        marker = " ← synthesizer" if a.get("is_synthesizer") else ""
        print(f"  - {a.get('petname') or a['did'][:16]}{marker}")
    print()
    print(f"Synthesized answer: {r['final_answer']}")


if __name__ == "__main__":
    main()
