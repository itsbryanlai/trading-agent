"""A scripted stand-in for the Opportunistic Identifier's market-data port. Tests never
reach Finnhub; the suite-wide guard in tests/conftest.py enforces it.

Script listings, quotes, profiles and fundamentals per symbol, inject any
`reference.provider` error from any call (optionally after N successful calls), and read
`calls` as `(method, symbol)` pairs to assert call order and that a stale quote skips the
other two per-name calls. Modeled on tests/fakes/market_data.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_agent.opportunistic_identifier.ports import (
    CompanyProfile,
    Fundamentals,
    Listing,
    ProviderError,
    Quote,
)

# Thursday 2026-10-08, 10:55 ET: five minutes before the default test clock (11:00 ET).
DEFAULT_QUOTE_TIME = datetime(2026, 10, 8, 14, 55, tzinfo=UTC)

# A complete, plausible set of fundamentals; `add(..., pe_ttm=None)` removes one.
DEFAULT_FUNDAMENTALS = {
    "avg_volume_10d_millions": "25",
    "high_52w": "250",
    "low_52w": "150",
    "pe_ttm": "20",
    "pb": "3",
    "ps_ttm": "4",
    "gross_margin_ttm": "45",
    "operating_margin_ttm": "20",
    "net_margin_ttm": "15",
    "roe_ttm": "18",
    "revenue_growth_ttm_yoy": "8",
    "eps_growth_ttm_yoy": "10",
    "debt_to_equity": "0.5",
    "dividend_yield": "1.2",
    "beta": "1.1",
}


class FakeOIMarketData:
    def __init__(self, quote_time: datetime | None = DEFAULT_QUOTE_TIME) -> None:
        self.listings: dict[str, Listing] = {}
        self.profiles: dict[str, CompanyProfile] = {}
        self.quotes: dict[str, Quote] = {}
        self.fundamentals_by_symbol: dict[str, Fundamentals] = {}
        # Used for every quote added without its own time; change it to move a test day.
        self.quote_time = quote_time
        self.calls: list[tuple[str, str | None]] = []
        # (call, symbol or None for any) -> [error, successes still allowed first]
        self._failures: dict[tuple[str, str | None], list] = {}

    # --- scripting -------------------------------------------------------------

    def add(
        self,
        symbol: str,
        *,
        type: str | None = "Common Stock",
        mic: str | None = "XNGS",
        description: str | None = "ACME CORP",
        market_cap_millions="3000000",
        currency: str | None = "USD",
        industry: str | None = "Software",
        previous_close="200",
        current="201",
        quote_time: datetime | None | str = "default",
        listed: bool = True,
        **fundamentals,
    ) -> None:
        """A symbol with sane values by default; pass None for a missing field."""
        unknown = set(fundamentals) - set(DEFAULT_FUNDAMENTALS)
        if unknown:
            raise TypeError(f"unknown fundamentals: {sorted(unknown)}")
        if listed:
            self.listings[symbol] = Listing(symbol, type, mic, description)
        self.profiles[symbol] = CompanyProfile(
            symbol, _dec(market_cap_millions), currency, industry
        )
        when = self.quote_time if quote_time == "default" else quote_time
        self.quotes[symbol] = Quote(symbol, _dec(current), _dec(previous_close), when)
        values = {**DEFAULT_FUNDAMENTALS, **fundamentals}
        self.fundamentals_by_symbol[symbol] = Fundamentals(
            symbol, **{key: _dec(value) for key, value in values.items()}
        )

    def fail(self, call: str, symbol: str | None = None, *, error: ProviderError, after: int = 0):
        """Make `call` raise `error` (for `symbol`, or any symbol) after `after` successes."""
        self._failures[(call, symbol)] = [error, after]

    def recover(self, call: str, symbol: str | None = None) -> None:
        self._failures.pop((call, symbol), None)

    def calls_for(self, symbol: str) -> list[str]:
        return [call for call, s in self.calls if s == symbol]

    # --- the port ----------------------------------------------------------------

    def us_listings(self) -> dict[str, Listing]:
        self._record("us_listings", None)
        return dict(self.listings)

    def quote(self, symbol: str) -> Quote:
        self._record("quote", symbol)
        return self.quotes.get(symbol) or Quote(symbol, None, None, None)

    def profile(self, symbol: str) -> CompanyProfile:
        self._record("profile", symbol)
        return self.profiles.get(symbol) or CompanyProfile(symbol, None, None, None)

    def fundamentals(self, symbol: str) -> Fundamentals:
        self._record("fundamentals", symbol)
        return self.fundamentals_by_symbol.get(symbol) or Fundamentals(symbol)

    def _record(self, call: str, symbol: str | None) -> None:
        self.calls.append((call, symbol))
        for key in ((call, symbol), (call, None)):
            failure = self._failures.get(key)
            if failure is None:
                continue
            if failure[1] > 0:
                failure[1] -= 1
                continue
            raise failure[0]


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))
