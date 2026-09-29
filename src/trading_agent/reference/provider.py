"""The market-data port (specs/004-reference-data/contracts/market-data-port.md).

The only way the job talks to the provider. Values carry the provider's numbers
unconverted, as Decimal, or None when absent or unusable; unit conversion is
`normalize`'s job. Nothing here writes or trades: the provider can't, and the
port doesn't model it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


class ProviderError(Exception):
    pass


class KeyRejected(ProviderError):
    """The provider refused the key itself (401/403)."""


class NotPermitted(ProviderError):
    """403 on one symbol's request: the key's plan doesn't cover it. That symbol's
    failure only, never the whole run (adversarial review M1)."""


class RateLimited(ProviderError):
    """429: slow down and carry on next tick."""


class ProviderUnavailable(ProviderError):
    """Timeout, network or server error, or an unreadable response."""


@dataclass(frozen=True)
class Listing:
    symbol: str
    type: str | None
    mic: str | None
    # The US list named this symbol more than once, with different types or
    # exchanges: fail closed (adversarial review LOW).
    conflicting: bool = False


@dataclass(frozen=True)
class Profile:
    symbol: str
    market_cap_millions: Decimal | None
    currency: str | None  # the currency market cap is reported in (H2)


@dataclass(frozen=True)
class Quote:
    symbol: str
    current: Decimal | None  # `c`: the latest price
    previous_close: Decimal | None  # `pc`: the close before the latest price's session
    timestamp: datetime | None  # `t`: when `current` was set (H1)


@dataclass(frozen=True)
class Metrics:
    symbol: str
    avg_volume_10d_millions: Decimal | None


class MarketDataProvider(Protocol):
    def list_us_symbols(self) -> dict[str, Listing]: ...

    def get_profile(self, symbol: str) -> Profile: ...

    def get_quote(self, symbol: str) -> Quote: ...

    def get_metrics(self, symbol: str) -> Metrics: ...
