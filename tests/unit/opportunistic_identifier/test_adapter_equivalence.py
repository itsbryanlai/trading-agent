"""Adapter equivalence (specs/011 /speckit-analyze G1): identical Finnhub bodies through
`reference.finnhub.FinnhubProvider` and `OIFinnhub` give the same `normalize` result, so
the agent derives a name's row exactly as the reference-data job does (FR-006)."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from trading_agent.opportunistic_identifier.finnhub import OIFinnhub
from trading_agent.reference.finnhub import FinnhubProvider
from trading_agent.reference.normalize import Failure, ReferenceRow, normalize

NOW = datetime(2026, 10, 8, 15, 0, tzinfo=UTC)
FRESH = int(datetime(2026, 10, 8, 14, 55, tzinfo=UTC).timestamp())
STALE = int(datetime(2026, 10, 1, 14, 55, tzinfo=UTC).timestamp())
KEY = "fake-not-real"


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def opener_for(bodies: dict):
    """Routes by path; `/stock/symbol` is answered per `mic` from bodies['symbols'][mic]."""

    def opener(request, timeout):
        url = urlsplit(request.full_url)
        if url.path.endswith("/stock/symbol"):
            mic = parse_qs(url.query)["mic"][0]
            body = bodies["symbols"].get(mic, [])
        else:
            body = bodies[url.path.rsplit("/api/v1", 1)[-1]]
        return Response(json.dumps(body).encode())

    return opener


def good(**over) -> dict:
    bodies = {
        "symbols": {"XNAS": [{"symbol": "AAA", "type": "Common Stock", "mic": "XNGS"}]},
        "/stock/profile2": {"marketCapitalization": 3000000, "currency": "USD"},
        "/quote": {"c": 201, "pc": 200, "t": FRESH},
        "/stock/metric": {"metric": {"10DayAverageTradingVolume": 25.5}},
    }
    bodies.update(over)
    return bodies


CASES = {
    "plain": good(),
    "conflicting listing": good(
        symbols={
            "XNAS": [{"symbol": "AAA", "type": "Common Stock", "mic": "XNGS"}],
            "XNYS": [{"symbol": "AAA", "type": "Common Stock", "mic": "XNYS"}],
        }
    ),
    "non-USD market cap": good(
        **{"/stock/profile2": {"marketCapitalization": 3000000, "currency": "EUR"}}
    ),
    "zero volume": good(**{"/stock/metric": {"metric": {"10DayAverageTradingVolume": 0}}}),
    "missing volume": good(**{"/stock/metric": {"metric": {}}}),
    "zero market cap": good(**{"/stock/profile2": {"marketCapitalization": 0, "currency": "USD"}}),
    "stale quote": good(**{"/quote": {"c": 201, "pc": 200, "t": STALE}}),
    "no quote time": good(**{"/quote": {"c": 201, "pc": 200}}),
    "zero previous close": good(**{"/quote": {"c": 201, "pc": 0, "t": FRESH}}),
    "implausible dollar volume": good(
        **{"/stock/metric": {"metric": {"10DayAverageTradingVolume": 99999999}}}
    ),
    "no type": good(symbols={"XNAS": [{"symbol": "AAA", "mic": "XNGS"}]}),
    "not listed": good(symbols={}),
}


def _via_reference(bodies):
    p = FinnhubProvider(KEY, opener=opener_for(bodies))
    return normalize(
        "AAA", p.list_us_symbols().get("AAA"), p.get_profile("AAA"), p.get_quote("AAA"),
        p.get_metrics("AAA"), NOW,
    )  # fmt: skip


def _via_oi(bodies):
    p = OIFinnhub(KEY, opener=opener_for(bodies))
    listing = p.us_listings().get("AAA")
    return normalize(
        "AAA",
        listing.to_reference() if listing else None,
        p.profile("AAA").to_reference(),
        p.quote("AAA"),
        p.fundamentals("AAA").to_metrics(),
        NOW,
    )


@pytest.mark.parametrize("bodies", CASES.values(), ids=CASES.keys())
def test_both_adapters_give_normalize_the_same_row_or_failure(bodies):
    assert _via_oi(bodies) == _via_reference(bodies)


def test_the_cases_cover_both_a_row_and_several_distinct_failures():
    results = [_via_oi(bodies) for bodies in CASES.values()]
    assert sum(isinstance(r, ReferenceRow) for r in results) == 1
    assert len({r.reason for r in results if isinstance(r, Failure)}) >= 6
