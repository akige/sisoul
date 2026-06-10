"""Tests for the decentralized lending market (M2 · §80/§81).

Covers: offer index TTL/aging, uptime scoring, weighted routing, and the two
load-bearing claims —
  1. a single offline lender is a non-event (ages out, routing skips it)
  2. zero-reputation sybil offers can't capture routing even at price 0.
"""
from __future__ import annotations

from sisoul.friend.market import (
    LendOffer,
    OfferBook,
    route_best,
    score_offer,
)


def _offer(did, price=0.0, models=("claude-sonnet-4-6",), mode="strong-tie-auto"):
    return LendOffer(lender_did=did, models=list(models), price_usdt_per_1k=price, mode=mode)


# ── OfferBook: ingest / TTL / aging ────────────────────────────────────────


def test_ingest_and_live():
    book = OfferBook(ttl_sec=180)
    book.ingest(_offer("did:key:zA"), now_ts=1000)
    book.ingest(_offer("did:key:zB"), now_ts=1000)
    live = book.live_offers(now_ts=1000)
    assert {o.lender_did for o in live} == {"did:key:zA", "did:key:zB"}


def test_offline_lender_ages_out():
    """Core M2 claim: a lender that stops broadcasting silently drops out of
    routing — being offline is a non-event, not an error."""
    book = OfferBook(ttl_sec=180)
    book.ingest(_offer("did:key:zOnline"), now_ts=1000)
    book.ingest(_offer("did:key:zGoesOffline"), now_ts=1000)
    # online one keeps re-broadcasting; the other goes silent
    book.ingest(_offer("did:key:zOnline"), now_ts=1100)
    # 200s after the offline one's last beat (> 180 TTL)
    live = book.live_offers(now_ts=1200)
    assert {o.lender_did for o in live} == {"did:key:zOnline"}
    assert book.uptime_score("did:key:zGoesOffline", now_ts=1200) == 0.0


def test_latest_broadcast_wins():
    book = OfferBook(ttl_sec=180)
    book.ingest(_offer("did:key:zA", price=0.02), now_ts=1000)
    book.ingest(_offer("did:key:zA", price=0.01), now_ts=1050)
    live = book.live_offers(now_ts=1060)
    assert len(live) == 1
    assert live[0].price_usdt_per_1k == 0.01


def test_prune_removes_aged():
    book = OfferBook(ttl_sec=100)
    book.ingest(_offer("did:key:zA"), now_ts=1000)
    book.ingest(_offer("did:key:zB"), now_ts=1090)
    removed = book.prune(now_ts=1150)  # zA aged (150s), zB live (60s)
    assert removed == 1
    assert {o.lender_did for o in book.live_offers(now_ts=1150)} == {"did:key:zB"}


def test_uptime_rewards_consistent_rebroadcast():
    book = OfferBook(ttl_sec=180)
    # reliable lender: 6 beats
    for t in range(0, 6):
        book.ingest(_offer("did:key:zReliable"), now_ts=1000 + t * 10)
    # flaky lender: heard once, a while ago
    book.ingest(_offer("did:key:zFlaky"), now_ts=1000)
    now = 1060
    assert book.uptime_score("did:key:zReliable", now) > book.uptime_score("did:key:zFlaky", now)


# ── routing ────────────────────────────────────────────────────────────────


def test_route_filters_by_model():
    book = OfferBook()
    book.ingest(_offer("did:key:zA", models=["gpt-4o"]), now_ts=1000)
    book.ingest(_offer("did:key:zB", models=["claude-sonnet-4-6"]), now_ts=1000)
    routed = route_best(
        book, model="claude-sonnet-4-6",
        reputations={"did:key:zA": 0.5, "did:key:zB": 0.5}, now_ts=1000,
    )
    assert [r.offer.lender_did for r in routed] == ["did:key:zB"]


def test_route_empty_when_nobody_available():
    """Empty result = retry/queue later, NOT an error."""
    book = OfferBook()
    routed = route_best(book, model="claude-sonnet-4-6", reputations={}, now_ts=1000)
    assert routed == []


def test_route_prefers_higher_reputation():
    book = OfferBook()
    book.ingest(_offer("did:key:zHigh", price=0.01), now_ts=1000)
    book.ingest(_offer("did:key:zLow", price=0.01), now_ts=1000)
    routed = route_best(
        book, model="claude-sonnet-4-6",
        reputations={"did:key:zHigh": 0.9, "did:key:zLow": 0.1}, now_ts=1000,
    )
    assert routed[0].offer.lender_did == "did:key:zHigh"


def test_route_budget_filters_pricey():
    book = OfferBook()
    book.ingest(_offer("did:key:zCheap", price=0.01), now_ts=1000)
    book.ingest(_offer("did:key:zPricey", price=0.10), now_ts=1000)
    routed = route_best(
        book, model="claude-sonnet-4-6",
        reputations={"did:key:zCheap": 0.5, "did:key:zPricey": 0.5},
        now_ts=1000, max_price_usdt_per_1k=0.05,
    )
    assert [r.offer.lender_did for r in routed] == ["did:key:zCheap"]


def test_route_min_reputation_floor_blocks_sybil():
    book = OfferBook()
    book.ingest(_offer("did:key:zSybil", price=0.0), now_ts=1000)
    routed = route_best(
        book, model="claude-sonnet-4-6",
        reputations={"did:key:zSybil": 0.0}, now_ts=1000,
        min_reputation=0.01,
    )
    assert routed == []


def test_sybil_free_offer_cannot_outrank_reputable_paid():
    """Load-bearing anti-sybil claim for routing: a zero-reputation lender
    offering for FREE still loses to a reputable lender charging money."""
    book = OfferBook(ttl_sec=180)
    # reputable lender, reliably online, charges 0.02
    for t in range(6):
        book.ingest(_offer("did:key:zTrusted", price=0.02), now_ts=1000 + t * 10)
    # sybil: free, just appeared
    book.ingest(_offer("did:key:zSybil", price=0.0), now_ts=1060)
    routed = route_best(
        book, model="claude-sonnet-4-6",
        reputations={"did:key:zTrusted": 0.95, "did:key:zSybil": 0.0},
        now_ts=1060,
    )
    assert routed[0].offer.lender_did == "did:key:zTrusted", (
        f"sybil free offer captured routing: {[(r.offer.lender_did, r.score) for r in routed]}"
    )


def test_score_multiplicative_zero_rep_tanks():
    """A near-zero reputation tanks the score regardless of free price."""
    s_sybil = score_offer(_offer("z", price=0.0), reputation=1e-9, uptime=1.0, max_price=0.05)
    s_good = score_offer(_offer("z", price=0.05), reputation=0.9, uptime=0.8, max_price=0.05)
    assert s_good > s_sybil


def test_route_excludes_self():
    book = OfferBook()
    book.ingest(_offer("did:key:zMe"), now_ts=1000)
    book.ingest(_offer("did:key:zOther"), now_ts=1000)
    routed = route_best(
        book, model="claude-sonnet-4-6",
        reputations={"did:key:zMe": 0.9, "did:key:zOther": 0.5}, now_ts=1000,
        exclude_dids={"did:key:zMe"},
    )
    assert [r.offer.lender_did for r in routed] == ["did:key:zOther"]


# ── offer (de)serialization ────────────────────────────────────────────────


def test_offer_roundtrip():
    o = LendOffer(
        lender_did="did:key:zA", models=["claude-sonnet-4-6", "gpt-4o"],
        price_usdt_per_1k=0.015, mode="per-request", daily_cap_tokens=1_000_000,
        note="alpha test", issued_at=1234.5,
    )
    o2 = LendOffer.from_dict(o.to_dict())
    assert o2 == o


# ── GossipSub e2e (MemoryTransport: broadcast → index → route) ─────────────

import asyncio
import pytest
from sisoul.chat.transport import MemoryTransport
from sisoul.friend.market import publish_offer, subscribe_offers


@pytest.mark.asyncio
async def test_publish_subscribe_roundtrip():
    """Lender publishes an offer; another node hears it off the topic, indexes
    it, and routes to it — the full wire path on MemoryTransport."""
    bus = MemoryTransport()
    book = OfferBook(ttl_sec=180)

    heard: list = []
    async def _listen():
        async for offer in subscribe_offers(bus):
            heard.append(offer)
            break  # one offer is enough for this test

    listener = asyncio.create_task(_listen())
    await asyncio.sleep(0.05)  # let the subscription attach

    await publish_offer(bus, _offer("did:key:zLender", price=0.01))
    await asyncio.wait_for(listener, timeout=2.0)

    assert len(heard) == 1
    assert heard[0].lender_did == "did:key:zLender"

    # index it and route
    book.ingest(heard[0], now_ts=1000)
    routed = route_best(
        book, model="claude-sonnet-4-6",
        reputations={"did:key:zLender": 0.7}, now_ts=1000,
    )
    assert routed and routed[0].offer.lender_did == "did:key:zLender"


@pytest.mark.asyncio
async def test_malformed_frame_skipped():
    """A non-offer frame on the topic doesn't break the subscriber."""
    from sisoul.chat.transport import WireEnvelope
    bus = MemoryTransport()

    heard: list = []
    async def _listen():
        async for offer in subscribe_offers(bus):
            heard.append(offer)
            break
    listener = asyncio.create_task(_listen())
    await asyncio.sleep(0.05)

    # garbage frame (wrong kind) then a real offer
    await bus.publish("/sisoul/market/v1", WireEnvelope(kind="not-an-offer", body={}).to_bytes())
    await publish_offer(bus, _offer("did:key:zReal"))
    await asyncio.wait_for(listener, timeout=2.0)

    assert [o.lender_did for o in heard] == ["did:key:zReal"]
