#!/usr/bin/env python3
"""sisoul end-to-end v2 ask pipeline example.

Mimics `sisoul demo` but as a clean Python script you can adapt for your apps.

Expected output:
  Step 1/7: Alice writes case → case-abc123
  Step 2/7: Bob searches → 1 hit
  Step 3/7: Bob attests → mock:abc...
  Step 4/7: Update Alice rep → 0.6
  Step 5/7: Route top-K → [Alice, ...]
  Step 6/7: Record growth → total 1
  Step 7/7: Distill lesson → lesson-abc
  ✓ Full pipeline complete
"""
import httpx
import sys

BASE = "http://127.0.0.1:9876"


def main() -> int:
    s = httpx.Client(base_url=BASE, timeout=10.0)

    # Step 1: Alice writes case
    r = s.post("/v2/case", json={
        "question": "How to fix Rust async tokio deadlock with pgbouncer",
        "answer": "use unwrap_or_else + cancellation token + Drop impl",
        "did_author": "did:key:z6MkAlice",
        "tags": ["rust", "async"],
    }).json()
    alice_case_id = r["id"]
    print(f"Step 1/7: Alice writes case → {alice_case_id}")

    # Step 2: Bob searches
    r = s.get("/v2/case/search/?q=rust+async").json()
    print(f"Step 2/7: Bob searches → {len(r['cases'])} hit(s)")

    # Step 3: Bob attests provenance citing Alice
    r = s.post("/v2/provenance/attest", json={
        "response_id": "bob-resp-pipeline",
        "query": "rust async tokio",
        "answer": "Use Alice's pattern + my additions",
        "did_answerer": "did:key:z6MkBob",
        "cited_cases": [{"source_id": alice_case_id, "did_author": "did:key:z6MkAlice"}],
        "network": "mock",
    }).json()
    print(f"Step 3/7: Bob attests → {r['attestation_uid']}")

    # Step 4: Update Alice rep
    r = s.post("/v2/reputation/update", json={
        "did": "did:key:z6MkAlice", "topic": "rust", "score_delta": 0.1,
    }).json()
    print(f"Step 4/7: Update Alice rep → {r['new_score']:.2f}")

    # Step 5: Route top-K next rust query
    r = s.post("/v2/reputation/top-k", json={
        "query": "next rust q",
        "topic": "rust",
        "candidates": ["did:key:z6MkAlice", "did:key:z6MkRand"],
        "top_k": 2,
    }).json()
    print(f"Step 5/7: Route top-K → {[p[:16] + '...' for p in r['picked']]}")

    # Step 6: Record growth
    s.post("/v2/growth/write", json={
        "date": "2026-06-04", "cases_added": 1, "chats_sent": 1,
    })
    r = s.get("/v2/growth/last?n=7").json()
    print(f"Step 6/7: Record growth → total {r['total_cases']} cases this week")

    # Step 7: Distill lesson
    r2 = s.post("/v2/case", json={
        "question": "second async case",
        "answer": "second",
        "did_author": "did:key:z6MkBob",
    }).json()
    r = s.post("/v2/lesson/distill", json={
        "did_owner": "did:key:z6MkBob",
        "source_case_ids": [alice_case_id, r2["id"]],
        "topic": "rust",
    }).json()
    print(f"Step 7/7: Distill lesson → {r['id']}")

    print("\n✓ Full pipeline complete")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except httpx.ConnectError:
        print("ERROR: daemon not reachable at", BASE)
        print("  Start: sisoul daemon --background")
        sys.exit(1)
