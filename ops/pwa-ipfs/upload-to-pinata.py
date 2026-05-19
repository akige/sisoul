#!/usr/bin/env python3
"""Upload ~/sisoul-dev/pwa/dist/ to IPFS via Pinata.

Env:
    PINATA_JWT - Pinata API JWT (https://app.pinata.cloud/keys)

Usage:
    ./upload-to-pinata.py --dist ~/sisoul-dev/pwa/dist --name sisoul-pwa-v0.1.0
    ./upload-to-pinata.py --dry-run

Returns CID on stdout (last line). Also writes ./pinata-upload.json with full response.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PINATA_PIN_DIR_URL = "https://api.pinata.cloud/pinning/pinFileToIPFS"


def upload(dist: Path, name: str, jwt: str, dry_run: bool) -> dict:
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    file_paths: list[Path] = []
    for f in sorted(dist.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(dist.parent)  # 含顶层目录名, Pinata 才能保 dir 结构
        file_paths.append(f)
        if not dry_run:
            files.append(
                ("file", (str(rel), f.read_bytes(), "application/octet-stream"))
            )

    print(f"[pinata] discovered {len(file_paths)} files under {dist}", file=sys.stderr)
    total_bytes = sum(f.stat().st_size for f in file_paths)
    print(f"[pinata] total bytes: {total_bytes}", file=sys.stderr)

    if dry_run:
        print("[pinata] DRY RUN — not uploading", file=sys.stderr)
        mock_cid = f"bafybeidryrun{hash(name) & 0xFFFFFFFF:08x}"
        return {
            "IpfsHash": mock_cid,
            "PinSize": total_bytes,
            "Timestamp": "DRY-RUN",
            "isDuplicate": False,
            "_files": len(file_paths),
        }

    try:
        import requests  # 只在真跑时 import, dry-run 不需要
    except ImportError:
        print("ERROR: requests required for live upload. pip install requests", file=sys.stderr)
        sys.exit(2)

    headers = {"Authorization": f"Bearer {jwt}"}
    metadata = {"name": name, "keyvalues": {"project": "sisoul"}}
    data = {
        "pinataMetadata": json.dumps(metadata),
        "pinataOptions": json.dumps({"wrapWithDirectory": True, "cidVersion": 1}),
    }
    print(f"[pinata] uploading to {PINATA_PIN_DIR_URL} ...", file=sys.stderr)
    resp = requests.post(PINATA_PIN_DIR_URL, headers=headers, files=files, data=data, timeout=300)
    if resp.status_code >= 400:
        print(f"[pinata] FAILED status={resp.status_code} body={resp.text}", file=sys.stderr)
        sys.exit(1)
    body = resp.json()
    body["_files"] = len(file_paths)
    return body


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dist",
        type=Path,
        default=Path.home() / "sisoul-dev/pwa/dist",
        help="PWA build output dir (default ~/sisoul-dev/pwa/dist)",
    )
    ap.add_argument("--name", default="sisoul-pwa", help="Pinata metadata name")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--output", type=Path, default=Path("./pinata-upload.json"))
    args = ap.parse_args()

    if not args.dist.is_dir():
        print(f"ERROR: dist dir not found: {args.dist}", file=sys.stderr)
        print("       Run `cd ~/sisoul-dev/pwa && npm run build` first.", file=sys.stderr)
        return 2

    jwt = os.environ.get("PINATA_JWT", "")
    if not jwt and not args.dry_run:
        print("ERROR: PINATA_JWT env not set. Use --dry-run to test without it.", file=sys.stderr)
        return 2

    result = upload(args.dist, args.name, jwt, args.dry_run)
    args.output.write_text(json.dumps(result, indent=2))
    cid = result.get("IpfsHash") or result.get("cid")
    print(f"[pinata] response → {args.output}", file=sys.stderr)
    print(f"[pinata] CID: {cid}", file=sys.stderr)
    # stdout: 仅 CID, 方便 pipe 给 set-ens-contenthash.py
    print(cid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
