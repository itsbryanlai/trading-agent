"""A scripted stand-in for the market-data provider. Tests never reach Finnhub
(spec FR-026); the suite-wide guard in tests/conftest.py enforces it.

Script values per symbol, inject any ProviderError from any call (optionally only
after N successful calls), and read `calls` to assert pacing and that nothing is
fetched twice.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from trading_agent.reference.provider import (
    Listing,
    Metrics,
    Profile,
    ProviderError,
    Quote,
)


class FakeMarketData:
    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self.listings: dict[str, Listing] = {}
        self.profiles: dict[str, Decimal | None] = {}
        self.quotes: dict[str, Decimal | None] = {}
        self.metrics: dict[str, Decimal | None] = {}
        self.calls: list[tuple[float, str, str | None]] = []
        self._clock = clock or (lambda: 0.0)
        # (call, symbol or None for any) -> [error, successes still allowed first]
        self._failures: dict[tuple[str, str | None], list] = {}

    # --- scripting -------------------------------------------------------------

    def add(
        self,
        symbol: str,
        *,
        type: str | None = "Common Stock",
        mic: str | None = "XNGS",
        market_cap_millions="3000000",
        previous_close="200",
        avg_volume_10d_millions="25",
        listed: bool = True,
    ) -> None:
        """A symbol with sane values by default; pass None for a missing field."""
        if listed:
            self.listings[symbol] = Listing(symbol, type, mic)
        self.profiles[symbol] = _dec(market_cap_millions)
        self.quotes[symbol] = _dec(previous_close)
        self.metrics[symbol] = _dec(avg_volume_10d_millions)

    def fail(self, call: str, symbol: str | None = None, *, error: ProviderError, after: int = 0):
        """Make `call` raise `error` (for `symbol`, or any symbol) after `after` successes."""
        self._failures[(call, symbol)] = [error, after]

    def recover(self, call: str, symbol: str | None = None) -> None:
        self._failures.pop((call, symbol), None)

    def calls_for(self, symbol: str) -> list[str]:
        return [call for _, call, s in self.calls if s == symbol]

    # --- the port ----------------------------------------------------------------

    def list_us_symbols(self) -> dict[str, Listing]:
        self._record("list_us_symbols", None)
        return dict(self.listings)

    def get_profile(self, symbol: str) -> Profile:
        self._record("get_profile", symbol)
        return Profile(symbol, self.profiles.get(symbol))

    def get_quote(self, symbol: str) -> Quote:
        self._record("get_quote", symbol)
        return Quote(symbol, self.quotes.get(symbol))

    def get_metrics(self, symbol: str) -> Metrics:
        self._record("get_metrics", symbol)
        return Metrics(symbol, self.metrics.get(symbol))

    def _record(self, call: str, symbol: str | None) -> None:
        self.calls.append((self._clock(), call, symbol))
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
