# PWA on IPFS + ENS

Deploy the Sisoul PWA build (`~/sisoul-dev/pwa/dist/`) to IPFS via Pinata, then
point an ENS name at the resulting CID via the public resolver `setContenthash`.

Scripts ship with a full **dry-run** mode that performs no network calls, no
signing, and zero spend — useful for previewing the exact bytes / requests / tx
before going live.

## Files

| Script | Job |
|--------|-----|
| `upload-to-pinata.py` | Multipart upload of `dist/` → IPFS via Pinata (CIDv1, wrapped dir) |
| `set-ens-contenthash.py` | `setContenthash(node, hash)` on the ENS resolver |
| `dry-run.py` | Single command, end-to-end simulation, zero network |

## Required env

| Var | Used by | Required for real run? |
|-----|---------|------------------------|
| `PINATA_JWT` | `upload-to-pinata.py` | Yes |
| `WEB3_RPC` | `set-ens-contenthash.py` | Yes — Ethereum mainnet JSON-RPC |
| `ENS_OWNER_PK` | `set-ens-contenthash.py` | Yes — private key of ENS name owner |
| `ENS_RESOLVER` | `set-ens-contenthash.py` | No — defaults to ENS public resolver v2 |

## Dependencies for real run

```bash
pip install requests web3 eth-account eth-utils multiformats
```

`dry-run.py` has **zero** third-party deps.

## Workflows

### Dry run (recommended first)

```bash
./dry-run.py
```

Output is a 3-step "would-happen" report including file count, mock CID, mock
contenthash, and the exact RPC method + arguments.

You can also dry-run individual stages:

```bash
./upload-to-pinata.py --dry-run
./set-ens-contenthash.py --name sisoul.eth --cid bafybeitest --dry-run
```

### Real run

```bash
# 1. Build PWA
cd ~/sisoul-dev/pwa && npm run build

# 2. Set env
export PINATA_JWT="<jwt from app.pinata.cloud/keys>"
export WEB3_RPC="https://eth.llamarpc.com"   # or your own node / Infura
export ENS_OWNER_PK="0x<hex>"                 # owner of the ENS name

# 3. Upload (CID prints on stdout)
CID=$(./upload-to-pinata.py --dist ~/sisoul-dev/pwa/dist --name sisoul-pwa-v0.1.0)
echo "CID = $CID"

# 4. Update ENS contenthash
./set-ens-contenthash.py --name sisoul.eth --cid "$CID"
```

After ~1 block confirmation the PWA is reachable at:

- `https://sisoul.eth.limo` (eth.limo gateway, no wallet needed)
- `ipfs://<CID>` in any IPFS-aware browser (Brave, Status, etc.)

## Safety

- All real-network scripts **fail closed** if their env var is missing — they
  will not silently fall back to dry-run.
- `set-ens-contenthash.py` uses `wait_for_transaction_receipt(timeout=180)` so
  you'll see the on-chain status before the script exits.
- `upload-to-pinata.py` writes the full Pinata response to `./pinata-upload.json`
  for audit.

## Cost sketch (mainnet ETH, approx)

- Pinata upload: ~free for small projects; check your tier
- ENS `setContenthash` gas: ~50–60k gas. At 20 gwei ≈ 0.0012 ETH.

## Notes

- The CID is wrapped in a directory (`wrapWithDirectory: true`) so URLs like
  `ipfs://<CID>/index.html` resolve correctly.
- For Optimism / Base ENS namespaces, point `WEB3_RPC` to that L2 and pass
  `--resolver` for the L2 public resolver instead of the mainnet default.
