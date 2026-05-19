#!/usr/bin/env python3
"""End-to-end dry-run for the PWA → IPFS → ENS pipeline.

Simulates:
  1. enumerate ~/sisoul-dev/pwa/dist/ (real fs read, real byte count)
  2. mock Pinata upload → returns deterministic fake CID
  3. mock ENS setContenthash → no network, no signing

Prints a step-by-step "what would happen" report.

Usage:
    ./dry-run.py
    ./dry-run.py --dist ./some-build --name sisoul.eth
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def fake_cid(name: str, total_bytes: int) -> str:
    h = hashlib.sha256(f"{name}-{total_bytes}".encode()).hexdigest()[:46]
    return f"bafybei{h}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dist",
        type=Path,
        default=Path.home() / "sisoul-dev/pwa/dist",
    )
    ap.add_argument("--name", default="sisoul.eth")
    ap.add_argument("--pinata-name", default="sisoul-pwa-dryrun")
    ap.add_argument("--resolver", default="0x231b0Ee14048e9dCcD1d247744d114a4EB5E8E63")
    args = ap.parse_args()

    print("=" * 60)
    print("Sisoul PWA → IPFS → ENS Dry Run")
    print("=" * 60)
    print()

    # ─── Step 1: enumerate dist ──────────────────────────────────────────
    print("[1/3] Enumerate PWA build")
    if not args.dist.is_dir():
        print(f"    ✗ dist not found: {args.dist}")
        print("    → would fail. Run `cd ~/sisoul-dev/pwa && npm run build` first.")
        print()
        files = []
        total_bytes = 0
    else:
        files = [f for f in args.dist.rglob("*") if f.is_file()]
        total_bytes = sum(f.stat().st_size for f in files)
        print(f"    ✓ {len(files)} files, {total_bytes:,} bytes")
        # 头 5 个文件展示
        for f in sorted(files)[:5]:
            print(f"      - {f.relative_to(args.dist.parent)} ({f.stat().st_size:,} B)")
        if len(files) > 5:
            print(f"      ... and {len(files) - 5} more")
    print()

    # ─── Step 2: mock Pinata upload ──────────────────────────────────────
    print("[2/3] Mock Pinata upload")
    cid = fake_cid(args.pinata_name, total_bytes)
    print(f"    POST https://api.pinata.cloud/pinning/pinFileToIPFS")
    print(f"      Authorization: Bearer $PINATA_JWT")
    print(f"      pinataMetadata.name = {args.pinata_name}")
    print(f"      wrapWithDirectory = true, cidVersion = 1")
    print(f"      multipart files: {len(files)}")
    print(f"    ← (mock) IpfsHash: {cid}")
    print(f"            PinSize: {total_bytes}")
    print()

    # ─── Step 3: mock ENS setContenthash ─────────────────────────────────
    print("[3/3] Mock ENS setContenthash")
    contenthash = f"0xe301<MULTIHASH_OF:{cid}>"
    print(f"    Resolver: {args.resolver}")
    print(f"    Name:     {args.name}")
    print(f"    Method:   setContenthash(namehash, contenthash)")
    print(f"    Value:    {contenthash}")
    print(f"    Tx:       (would broadcast via $WEB3_RPC, signed with $ENS_OWNER_PK)")
    print(f"    ← (mock) tx_hash: 0xDRY_RUN_NO_TX_SENT")
    print()

    # ─── Summary ─────────────────────────────────────────────────────────
    summary = {
        "dist": str(args.dist),
        "files": len(files),
        "total_bytes": total_bytes,
        "pinata_name": args.pinata_name,
        "fake_cid": cid,
        "ens_name": args.name,
        "ens_resolver": args.resolver,
        "fake_contenthash": contenthash,
        "url_after_real_run": f"https://{args.name}.limo  /  ipfs://{cid}",
    }
    print("─" * 60)
    print("Summary (would-happen):")
    print(json.dumps(summary, indent=2))
    print()
    print("To run for real, set env:")
    print("  export PINATA_JWT=...")
    print("  export WEB3_RPC=https://eth.llamarpc.com")
    print("  export ENS_OWNER_PK=0x...")
    print("Then:")
    print(f"  CID=$(./upload-to-pinata.py --dist {args.dist} --name {args.pinata_name})")
    print(f"  ./set-ens-contenthash.py --name {args.name} --cid $CID")
    return 0


if __name__ == "__main__":
    sys.exit(main())
