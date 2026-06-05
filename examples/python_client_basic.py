#!/usr/bin/env python3
"""sisoul Python client basic example.

Expected output:
  daemon health: ok
  Added case: case-abc...
  Found N case(s) matching 'rust'
  Attestation UID: mock:...

Usage: python python_client_basic.py
Requires: sisoul daemon running on 127.0.0.1:9876
"""
import httpx

BASE = "http://127.0.0.1:9876"


def main() -> None:
    # 1. Health check
    health = httpx.get(f"{BASE}/sisoul/health", timeout=5).json()
    print(f"daemon health: {health['status']}")
    print(f"version: {health['version']}\n")

    # 2. Add a case
    r = httpx.post(f"{BASE}/v2/case", json={
        "question": "How to use Rust async tokio::select?",
        "answer": "Use unwrap_or_else and proper cancellation",
        "did_author": "did:key:z6MkExample",
        "tags": ["rust", "async"],
    }, timeout=5).json()
    case_id = r["id"]
    print(f"Added case: {case_id}")

    # 3. Search
    s = httpx.get(f"{BASE}/v2/case/search/?q=rust", timeout=5).json()
    print(f"Found {len(s['cases'])} case(s) matching 'rust'")
    for c in s["cases"][:3]:
        print(f"  - {c['id']}: {c['question'][:50]}")
    print()

    # 4. Attest provenance (cite Alice's case)
    a = httpx.post(f"{BASE}/v2/provenance/attest", json={
        "response_id": "example-resp-1",
        "query": "rust async",
        "answer": "answer text",
        "did_answerer": "did:key:z6MkExample",
        "cited_cases": [{"source_id": case_id, "did_author": "did:key:z6MkExample"}],
        "network": "mock",
    }, timeout=5).json()
    print(f"Attestation UID: {a['attestation_uid']}")
    print(f"Citation count: {a['citation_count']}")


if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError:
        print("ERROR: daemon not reachable at", BASE)
        print("  Start: sisoul daemon --background")
        raise SystemExit(1)
