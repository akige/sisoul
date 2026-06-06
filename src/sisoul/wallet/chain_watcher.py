"""USDT-TRC20 chain-watcher MVP — read-only TronGrid lookup.

Per docs/INCENTIVE-DESIGN.md, sisoul never custodies funds. This module is
PURELY a convenience for the lender side: given a receive address and a
recent borrow's expected USDT amount, query TronGrid's free public API to
see if any matching inbound TRC20 USDT transfer landed.

Lookup logic (alpha v1.1 MVP):
  - GET https://api.trongrid.io/v1/accounts/<addr>/transactions/trc20
        ?only_to=true&limit=20&contract_address=<usdt>
  - Filter for the USDT-TRC20 contract:
        TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t  (Tether USD on Tron)
  - For each, check value (units 1e-6) and from address (optional borrower hint)
  - Return list of likely matches with (tx_id, value, from_addr, age_seconds)

No signing, no spending — just an HTTP GET. The lender decides whether to
approve the LendRequest based on the returned matches.

Out of scope (deferred to v1.1 GA):
  - Automatic LendRequest approval — `sisoul lend approve` still requires
    the lender to type `y`.
  - Tx hash signature verification — lenders MUST also check Tronscan / their
    own wallet to confirm the tx is irreversible (1-2 minute finality on TRX).
  - Concurrency / pagination across >20 historical txs.
"""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional

try:
    import httpx
    HAVE_HTTPX = True
except Exception:
    HAVE_HTTPX = False


USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRONGRID_API_BASE = "https://api.trongrid.io"


class ChainWatcherError(Exception):
    """Base."""


@dataclass(frozen=True)
class InboundUsdtTx:
    tx_id: str
    from_addr: str
    to_addr: str
    value_usdt: float
    block_timestamp_ms: int
    age_seconds: float

    @property
    def tronscan_url(self) -> str:
        return f"https://tronscan.org/#/transaction/{self.tx_id}"


def list_inbound_usdt(
    receive_address: str,
    *,
    limit: int = 20,
    min_age_seconds: float = 0.0,
    max_age_seconds: Optional[float] = None,
    min_value_usdt: float = 0.0,
    expected_value_usdt: Optional[float] = None,
    expected_value_tolerance_pct: float = 5.0,
    timeout_seconds: float = 12.0,
) -> list[InboundUsdtTx]:
    """Query TronGrid for recent inbound USDT-TRC20 transfers to `receive_address`.

    Filters:
      - min_age_seconds / max_age_seconds: drop txs outside this age window
        (e.g. min_age=60 to skip in-flight, max_age=86400 to only see 24h)
      - min_value_usdt: drop dust
      - expected_value_usdt + tolerance: match a specific borrow quote, e.g.
        expected_value_usdt=0.05, tolerance=5 → accept 0.0475..0.0525

    Returns: list of InboundUsdtTx, newest first.
    Raises ChainWatcherError on HTTP failure or unparseable response.
    """
    if not HAVE_HTTPX:
        raise ChainWatcherError("httpx not installed — pip install httpx")
    if not receive_address.startswith("T") or len(receive_address) != 34:
        raise ChainWatcherError(
            f"receive_address must be a TRC20 T-address (got {receive_address!r})"
        )
    url = f"{TRONGRID_API_BASE}/v1/accounts/{receive_address}/transactions/trc20"
    params = {
        "only_to": "true",
        "limit": str(limit),
        "contract_address": USDT_TRC20_CONTRACT,
    }
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            r = client.get(url, params=params)
            r.raise_for_status()
            body = r.json()
    except httpx.HTTPError as e:
        raise ChainWatcherError(f"TronGrid request failed: {e}") from e
    except ValueError as e:
        raise ChainWatcherError(f"TronGrid returned non-JSON: {e}") from e
    if not body.get("success"):
        raise ChainWatcherError(f"TronGrid reported failure: {body}")
    out: list[InboundUsdtTx] = []
    now_ms = int(time.time() * 1000)
    for item in body.get("data", []):
        try:
            tx_id = item["transaction_id"]
            from_addr = item.get("from", "") or ""
            to_addr = item.get("to", "") or ""
            value_raw = int(item.get("value", "0"))
            value_usdt = value_raw / 1e6
            block_ts = int(item.get("block_timestamp", now_ms))
            age = (now_ms - block_ts) / 1000.0
        except (KeyError, ValueError, TypeError):
            continue
        if value_usdt < min_value_usdt:
            continue
        if age < min_age_seconds:
            continue
        if max_age_seconds is not None and age > max_age_seconds:
            continue
        if expected_value_usdt is not None:
            tol = expected_value_usdt * (expected_value_tolerance_pct / 100.0)
            if abs(value_usdt - expected_value_usdt) > tol:
                continue
        out.append(InboundUsdtTx(
            tx_id=tx_id,
            from_addr=from_addr,
            to_addr=to_addr,
            value_usdt=value_usdt,
            block_timestamp_ms=block_ts,
            age_seconds=age,
        ))
    return out


def find_payment_for_borrow(
    receive_address: str,
    expected_value_usdt: float,
    *,
    max_age_seconds: float = 3600.0,
    min_age_seconds: float = 30.0,
    tolerance_pct: float = 5.0,
) -> Optional[InboundUsdtTx]:
    """Convenience: find the most recent inbound tx that matches `expected_value_usdt`
    within the given window. Returns None if no match.

    `min_age_seconds=30` skips txs less than 30s old (Tron finality is ~3s but
    we want to avoid front-running on chain reorgs).
    """
    candidates = list_inbound_usdt(
        receive_address,
        max_age_seconds=max_age_seconds,
        min_age_seconds=min_age_seconds,
        expected_value_usdt=expected_value_usdt,
        expected_value_tolerance_pct=tolerance_pct,
    )
    return candidates[0] if candidates else None


__all__ = [
    "list_inbound_usdt", "find_payment_for_borrow",
    "InboundUsdtTx", "ChainWatcherError",
    "USDT_TRC20_CONTRACT", "TRONGRID_API_BASE",
]
