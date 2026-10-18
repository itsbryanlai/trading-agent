"""A fake store for the Assistant: canned rows per menu entry, recorded calls (specs/013 T010)."""

from __future__ import annotations

from trading_agent.assistant.menu import QueryResult, ValidChoice
from trading_agent.assistant.store import StoreError


class FakeStore:
    def __init__(
        self,
        known: set[str] | None = None,
        rows: dict[str, list[dict]] | None = None,
        *,
        cut: set[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.known = set(known if known is not None else {"MU", "AAPL"})
        self.rows = rows or {}
        self.cut = cut or set()
        self.error = error
        self.known_calls = 0
        self.question_calls: list[set[str]] = []  # the symbols of each read_question
        self.run_calls: list[list[ValidChoice]] = []

    def known_symbols(self) -> set[str]:
        self.known_calls += 1
        if self.error is not None:
            raise self.error
        return set(self.known)

    def read_question(self, symbols, validate):
        """The lookup, the validation and the reads as one call (one transaction)."""
        self.question_calls.append(set(symbols))
        if self.error is not None:
            raise self.error
        valid, dropped = validate(self.known & set(symbols))
        database = [c for c in valid if c.name != "settings"]
        return valid, dropped, self.run(database) if database else []

    def run(self, choices: list[ValidChoice]) -> list[QueryResult]:
        self.run_calls.append(list(choices))
        if self.error is not None:
            raise self.error
        return [
            QueryResult(
                c.name,
                {k: getattr(c, k) for k in ("symbol", "agent", "section") if getattr(c, k)},
                list(self.rows.get(c.name, [])),
                c.name in self.cut,
            )
            for c in choices
        ]


def failing() -> FakeStore:
    return FakeStore(error=StoreError("database: OperationalError"))
