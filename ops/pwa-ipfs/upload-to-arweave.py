#!/usr/bin/env python3
"""Upload ~/sisoul-dev/pwa/dist/ to Arweave via Bundlr/Turbo (v1.0-decentralized #5).

替掉 ``upload-to-pinata.py`` 的 Pinata IPFS 长期存路径, 改走 ArDrive Turbo 直传 Arweave.
- < 100 KiB: free tier (无需付费)
- > 100 KiB: 走付费, 用户先 fund Turbo (USDC/ETH/SOL/MATIC/credit-card)

Env:
    ARWEAVE_WALLET        - Arweave JWK wallet JSON path (Turbo 真上传要签 DataItem)
    SISOUL_TURBO_DRY_RUN  - "1" = 仅 quote, 不真上传
    ARWEAVE_ALLOW_MAINNET - 双 gate 之一. 必须 "1" 且 --confirm-mainnet 才真打 mainnet

Usage:
    # dry-run 单只问价
    ./upload-to-arweave.py --dist ~/sisoul-dev/pwa/dist --dry-run

    # 真上传 (默认 testnet)
    ./upload-to-arweave.py --dist ~/sisoul-dev/pwa/dist

    # 真打 mainnet (双 gate)
    ARWEAVE_ALLOW_MAINNET=1 ./upload-to-arweave.py --dist ... --confirm-mainnet

输出:
    stdout 最后一行 = Arweave tx_id
    ./arweave-upload.json = 完整 UploadReceipt + per-file 列表

Arweave 不直接支持 "dir wrap" (跟 IPFS 不同). 这里把整个 dist tar/zip 成 1 个 tx,
加 _arweave-manifest.json (Arweave path-manifest 让 gateway 自动展开 path).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sisoul.onchain.bundlr_turbo import (  # noqa: E402
    FREE_TIER_BYTES,
    ArweaveUploader,
    BundlrError,
    receipt_to_dict,
)


def build_zip(dist: Path) -> tuple[bytes, list[tuple[str, int]]]:
    buf = io.BytesIO()
    files: list[tuple[str, int]] = []
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(dist.rglob("*")):
            if not f.is_file():
                continue
            rel = str(f.relative_to(dist))
            data = f.read_bytes()
            zf.writestr(rel, data)
            files.append((rel, len(data)))
        manifest = {
            "manifest": "arweave/paths",
            "version": "0.1.0",
            "index": {"path": "index.html"},
            "paths": {rel: {"id": ""} for rel, _ in files},
        }
        zf.writestr("_arweave-manifest.json", json.dumps(manifest, indent=2))
    return buf.getvalue(), files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", type=Path, default=Path.home() / "sisoul-dev/pwa/dist")
    ap.add_argument("--name", default="sisoul-pwa")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--network", choices=["testnet", "mainnet", "mock"], default="testnet")
    ap.add_argument("--confirm-mainnet", action="store_true")
    ap.add_argument("--provider", choices=["turbo", "irys", "arweave-direct", "mock"], default="turbo")
    ap.add_argument("--output", type=Path, default=Path("./arweave-upload.json"))
    args = ap.parse_args()

    dry_run = args.dry_run or os.environ.get("SISOUL_TURBO_DRY_RUN") == "1"

    if not args.dist.is_dir():
        print(f"ERROR: dist dir not found: {args.dist}", file=sys.stderr)
        print("       Run `cd ~/sisoul-dev/pwa && npm run build` first.", file=sys.stderr)
        return 2

    print(f"[arweave] dist={args.dist}", file=sys.stderr)
    blob, files = build_zip(args.dist)
    sha = hashlib.sha256(blob).hexdigest()
    print(f"[arweave] {len(files)} files, total zip = {len(blob)} bytes, sha256={sha[:16]}...", file=sys.stderr)

    wallet_env = os.environ.get("ARWEAVE_WALLET")
    wallet_path = Path(wallet_env).expanduser() if wallet_env else None

    uploader = ArweaveUploader(
        provider=args.provider, network=args.network,
        confirm_mainnet=args.confirm_mainnet, wallet_path=wallet_path,
    )

    try:
        quote = uploader.quote(len(blob))
    except BundlrError as e:
        print(f"[arweave] quote failed: {e}", file=sys.stderr)
        return 1

    print(
        f"[arweave] quote: winc={quote.cost_winc} usd={quote.cost_usd} "
        f"free_tier={quote.free_tier} (FREE_TIER_BYTES={FREE_TIER_BYTES})",
        file=sys.stderr,
    )

    if dry_run:
        print("[arweave] DRY RUN — not uploading", file=sys.stderr)
        out: dict[str, Any] = {
            "dry_run": True,
            "network": uploader.network,
            "requested_network": args.network,
            "provider": uploader.provider,
            "size_bytes": len(blob),
            "sha256": sha,
            "files": [{"path": p, "bytes": b} for p, b in files],
            "quote": {
                "winc": quote.cost_winc, "usd": str(quote.cost_usd),
                "free_tier": quote.free_tier,
            },
        }
        args.output.write_text(json.dumps(out, indent=2))
        print(f"[arweave] response → {args.output}", file=sys.stderr)
        print(f"dry-run-{sha[:43]}")
        return 0

    if uploader.provider != "mock" and (wallet_path is None or not wallet_path.exists()):
        print(
            f"[arweave] ERROR: ARWEAVE_WALLET 未设/不存在 (need {wallet_path}). "
            "用 --dry-run 跑 quote 不要 wallet, 或 export ARWEAVE_WALLET=path/to/wallet.json",
            file=sys.stderr,
        )
        return 2

    try:
        receipt = uploader.upload(
            blob, content_type="application/zip",
            tags={
                "App-Name": args.name,
                "App-Version": "1.0.0-decentralized",
                "Content-Type": "application/zip",
                "Type": "pwa-build",
            },
        )
    except BundlrError as e:
        print(f"[arweave] upload failed: {e}", file=sys.stderr)
        return 1

    out2: dict[str, Any] = {
        "dry_run": False,
        "network": uploader.network,
        "provider": uploader.provider,
        "size_bytes": len(blob),
        "sha256": sha,
        "files": [{"path": p, "bytes": b} for p, b in files],
        "receipt": receipt_to_dict(receipt),
    }
    args.output.write_text(json.dumps(out2, indent=2))
    print(f"[arweave] response → {args.output}", file=sys.stderr)
    print(f"[arweave] tx_id: {receipt.tx_id}", file=sys.stderr)
    print(f"[arweave] bundle_id: {receipt.bundle_id}", file=sys.stderr)
    print(f"[arweave] fetch_url: {receipt.fetch_url}", file=sys.stderr)
    print(f"[arweave] cost_paid_usd: {receipt.cost_paid_usd}", file=sys.stderr)
    print(receipt.tx_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
