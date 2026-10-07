"""The Opportunistic Identifier's market-data port (specs/011-opportunistic-identifier
contracts/ports.md).

The only way the agent reaches the network for data. Numbers are Decimal, or None when
absent or unusable: never zero. Nothing here writes or trades. `to_reference()` and
`to_metrics()` give the feature 004 types `reference.normalize.normalize` accepts, so
the agent derives a name's market cap and dollar volume exactly as the reference-data
job does (research O4).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from trading_agent.reference import provider as ref
from trading_agent.reference.provider import (
    KeyRejected,
    NotPermitted,
    ProviderError,
    ProviderUnavailable,
    Quote,
    RateLimited,
)

__all__ = [
    "CompanyProfile",
    "Fundamentals",
    "KeyRejected",
    "Listing",
    "MarketData",
    "NotPermitted",
    "ProviderError",
    "ProviderUnavailable",
    "Quote",
    "RateLimited",
]


@dataclass(frozen=True)
class Listing:
    symbol: str
    type: str | None
    mic: str | None
    description: str | None  # the company name, cut to 100 characters
    conflicting: bool = False

    def to_reference(self) -> ref.Listing:
        return ref.Listing(self.symbol, self.type, self.mic, conflicting=self.conflicting)


@dataclass(frozen=True)
class CompanyProfile:
    symbol: str
    market_cap_millions: Decimal | None
    currency: str | None
    industry: str | None  # Finnhub's `finnhubIndustry`, cut to 100 characters

    def to_reference(self) -> ref.Profile:
        return ref.Profile(self.symbol, self.market_cap_millions, self.currency)


@dataclass(frozen=True)
class Fundamentals:
    """The `/stock/metric` keys the agent uses (data-model.md "Fundamentals sent to the
    model"). Key names beyond the first two are confirmed by the owner's `--check` run."""

    symbol: str
    avg_volume_10d_millions: Decimal | None = None  # 10DayAverageTradingVolume
    high_52w: Decimal | None = None  # 52WeekHigh
    low_52w: Decimal | None = None  # 52WeekLow
    pe_ttm: Decimal | None = None  # peTTM
    pb: Decimal | None = None  # pbQuarterly
    ps_ttm: Decimal | None = None  # psTTM
    gross_margin_ttm: Decimal | None = None  # grossMarginTTM
    operating_margin_ttm: Decimal | None = None  # operatingMarginTTM
    net_margin_ttm: Decimal | None = None  # netProfitMarginTTM
    roe_ttm: Decimal | None = None  # roeTTM
    revenue_growth_ttm_yoy: Decimal | None = None  # revenueGrowthTTMYoy
    eps_growth_ttm_yoy: Decimal | None = None  # epsGrowthTTMYoy
    debt_to_equity: Decimal | None = None  # totalDebt/totalEquityQuarterly
    dividend_yield: Decimal | None = None  # currentDividendYieldTTM
    beta: Decimal | None = None  # beta

    def to_metrics(self) -> ref.Metrics:
        return ref.Metrics(self.symbol, self.avg_volume_10d_millions)


class MarketData(Protocol):
    def us_listings(self) -> dict[str, Listing]: ...

    def quote(self, symbol: str) -> Quote: ...

    def profile(self, symbol: str) -> CompanyProfile: ...

    def fundamentals(self, symbol: str) -> Fundamentals: ...
