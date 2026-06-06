# Chain Status — what's testnet vs mainnet, and should we switch?

> Audit done 2026-06-06 (evening). Every claim is `grep`'d from `src/`.

## TL;DR

| Layer | Network | Status | Can we use it today? | Should we switch to mainnet now? |
|---|---|---|---|---|
| **EAS attestations** (friend / borrow ledger / reputation / SBT) | Optimism Sepolia (testnet) — also Arbitrum/Base/zkSync Sepolia | ✅ shipped | ✅ free, can be played with | ❌ Not now — wait for v1.0 stable (T+6m) |
| **Arweave snapshots** (vault encrypted snapshot persistence) | Mainnet (with `ARWEAVE_ALLOW_MAINNET=1` double-gate) | ✅ shipped | ⚠️ costs real AR; alpha runs in "free-tier-only" mode | ✅ Already mainnet (read), opt-in for write |
| **USDT-TRC20 micropay** (cross-stranger borrow) | TRC20 mainnet (Tron) | ✅ shipped today (Phase C MVP + chain-watcher) | ✅ free to query (TronGrid public API), real-money settlements | ✅ Already mainnet — this is the user paying the lender directly |
| **IPFS / kubo** (P2P transport for friend/borrow/chat) | Public mainnet swarm | ✅ shipped | ✅ free | ✅ Already mainnet |
| **kubo bootstrap nodes** | Public DHT + sisoul's 3 public peers | ✅ shipped | ✅ free | ✅ Already mainnet |
| **ENS** (`<handle>.sisoul.eth`) | Code path exists; no domain registered | ❌ deferred | n/a | ❌ Wait until SBT mainnet ship + brand confidence |
| **SBT honour badge** (`contracts/src/SisoulSBT.sol`) | Contract written; not deployed anywhere | ❌ deferred | n/a (no deploy yet) | ❌ Mint to Optimism mainnet at v1.0 stable, not before |
| **DAO governance** | Code skeleton only (not deployed) | ❌ deferred | n/a | ❌ Per §4.10, never (no token DAO ever) |

## Why EAS attestations are still Sepolia testnet

Wave 4 (`Phase F`) shipped EAS support with a hard guard in `src/sisoul/onchain/eas.py:713`:

> `# network=optimism-mainnet → 拒 (波 4 约束).`

This is **policy, not a technical limit**:

- The EAS contract address `0x4200000000000000000000000000000000000021` is the **same on Optimism mainnet AND Optimism Sepolia** — both are OP Stack chains deploying via a deterministic predeploy. Same code, same ABI, same call shape.
- Removing the guard takes ~5 lines.
- The reason we kept it as `policy → reject` is that **flipping it to mainnet means real gas**. At today's prices:
  - Optimism mainnet: ~$0.001-0.01 per attestation
  - 100 active alpha testers × 10 borrows/day × 5 attestations/borrow × 30 days = **150,000 attestations/month = ~$150-$1500/month, all paid by alpha testers' wallets**.

That's a lot of friction for an alpha that hasn't proven product-market fit yet. The Sepolia testnet is free, identical in behaviour, and lets us catch all the bugs that matter (data model, schema, retries, deduplication).

## Why USDT-TRC20 is already mainnet

USDT-TRC20 is **not our chain**. We don't deploy anything. We just look up tx history on TronGrid's free public API.

- Borrower runs `sisoul borrow run --dry-run` → sees "0.05 USDT to T...".
- Borrower opens **their own** wallet (Trust / TronLink / Binance / SafePal) and sends 0.05 USDT to the lender's T-address.
- Lender runs `sisoul wallet inbound` → TronGrid returns the tx in 1-3 minutes (Tron finality).
- Lender confirms in `sisoul lend approve` (alpha v1.1: automated; alpha v1.0: manual).

We **must** use mainnet here because Tron testnet (Nile / Shasta) doesn't have USDT, and even if it did, alpha users wouldn't want to bridge fake testnet stablecoins.

## Should we switch EAS to mainnet now? Cost/benefit

| Option | Pros | Cons | Recommendation |
|---|---|---|---|
| **Stay Sepolia** (current) | Free; bug-discovery surface unchanged | "It's just testnet" reputation; nobody verifies attestations | ✅ Keep until v1.0 stable (T+6m) |
| **Flip to Optimism mainnet now** | "Real" reputation; tronscan-style verifiable attestations | $150-$1500/month bill on alpha testers; rollback is hard once attestations are on mainnet | ❌ Not now |
| **Hybrid**: opt-in mainnet via env var | Alpha testers can choose | Splits the audit graph; reputation can't be meaningfully ranked across testnet+mainnet | ❌ Bad UX |

**Recommendation**: stay Sepolia for alpha + beta. Switch all networks to Optimism mainnet at v1.0 stable (T+6m), in one batch, with a documented migration path for any testnet attestations users want to "preserve" (we'll write a snapshot script).

## What "切到 mainnet" actually involves (when we do it)

1. Set `network=optimism-mainnet` (or per-attestation override).
2. Deploy SBT honour badge to Optimism mainnet (one-time, costs maintainer ~$5 in gas).
3. Update `docs/GOVERNANCE.md` and `docs/TOKENOMICS.md` to point at the mainnet contract address.
4. Migrate any "valuable" Sepolia attestations:
   - Provide a `sisoul attest migrate --from sepolia --to mainnet --since 2026-XX-XX` command (write it then).
   - For most alpha attestations: don't migrate. They're test data.
5. Bump `sisoul --version` to `1.0.0` (drop `-alpha`).

Estimated work when we do it: **3-5 days** including testing, not 3-5 hours.

## What about Arweave?

Arweave is already mainnet **but read-only** by default. Write requires:

- `ARWEAVE_NETWORK=mainnet` AND `ARWEAVE_ALLOW_MAINNET=1` (double gate per `src/sisoul/onchain/bundlr_turbo.py:245`)

Real Arweave snapshot cost: ~$0.10/MB. A monthly encrypted vault snapshot at 10 MB = $1/month per user. For 10K users = $10K/month — sustainable via grants but worth noting (whitepaper §4.10).

Alpha testers default to **bundlr/turbo free tier** (no cost for the first few MB). Power users opt in to mainnet writes when their vault grows.

## What about the "半吊子状态可以切 mainnet 吗" question?

Yes, technically:

- All the mainnet-vs-testnet differences are config (chain ID, RPC URL, contract address).
- The data model, signing, schema, replay protection — all identical.
- We've tested 158 pytests + manual e2e on Sepolia. The same code paths work on mainnet.

But **should** we, today, with 0 stars and 0 verified alpha users? No.

The right time is v1.0 stable (T+6m) when:
- We have evidence that 50+ active users want this on-chain.
- The reputation graph has enough density that "your attestation is on Optimism mainnet" actually means something.
- We've raised the first round of grants to cover the gas-cost overhead for the first batch of users.

Until then: Sepolia for EAS, mainnet for USDT (mandatory) + IPFS (mandatory) + Arweave (opt-in).
