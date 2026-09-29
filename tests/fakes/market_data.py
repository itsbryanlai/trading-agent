"""A scripted stand-in for the market-data provider. Tests never reach Finnhub
(spec FR-026); the suite-wide guard in tests/conftest.py enforces it.

Script values per symbol, inject any ProviderError from any call (optionally only
after N successful calls), and read `calls` to assert pacing and that nothing is
fetched twice.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from trading_agent.reference.provider import (
    Listing,
    Metrics,
    Profile,
    ProviderError,
    Quote,
)

# Monday 2026-09-28 07:00 ET: a quote already rolled over to the test day, so its
# `pc` is Friday's close (research D2, adversarial review H1).
DEFAULT_QUOTE_TIME = datetime(2026, 9, 28, 11, 0, tzinfo=UTC)


class FakeMarketData:
    def __init__(
        self,
        clock: Callable[[], float] | None = None,
        quote_time: datetime | None = DEFAULT_QUOTE_TIME,
    ) -> None:
        self.listings: dict[str, Listing] = {}
        self.profiles: dict[str, Profile] = {}
        self.metrics: dict[str, Decimal | None] = {}
        # symbol -> (current, previous close, quote time or "default")
        self._quote_args: dict[str, tuple] = {}
        # Used for every quote added without its own time; change it to move a test day.
        self.quote_time = quote_time
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
        current="201",
        quote_time: datetime | None | str = "default",
        currency: str | None = "USD",
        avg_volume_10d_millions="25",
        listed: bool = True,
    ) -> None:
        """A symbol with sane values by default; pass None for a missing field."""
        if listed:
            self.listings[symbol] = Listing(symbol, type, mic)
        self.profiles[symbol] = Profile(symbol, _dec(market_cap_millions), currency)
        self._quote_args[symbol] = (_dec(current), _dec(previous_close), quote_time)
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
        return self.profiles.get(symbol) or Profile(symbol, None, None)

    def get_quote(self, symbol: str) -> Quote:
        self._record("get_quote", symbol)
        current, previous, when = self._quote_args.get(symbol, (None, None, None))
        when = self.quote_time if when == "default" else when
        return Quote(symbol, current, previous, when)

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
