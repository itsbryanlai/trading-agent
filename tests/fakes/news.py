"""A stand-in news source for Research's tests: no network, recorded calls."""

from __future__ import annotations

from datetime import date

from trading_agent.research.ports import RawArticle


class FakeNews:
    def __init__(
        self,
        general: list[RawArticle] | None = None,
        by_symbol: dict[str, list[RawArticle]] | None = None,
        symbols: dict[str, str] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.general = general or []
        self.by_symbol = by_symbol or {}
        self.symbols = (
            symbols
            if symbols is not None
            else {"AAPL": "APPLE INC", "MSFT": "MICROSOFT CORP", "NVDA": "NVIDIA CORP"}
        )
        # Keys: "general", "symbols", or a ticker for its company news.
        self.errors = errors or {}
        self.calls: list[tuple] = []

    def _maybe_raise(self, key: str) -> None:
        if key in self.errors:
            raise self.errors[key]

    def general_news(self) -> list[RawArticle]:
        self.calls.append(("general",))
        self._maybe_raise("general")
        return list(self.general)

    def company_news(self, symbol: str, start: date, end: date) -> list[RawArticle]:
        self.calls.append(("company", symbol, start, end))
        self._maybe_raise(symbol)
        return list(self.by_symbol.get(symbol, []))

    def us_symbols(self) -> dict[str, str]:
        self.calls.append(("symbols",))
        self._maybe_raise("symbols")
        return dict(self.symbols)
