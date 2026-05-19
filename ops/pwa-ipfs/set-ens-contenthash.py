#!/usr/bin/env python3
"""Set ENS contenthash to an IPFS CID via the public resolver.

Env:
    WEB3_RPC      - JSON-RPC URL (e.g. https://mainnet.optimism.io or https://eth.llamarpc.com)
    ENS_OWNER_PK  - private key of ENS name owner (hex, 0x...)
    ENS_RESOLVER  - resolver address (default: ENS public resolver on mainnet)

Usage:
    ./set-ens-contenthash.py --name sisoul.eth --cid bafybei...
    ./set-ens-contenthash.py --name sisoul.eth --cid bafybei... --dry-run

Library deps for real run: web3>=6, eth_utils. dry-run has zero deps.
"""

from __future__ import annotations

import argparse
import os
import sys

DEFAULT_RESOLVER = "0x231b0Ee14048e9dCcD1d247744d114a4EB5E8E63"  # ENS public resolver v2 (mainnet)
IPFS_CONTENTHASH_PREFIX = "0xe301"  # 0xe3 = ipfs-ns multicodec, 0x01 = cidv1


def cid_to_contenthash(cid: str) -> str:
    """Convert CIDv1 string → EIP-1577 contenthash hex (0xe301 + multihash bytes).

    Real impl needs `multiformats` lib. For dry-run we return a tagged placeholder
    so the caller can see what would be written without depending on multiformats.
    """
    if not cid:
        raise ValueError("CID empty")
    try:
        import multiformats  # type: ignore
    except ImportError:
        # placeholder so dry-run / no-deps doesn't crash. Real run requires this lib.
        return f"{IPFS_CONTENTHASH_PREFIX}<MULTIHASH_OF:{cid}>"

    cid_obj = multiformats.CID.decode(cid)
    # 取 multihash 部分 (跳过 cid version + codec, EIP-1577 不重复 encode)
    multihash_bytes = cid_obj.raw_digest_with_prefix  # type: ignore[attr-defined]
    return IPFS_CONTENTHASH_PREFIX + multihash_bytes.hex()


def set_contenthash_dry(name: str, cid: str, resolver: str, rpc: str) -> dict:
    ch = cid_to_contenthash(cid)
    print(f"[ens] DRY RUN — would set contenthash on resolver {resolver}", file=sys.stderr)
    print(f"[ens]   name      : {name}", file=sys.stderr)
    print(f"[ens]   CID       : {cid}", file=sys.stderr)
    print(f"[ens]   contenthash: {ch}", file=sys.stderr)
    print(f"[ens]   RPC       : {rpc}", file=sys.stderr)
    return {
        "dry_run": True,
        "name": name,
        "cid": cid,
        "contenthash": ch,
        "resolver": resolver,
        "rpc": rpc,
        "tx_hash": "0xDRY_RUN_NO_TX_SENT",
    }


def set_contenthash_real(name: str, cid: str, resolver: str, rpc: str, pk: str) -> dict:
    try:
        from web3 import Web3
        from eth_account import Account
    except ImportError:
        print("ERROR: web3 + eth_account required. pip install 'web3>=6'", file=sys.stderr)
        sys.exit(2)

    w3 = Web3(Web3.HTTPProvider(rpc))
    if not w3.is_connected():
        print(f"ERROR: cannot connect to RPC {rpc}", file=sys.stderr)
        sys.exit(1)

    acct = Account.from_key(pk)
    chain_id = w3.eth.chain_id
    print(f"[ens] connected chain_id={chain_id} from={acct.address}", file=sys.stderr)

    # ENS namehash
    from eth_utils import keccak

    def namehash(n: str) -> bytes:
        node = b"\x00" * 32
        for label in reversed(n.split(".")):
            node = keccak(node + keccak(label.encode()))
        return node

    node = namehash(name)
    contenthash_hex = cid_to_contenthash(cid)
    contenthash_bytes = bytes.fromhex(contenthash_hex[2:])

    # ABI: setContenthash(bytes32 node, bytes contenthash)
    abi = [
        {
            "inputs": [
                {"name": "node", "type": "bytes32"},
                {"name": "hash", "type": "bytes"},
            ],
            "name": "setContenthash",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function",
        }
    ]
    contract = w3.eth.contract(address=Web3.to_checksum_address(resolver), abi=abi)

    nonce = w3.eth.get_transaction_count(acct.address)
    gas_price = w3.eth.gas_price
    tx = contract.functions.setContenthash(node, contenthash_bytes).build_transaction(
        {
            "from": acct.address,
            "nonce": nonce,
            "gas": 120_000,
            "gasPrice": gas_price,
            "chainId": chain_id,
        }
    )
    signed = w3.eth.account.sign_transaction(tx, pk)
    print(f"[ens] sending tx ...", file=sys.stderr)
    tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    print(f"[ens] tx sent: {tx_hash.hex()} — waiting for receipt ...", file=sys.stderr)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    return {
        "dry_run": False,
        "name": name,
        "cid": cid,
        "contenthash": contenthash_hex,
        "resolver": resolver,
        "rpc": rpc,
        "tx_hash": tx_hash.hex(),
        "block_number": receipt.blockNumber,
        "gas_used": receipt.gasUsed,
        "status": receipt.status,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="ENS name e.g. sisoul.eth")
    ap.add_argument("--cid", required=True, help="IPFS CIDv1 (bafy...)")
    ap.add_argument("--resolver", default=DEFAULT_RESOLVER)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rpc = os.environ.get("WEB3_RPC", "")
    pk = os.environ.get("ENS_OWNER_PK", "")

    if args.dry_run:
        rpc = rpc or "https://eth.llamarpc.com (mock)"
        out = set_contenthash_dry(args.name, args.cid, args.resolver, rpc)
    else:
        if not rpc:
            print("ERROR: WEB3_RPC env required for real run", file=sys.stderr)
            return 2
        if not pk:
            print("ERROR: ENS_OWNER_PK env required for real run", file=sys.stderr)
            return 2
        out = set_contenthash_real(args.name, args.cid, args.resolver, rpc, pk)

    import json
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
