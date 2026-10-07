"""The port's types convert to exactly what `reference.normalize.normalize` accepts, and
the adapter asks for the exchanges the gate allows (research O4)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_agent.opportunistic_identifier import finnhub, ports
from trading_agent.reference import provider as ref
from trading_agent.reference.normalize import ReferenceRow, normalize
from trading_agent.risk.rules import US_LISTED_MICS

NOW = datetime(2026, 10, 8, 15, 0, tzinfo=UTC)
QUOTE = ref.Quote("AAPL", Decimal("201"), Decimal("200"), datetime(2026, 10, 8, 14, 55, tzinfo=UTC))


def test_to_reference_gives_the_reference_types_normalize_expects():
    listing = ports.Listing("AAPL", "Common Stock", "XNGS", "APPLE INC")
    profile = ports.CompanyProfile("AAPL", Decimal("3000000"), "USD", "Technology")
    metrics = ports.Fundamentals("AAPL", avg_volume_10d_millions=Decimal("25"), pe_ttm=Decimal(9))

    assert listing.to_reference() == ref.Listing("AAPL", "Common Stock", "XNGS")
    assert profile.to_reference() == ref.Profile("AAPL", Decimal("3000000"), "USD")
    assert metrics.to_metrics() == ref.Metrics("AAPL", Decimal("25"))
    row = normalize(
        "AAPL", listing.to_reference(), profile.to_reference(), QUOTE, metrics.to_metrics(), NOW
    )
    assert isinstance(row, ReferenceRow) and row.security_type == "common_stock"


def test_a_conflicting_listing_stays_conflicting_through_to_reference():
    listing = ports.Listing("DUAL", None, None, None, conflicting=True)
    assert listing.to_reference().conflicting


def test_the_symbol_list_asks_for_exactly_the_gates_exchanges():
    assert finnhub.SYMBOL_LIST_MICS == tuple(sorted(US_LISTED_MICS))
