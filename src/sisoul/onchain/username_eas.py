"""sisoul username -> did:key registry on EAS (Ethereum Attestation Service).

v1.0-stable decentralised username layer (HANDOFF §1 deliverable 2 + Workstream B).

Design (per user decision 2026-06-06 = "EAS Optimism mainnet"):
- A username claim is an EAS attestation on Optimism. EAS is permissionless,
  trustless infra — NOT sisoul-operated. Anyone can attest; any client reads via
  any EAS indexer (easscan is one public, free option). No central name authority,
  so this is §4.10-compatible (sisoul issues nothing, takes no cut, runs no server).
- Resolution rule: first valid attestation per username wins (lowest block / time).
  Later claims to the same name are ignored by clients.
- The signing/paying key is an EVM account derived from the user's SAME BIP-39 seed
  (standard path m/44'/60'/0'/0/0). No separate key import — keeps "极简 UX". The
  user funds that address with a few cents of OP ETH to pay their own gas. sisoul
  never custodies, subsidises, or routes value (§4.10).

Schema: ``string username,string did_key,uint64 issued_at``
  The EAS ``attester`` field already records the claimant's EVM address, so it is
  not duplicated as a schema field. The schema UID is deterministic (keccak of the
  schema string + resolver + revocable), so clients resolve against it even before
  anyone registers it on-chain; registering merely makes the schema record exist.

v1.0 known limitation: the did_key->EVM binding is asserted (the attester chose
the did_key field) but not cryptographically proven inside the attestation, because
sisoul did:key is an X25519 (encryption) key, not a signing key. Squatting
resistance = first-claim-wins + the attester pays gas. A signed did->evm binding is
deferred to v1.1 (needs an Ed25519 sub-key). Documented in docs/CHAIN-STATUS.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# ── chains (OP-stack EAS predeploys: identical addresses on mainnet + sepolia) ──

EVM_DERIVATION_PATH = "m/44'/60'/0'/0/0"
USERNAME_SCHEMA = "string username,string did_key,uint64 issued_at"

EAS_CONTRACT = "0x4200000000000000000000000000000000000021"
SCHEMA_REGISTRY = "0x4200000000000000000000000000000000000020"
ZERO_RESOLVER = "0x0000000000000000000000000000000000000000"

OPTIMISM_MAINNET_CHAIN_ID = 10
OPTIMISM_MAINNET_RPC = "https://mainnet.optimism.io"
OPTIMISM_MAINNET_EASSCAN = "https://optimism.easscan.org/graphql"

OPTIMISM_SEPOLIA_CHAIN_ID = 11155420
OPTIMISM_SEPOLIA_RPC = "https://sepolia.optimism.io"
OPTIMISM_SEPOLIA_EASSCAN = "https://optimism-sepolia.easscan.org/graphql"

ALLOW_MAINNET_ENV = "EAS_ALLOW_MAINNET"


@dataclass(frozen=True)
class Chain:
    network: str
    chain_id: int
    rpc_url: str
    easscan_graphql: str
    is_mainnet: bool


CHAINS: dict[str, Chain] = {
    "optimism-mainnet": Chain(
        "optimism-mainnet", OPTIMISM_MAINNET_CHAIN_ID, OPTIMISM_MAINNET_RPC,
        OPTIMISM_MAINNET_EASSCAN, True,
    ),
    "optimism-sepolia": Chain(
        "optimism-sepolia", OPTIMISM_SEPOLIA_CHAIN_ID, OPTIMISM_SEPOLIA_RPC,
        OPTIMISM_SEPOLIA_EASSCAN, False,
    ),
}
# short aliases
CHAINS["mainnet"] = CHAINS["optimism-mainnet"]
CHAINS["sepolia"] = CHAINS["optimism-sepolia"]
CHAINS["optimism"] = CHAINS["optimism-sepolia"]  # default short -> testnet (safe)


# ── exceptions ────────────────────────────────────────────────────────────────


class UsernameEASError(Exception):
    """username-EAS 通用异常."""


class MainnetBlockedError(UsernameEASError):
    """主网双 gate 未满足 (需 EAS_ALLOW_MAINNET=1 + allow_mainnet=True)."""


class IndexerError(UsernameEASError):
    """easscan GraphQL 查询失败."""


# ── EVM account derived from BIP-39 seed ──────────────────────────────────────


@dataclass
class EVMAccount:
    address: str
    private_key: str  # 0x hex — never log / persist this

    def public(self) -> dict[str, str]:
        """Safe-to-print subset (no private key)."""
        return {"evm_address": self.address, "derivation_path": EVM_DERIVATION_PATH}


def derive_evm_account(mnemonic: str) -> EVMAccount:
    """Derive the EVM account from a BIP-39 mnemonic (standard ETH path)."""
    from eth_account import Account

    Account.enable_unaudited_hdwallet_features()
    acct = Account.from_mnemonic(mnemonic.strip(), account_path=EVM_DERIVATION_PATH)
    pk = acct.key.hex()  # eth_account 7.x returns no 0x prefix; normalise.
    if not pk.startswith("0x"):
        pk = "0x" + pk
    return EVMAccount(address=acct.address, private_key=pk)


def load_evm_account(seed_path: Optional[Path] = None) -> EVMAccount:
    """Load the user's BIP-39 seed and derive the EVM account from it."""
    from sisoul.identity.seed import load_mnemonic_from_file

    mnemonic = load_mnemonic_from_file(seed_path)
    return derive_evm_account(mnemonic)


# ── schema UID + attestation data encoding ────────────────────────────────────


def compute_schema_uid(
    schema: str = USERNAME_SCHEMA,
    resolver: str = ZERO_RESOLVER,
    revocable: bool = True,
) -> str:
    """EAS schema UID = keccak256(abi.encodePacked(schema, resolver, revocable)).

    Deterministic — independent of which chain it is registered on.
    """
    from eth_abi.packed import encode_packed
    from eth_utils import keccak

    packed = encode_packed(["string", "address", "bool"], [schema, resolver, revocable])
    return "0x" + keccak(packed).hex()


#: deterministic UID of the username schema (same on every OP-stack chain).
USERNAME_SCHEMA_UID = compute_schema_uid()


def encode_username_data(username: str, did_key: str, issued_at: Optional[int] = None) -> tuple[str, int]:
    """ABI-encode the (username, did_key, issued_at) tuple for the attestation `data`."""
    from eth_abi import encode

    issued_at = int(issued_at if issued_at is not None else time.time())
    blob = encode(["string", "string", "uint64"], [username, did_key, issued_at])
    return "0x" + blob.hex(), issued_at


def resolve_chain(network: str) -> Chain:
    key = (network or "").lower()
    if key in CHAINS:
        return CHAINS[key]
    raise UsernameEASError(f"未知 network {network!r}; 支持 {sorted(CHAINS)}")


def _mainnet_gate(chain: Chain, allow_mainnet: bool) -> None:
    if not chain.is_mainnet:
        return
    env_ok = os.environ.get(ALLOW_MAINNET_ENV) == "1"
    if not (allow_mainnet and env_ok):
        raise MainnetBlockedError(
            f"{chain.network} 上链被双 gate 阻止: 需 env {ALLOW_MAINNET_ENV}=1 + "
            f"allow_mainnet=True (防误花真钱). 当前 env_ok={env_ok}, allow_mainnet={allow_mainnet}."
        )


# ── register a username (build always; send only on live, gated) ──────────────

# minimal EAS ABI (just attest()).
_EAS_ATTEST_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "bytes32", "name": "schema", "type": "bytes32"},
                    {
                        "components": [
                            {"internalType": "address", "name": "recipient", "type": "address"},
                            {"internalType": "uint64", "name": "expirationTime", "type": "uint64"},
                            {"internalType": "bool", "name": "revocable", "type": "bool"},
                            {"internalType": "bytes32", "name": "refUID", "type": "bytes32"},
                            {"internalType": "bytes", "name": "data", "type": "bytes"},
                            {"internalType": "uint256", "name": "value", "type": "uint256"},
                        ],
                        "internalType": "struct AttestationRequestData",
                        "name": "data",
                        "type": "tuple",
                    },
                ],
                "internalType": "struct AttestationRequest",
                "name": "request",
                "type": "tuple",
            }
        ],
        "name": "attest",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "payable",
        "type": "function",
    }
]


def build_register_plan(
    username: str,
    did_key: str,
    *,
    network: str = "optimism-sepolia",
    schema_uid: str = USERNAME_SCHEMA_UID,
    seed_path: Optional[Path] = None,
    issued_at: Optional[int] = None,
) -> dict[str, Any]:
    """Build (but do not send) the username attestation. Pure / offline / testable.

    Returns the full plan incl. the derived EVM address the user must fund.
    """
    acct = load_evm_account(seed_path)
    chain = resolve_chain(network)
    data_hex, issued = encode_username_data(username, did_key, issued_at)
    return {
        "method": "dry-run",
        "username": username,
        "did_key": did_key,
        "issued_at": issued,
        "evm_address": acct.address,
        "evm_derivation": EVM_DERIVATION_PATH,
        "network": chain.network,
        "chain_id": chain.chain_id,
        "eas_contract": EAS_CONTRACT,
        "schema_uid": schema_uid,
        "data": data_hex,
        "is_mainnet": chain.is_mainnet,
    }


def register_username(
    username: str,
    did_key: str,
    *,
    network: str = "optimism-sepolia",
    schema_uid: str = USERNAME_SCHEMA_UID,
    seed_path: Optional[Path] = None,
    rpc_url: Optional[str] = None,
    dry_run: bool = True,
    allow_mainnet: bool = False,
    gas_timeout: float = 180.0,
) -> dict[str, Any]:
    """Register a username as an EAS attestation.

    dry_run=True (default): build the plan, return the derived EVM address + encoded
    data, send nothing. Safe + offline.

    dry_run=False: actually sign with the seed-derived EVM key and broadcast. Needs
    the address funded with OP gas + an RPC. Mainnet is double-gated.
    """
    plan = build_register_plan(
        username, did_key, network=network, schema_uid=schema_uid, seed_path=seed_path
    )
    if dry_run:
        return plan

    chain = resolve_chain(network)
    _mainnet_gate(chain, allow_mainnet)

    from web3 import Web3

    acct = load_evm_account(seed_path)
    w3 = Web3(Web3.HTTPProvider(rpc_url or chain.rpc_url))
    if not w3.is_connected():
        raise UsernameEASError(f"RPC 连不上: {rpc_url or chain.rpc_url}")
    onchain_id = w3.eth.chain_id
    if onchain_id != chain.chain_id:
        raise UsernameEASError(
            f"RPC chain_id={onchain_id} 与 {chain.network}(期望 {chain.chain_id}) 不符"
        )

    # ── ensure schema registered on this chain (one-time per chain) ────────
    # EAS schema UIDs are deterministic across chains (keccak256 of schema +
    # resolver + revocable), but the SchemaRegistry per-chain must still have
    # an entry for that UID before attest() will accept it. attest() reverts
    # with InvalidSchema() (0xbf37b20e) when missing. Register on-demand.
    sr_abi_get = [{"inputs": [{"name": "uid", "type": "bytes32"}], "name": "getSchema",
                   "outputs": [{"components": [
                       {"name": "uid", "type": "bytes32"},
                       {"name": "resolver", "type": "address"},
                       {"name": "revocable", "type": "bool"},
                       {"name": "schema", "type": "string"}], "name": "", "type": "tuple"}],
                   "stateMutability": "view", "type": "function"}]
    sr_abi_reg = [{"inputs": [{"name": "schema", "type": "string"},
                              {"name": "resolver", "type": "address"},
                              {"name": "revocable", "type": "bool"}],
                   "name": "register", "outputs": [{"name": "", "type": "bytes32"}],
                   "stateMutability": "nonpayable", "type": "function"}]
    sr_addr = Web3.to_checksum_address(SCHEMA_REGISTRY)
    sr_view = w3.eth.contract(address=sr_addr, abi=sr_abi_get)
    rec = sr_view.functions.getSchema(bytes.fromhex(schema_uid[2:])).call()
    # rec = (uid, resolver, revocable, schema) — uid is 0x00..00 if unregistered
    if int.from_bytes(rec[0], "big") == 0:
        sr_write = w3.eth.contract(address=sr_addr, abi=sr_abi_reg)
        reg_tx = sr_write.functions.register(USERNAME_SCHEMA, Web3.to_checksum_address(ZERO_RESOLVER), True).build_transaction({
            "from": Web3.to_checksum_address(acct.address),
            "nonce": w3.eth.get_transaction_count(Web3.to_checksum_address(acct.address)),
            "value": 0,
        })
        reg_signed = w3.eth.account.sign_transaction(reg_tx, acct.private_key)
        reg_raw = getattr(reg_signed, "raw_transaction", None) or getattr(reg_signed, "rawTransaction")
        reg_hash = w3.eth.send_raw_transaction(reg_raw)
        reg_receipt = w3.eth.wait_for_transaction_receipt(reg_hash, timeout=gas_timeout)
        if reg_receipt.get("status") != 1:
            raise UsernameEASError(f"schema register tx reverted: {reg_hash.hex()}")
        # post-condition: schema now resolvable
        rec2 = sr_view.functions.getSchema(bytes.fromhex(schema_uid[2:])).call()
        if int.from_bytes(rec2[0], "big") == 0:
            raise UsernameEASError(
                f"schema register tx succeeded but getSchema still empty (uid mismatch?). "
                f"tx={reg_hash.hex()}"
            )

    eas = w3.eth.contract(address=Web3.to_checksum_address(EAS_CONTRACT), abi=_EAS_ATTEST_ABI)
    request = (
        bytes.fromhex(schema_uid[2:]),
        (
            Web3.to_checksum_address(acct.address),  # recipient = self
            0,  # expirationTime = no expiry
            True,  # revocable
            b"\x00" * 32,  # refUID
            bytes.fromhex(plan["data"][2:]),
            0,  # value
        ),
    )
    fn = eas.functions.attest(request)
    tx = fn.build_transaction(
        {
            "from": Web3.to_checksum_address(acct.address),
            "nonce": w3.eth.get_transaction_count(Web3.to_checksum_address(acct.address)),
            "value": 0,
        }
    )
    signed = w3.eth.account.sign_transaction(tx, acct.private_key)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
    tx_hash = w3.eth.send_raw_transaction(raw)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=gas_timeout)
    if receipt.get("status") != 1:
        raise UsernameEASError(f"attest tx reverted: {tx_hash.hex()}")
    return {
        "method": "live",
        "username": username,
        "did_key": did_key,
        "evm_address": acct.address,
        "network": chain.network,
        "schema_uid": schema_uid,
        "tx_hash": tx_hash.hex(),
        "block": receipt.get("blockNumber"),
        "gas_used": receipt.get("gasUsed"),
    }


# ── resolve / discover via an EAS indexer (easscan, free + public) ────────────


def _easscan_query(graphql_url: str, query: str, variables: dict, timeout: float) -> dict:
    import httpx

    try:
        r = httpx.post(graphql_url, json={"query": query, "variables": variables}, timeout=timeout)
        r.raise_for_status()
        body = r.json()
    except Exception as e:  # noqa: BLE001
        raise IndexerError(f"easscan 查询失败 ({graphql_url}): {e}") from e
    if "errors" in body and body["errors"]:
        raise IndexerError(f"easscan GraphQL errors: {body['errors']}")
    return body.get("data", {})


def _decode_fields(decoded_data_json: str) -> dict[str, Any]:
    """easscan stores attestation fields as decodedDataJson; flatten to name->value."""
    out: dict[str, Any] = {}
    try:
        for item in json.loads(decoded_data_json):
            name = item.get("name")
            val = item.get("value", {})
            # value is {name, type, value}; the inner value may be str/int/dict
            inner = val.get("value") if isinstance(val, dict) else val
            if isinstance(inner, dict) and "hex" in inner:  # bignumber-ish
                inner = inner.get("hex")
            out[name] = inner
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return out


_RESOLVE_QUERY = (
    "query($schema:String!){attestations("
    "where:{schemaId:{equals:$schema},revoked:{equals:false}},"
    "orderBy:[{time:asc}]){attester time decodedDataJson}}"
)


def resolve_username(
    username: str,
    *,
    network: str = "optimism-mainnet",
    schema_uid: str = USERNAME_SCHEMA_UID,
    indexer_url: Optional[str] = None,
    timeout: float = 12.0,
) -> Optional[str]:
    """Resolve @username -> did:key via an EAS indexer. First-claim-wins.

    Returns the did_key string, or None if no (valid, first) attestation claims it.
    """
    chain = resolve_chain(network)
    url = indexer_url or chain.easscan_graphql
    data = _easscan_query(url, _RESOLVE_QUERY, {"schema": schema_uid}, timeout)
    rows = data.get("attestations", []) or []
    for row in rows:  # already ordered by time asc -> first match wins
        fields = _decode_fields(row.get("decodedDataJson", "[]"))
        if fields.get("username") == username:
            did = fields.get("did_key")
            return str(did) if did else None
    return None


def discover(
    *,
    network: str = "optimism-mainnet",
    schema_uid: str = USERNAME_SCHEMA_UID,
    indexer_url: Optional[str] = None,
    limit: int = 50,
    timeout: float = 12.0,
) -> list[dict[str, Any]]:
    """List recent username claims (username + did_key + issued_at + attester)."""
    chain = resolve_chain(network)
    url = indexer_url or chain.easscan_graphql
    query = (
        "query($schema:String!,$take:Int!){attestations("
        "where:{schemaId:{equals:$schema},revoked:{equals:false}},"
        "orderBy:[{time:desc}],take:$take){attester time decodedDataJson}}"
    )
    data = _easscan_query(url, query, {"schema": schema_uid, "take": int(limit)}, timeout)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in data.get("attestations", []) or []:
        fields = _decode_fields(row.get("decodedDataJson", "[]"))
        name = fields.get("username")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(
            {
                "username": name,
                "did_key": fields.get("did_key"),
                "issued_at": fields.get("issued_at"),
                "attester": row.get("attester"),
            }
        )
    return out


__all__ = [
    "USERNAME_SCHEMA",
    "USERNAME_SCHEMA_UID",
    "EVM_DERIVATION_PATH",
    "CHAINS",
    "EVMAccount",
    "UsernameEASError",
    "MainnetBlockedError",
    "IndexerError",
    "derive_evm_account",
    "load_evm_account",
    "compute_schema_uid",
    "encode_username_data",
    "build_register_plan",
    "register_username",
    "resolve_username",
    "discover",
]
