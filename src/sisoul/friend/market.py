"""Decentralized LLM lending market — offer broadcast + weighted routing (M2 · §80/§81).

No central listing server. Lenders broadcast signed offers on a shared
GossipSub topic; every node keeps a local index of live offers it has heard.
A borrower picks a lender by scoring live offers on price × reputation ×
uptime — single offline lender is a non-event (just another stale entry that
ages out), which is how M2 removes the "both must be online" coupling.

Topic
-----
``/sisoul/market/v1`` — global offer board (all lenders broadcast here)

Offer lifecycle
---------------
- lender re-broadcasts its offer every ``OFFER_REFRESH_SEC``
- a heard offer is "live" for ``OFFER_TTL_SEC`` since last seen, then ages out
- borrower routes only over live offers, never over stale/aged-out ones

Reputation feeds in from ``reputation.compute_reputation`` (EigenTrust), so a
zero-rep newcomer offer ranks near the bottom even at price 0 — sybil offers
can't capture routing.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from math import log
from typing import Any, Optional, AsyncIterator

MARKET_TOPIC = "/sisoul/market/v1"

OFFER_REFRESH_SEC = 60.0   # lender re-broadcast cadence
OFFER_TTL_SEC = 180.0      # a heard offer is live this long since last seen


# ── offer ─────────────────────────────────────────────────────────────────


@dataclass
class LendOffer:
    """A lender's standing offer, broadcast on the market topic.

    price_usdt_per_1k: 0.0 == gift (free). reputation/uptime are NOT trusted
    from the offer itself — the borrower recomputes reputation locally from
    on-chain reviews and tracks uptime from how reliably it hears this offer.
    """

    lender_did: str
    models: list[str]
    price_usdt_per_1k: float = 0.0
    mode: str = "strong-tie-auto"  # strong-tie-auto | per-request
    daily_cap_tokens: int = 0      # 0 == unlimited (lender's own risk)
    note: str = ""
    issued_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LendOffer":
        return cls(
            lender_did=str(d.get("lender_did", "")),
            models=list(d.get("models", []) or []),
            price_usdt_per_1k=float(d.get("price_usdt_per_1k", 0.0) or 0.0),
            mode=str(d.get("mode", "strong-tie-auto")),
            daily_cap_tokens=int(d.get("daily_cap_tokens", 0) or 0),
            note=str(d.get("note", "")),
            issued_at=float(d.get("issued_at", 0.0) or 0.0),
        )


@dataclass
class _HeardOffer:
    """An offer in the local index, with the wall-clock we last heard it."""

    offer: LendOffer
    last_seen: float
    seen_count: int = 1


# ── local offer index ────────────────────────────────────────────────────


class OfferBook:
    """Local index of live offers heard off the market topic.

    Pure data structure — no I/O, no clock of its own (callers pass now_ts so
    tests are deterministic). The daemon's market loop feeds it; routing reads
    it.
    """

    def __init__(self, ttl_sec: float = OFFER_TTL_SEC) -> None:
        self.ttl = float(ttl_sec)
        self._by_lender: dict[str, _HeardOffer] = {}

    def ingest(self, offer: LendOffer, now_ts: float) -> None:
        """Record/refresh an offer. Latest broadcast per lender wins."""
        prev = self._by_lender.get(offer.lender_did)
        self._by_lender[offer.lender_did] = _HeardOffer(
            offer=offer,
            last_seen=now_ts,
            seen_count=(prev.seen_count + 1) if prev else 1,
        )

    def live_offers(self, now_ts: float) -> list[LendOffer]:
        """Offers heard within TTL (aged-out lenders silently drop out)."""
        return [
            h.offer
            for h in self._by_lender.values()
            if now_ts - h.last_seen <= self.ttl
        ]

    def uptime_score(self, lender_did: str, now_ts: float) -> float:
        """Cheap uptime proxy in [0,1]: have we heard this lender recently, and
        how many times. A lender that re-broadcasts reliably scores high; one we
        heard once long ago scores low. Not gameable for routing on its own
        (reputation dominates), just breaks ties toward reliably-online peers."""
        h = self._by_lender.get(lender_did)
        if h is None:
            return 0.0
        age = now_ts - h.last_seen
        if age > self.ttl:
            return 0.0
        freshness = 1.0 - (age / self.ttl)            # 1.0 just heard → 0 at TTL
        consistency = min(1.0, h.seen_count / 5.0)    # 5+ broadcasts → full
        return 0.5 * freshness + 0.5 * consistency

    def prune(self, now_ts: float) -> int:
        """Drop aged-out offers. Returns count removed."""
        dead = [
            did for did, h in self._by_lender.items()
            if now_ts - h.last_seen > self.ttl
        ]
        for did in dead:
            del self._by_lender[did]
        return len(dead)


# ── routing ─────────────────────────────────────────────────────────────


@dataclass
class RoutedOffer:
    offer: LendOffer
    score: float
    reputation: float
    uptime: float
    price_factor: float


def score_offer(
    offer: LendOffer,
    *,
    reputation: float,
    uptime: float,
    max_price: float,
) -> float:
    """Routing score in [0,1]-ish. Higher = better pick.

    score = reputation^w_rep × uptime^w_up × price_factor^w_price

    Multiplicative so any near-zero factor tanks the offer (a 0-reputation
    sybil at price 0 still loses). price_factor rewards cheaper offers but
    never lets price alone win against reputation.
    """
    W_REP, W_UP, W_PRICE = 0.55, 0.20, 0.25
    # price_factor: 1.0 at free, decays toward 0 as price → max_price.
    if max_price <= 0:
        price_factor = 1.0
    else:
        price_factor = max(0.0, 1.0 - (offer.price_usdt_per_1k / max_price))
        price_factor = 0.05 + 0.95 * price_factor  # never fully zero on price
    rep = max(1e-9, reputation)
    up = max(1e-9, uptime)
    return (rep ** W_REP) * (up ** W_UP) * (price_factor ** W_PRICE)


def route_best(
    book: OfferBook,
    *,
    model: str,
    reputations: dict[str, float],
    now_ts: float,
    max_price_usdt_per_1k: Optional[float] = None,
    mode: Optional[str] = None,
    min_reputation: float = 0.0,
    exclude_dids: Optional[set[str]] = None,
) -> list[RoutedOffer]:
    """Rank live offers serving ``model`` by price × reputation × uptime.

    Args:
        reputations: {did: score} from reputation.compute_reputation.
        max_price_usdt_per_1k: borrower's budget ceiling (None = no cap; filters
            out pricier offers; also the normaliser for price_factor).
        mode: require a specific lend mode (None = any).
        min_reputation: drop offers below this reputation (sybil floor).
        exclude_dids: never route to these (e.g. self, blocklist).

    Returns offers sorted best-first. Empty list = nobody available right now
    (borrower can retry/queue async) — NOT an error.
    """
    exclude = exclude_dids or set()
    live = book.live_offers(now_ts)
    candidates: list[LendOffer] = []
    for o in live:
        if o.lender_did in exclude:
            continue
        if model not in o.models:
            continue
        if mode is not None and o.mode != mode:
            continue
        if max_price_usdt_per_1k is not None and o.price_usdt_per_1k > max_price_usdt_per_1k:
            continue
        if reputations.get(o.lender_did, 0.0) < min_reputation:
            continue
        candidates.append(o)

    if not candidates:
        return []

    # price normaliser: borrower budget if given, else the priciest candidate
    prices = [c.price_usdt_per_1k for c in candidates]
    max_price = max_price_usdt_per_1k if max_price_usdt_per_1k is not None else max(prices)

    routed: list[RoutedOffer] = []
    for o in candidates:
        rep = reputations.get(o.lender_did, 0.0)
        up = book.uptime_score(o.lender_did, now_ts)
        s = score_offer(o, reputation=rep, uptime=up, max_price=max_price)
        pf = 1.0 if max_price <= 0 else max(0.0, 1.0 - (o.price_usdt_per_1k / max_price))
        routed.append(RoutedOffer(offer=o, score=s, reputation=rep, uptime=up, price_factor=pf))

    routed.sort(key=lambda r: r.score, reverse=True)
    return routed


# ── wire (GossipSub publish/subscribe) ─────────────────────────────────────


async def publish_offer(transport: Any, offer: LendOffer) -> str:
    """Broadcast a lend offer on the market topic. Returns the topic."""
    from sisoul.chat.transport import WireEnvelope

    wire = WireEnvelope(kind="market-offer", body=offer.to_dict())
    await transport.publish(MARKET_TOPIC, wire.to_bytes())
    return MARKET_TOPIC


async def subscribe_offers(transport: Any) -> AsyncIterator[LendOffer]:
    """Yield LendOffer heard on the market topic. Skips malformed frames."""
    from sisoul.chat.transport import WireEnvelope

    gen = await transport.subscribe(MARKET_TOPIC)
    try:
        async for raw in gen:
            try:
                wire = WireEnvelope.from_bytes(raw)
                if wire.kind != "market-offer":
                    continue
                yield LendOffer.from_dict(wire.body)
            except Exception:
                continue
    finally:
        try:
            await gen.aclose()  # type: ignore[attr-defined]
        except Exception:
            pass


__all__ = [
    "MARKET_TOPIC",
    "OFFER_REFRESH_SEC",
    "OFFER_TTL_SEC",
    "LendOffer",
    "OfferBook",
    "RoutedOffer",
    "score_offer",
    "route_best",
    "publish_offer",
    "subscribe_offers",
]
