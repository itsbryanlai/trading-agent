"""One agent's book, valued and updated at a close (research J5, J6).

Pure: prices, reports and the calendar's `previous_session` come in as arguments, and
nothing is read from the clock, the environment or a database. All arithmetic is `Decimal`.
Weights are percentages of the book. Rounding is not done here; `state.py` rounds once, when
the book is stored.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal

from trading_agent.journal.model import Book, BookResult, Holding, Price, ReportRow

HUNDRED = Decimal(100)
ZERO = Decimal(0)


def new_book(agent: str, day: date) -> Book:
    """An agent's first book: index 100 and no holdings, valued first on `day`."""
    return Book(agent, day, HUNDRED, {})


def sessions_since(a: date, b: date, previous_session: Callable[[date], date], limit: int) -> int:
    """Sessions `s` with `a < s <= b`, counted by stepping back from `b` at most `limit`
    times, so the answer never exceeds `limit` (research J6). `b` is a session."""
    count = 0
    current = b
    while count < limit and current > a:
        count += 1
        current = previous_session(current)
    return count


def advance(
    book: Book,
    reports: Sequence[ReportRow],
    prices: Mapping[str, Price],
    day: date,
    holding_sessions: int,
    previous_session: Callable[[date], date],
) -> BookResult:
    """Value the book at `day`'s close, drift its weights, apply the reports in order, exit
    stale holdings and scale to 100% (research J5 steps 1 to 6)."""
    holdings, day_return, unpriced = _value_and_drift(book.holdings, prices)
    holdings, skipped = _apply(holdings, reports, prices)
    exited_sell = tuple(sorted(s for s, h in holdings.items() if h.weight_pct == 0))
    stale = tuple(
        sorted(
            s
            for s, h in holdings.items()
            if h.weight_pct != 0
            and sessions_since(h.support_session, day, previous_session, holding_sessions)
            >= holding_sessions
        )
    )
    kept = {s: h for s, h in holdings.items() if s not in exited_sell and s not in stale}
    kept, scaled_by = _scale(kept)
    return BookResult(
        book=Book(book.agent, book.started_on, book.index * (1 + day_return), kept),
        day_return=day_return,
        scaled_by=scaled_by,
        late_reports=sum(1 for r in reports if r.late),
        unpriced=unpriced,
        skipped_targets=skipped,
        exited_sell=exited_sell,
        exited_holding_limit=stale,
    )


def _value_and_drift(
    holdings: Mapping[str, Holding], prices: Mapping[str, Price]
) -> tuple[dict[str, Holding], Decimal, tuple[str, ...]]:
    returns: dict[str, Decimal] = {}
    unpriced = []
    for symbol, h in holdings.items():
        close = _close(prices, symbol)
        if close is None:
            returns[symbol] = ZERO
            unpriced.append(symbol)
        else:
            returns[symbol] = close / h.ref_price - 1
    day_return = sum((h.weight_pct * returns[s] for s, h in holdings.items()), ZERO) / HUNDRED
    # R >= -1 because weights are non-negative and sum to at most 100, and a price is never
    # below zero (a zero price is unusable, so unpriced): 1 + R can't reach zero.
    growth = 1 + day_return
    drifted = {
        s: replace(
            h,
            weight_pct=h.weight_pct * (1 + returns[s]) / growth,
            ref_price=_close(prices, s) or h.ref_price,
        )
        for s, h in holdings.items()
    }
    return drifted, day_return, tuple(sorted(unpriced))


def _apply(
    holdings: dict[str, Holding], reports: Sequence[ReportRow], prices: Mapping[str, Price]
) -> tuple[dict[str, Holding], tuple[str, ...]]:
    held = dict(holdings)
    skipped: set[str] = set()
    for report in sorted(reports, key=lambda r: (r.generated_at, r.id)):
        symbol, size = report.symbol, report.suggested_size_pct
        if report.direction in ("buy", "hold") and size is not None:
            close = _close(prices, symbol)
            current = held.get(symbol)
            if close is None and current is None:
                skipped.add(symbol)
                continue
            held[symbol] = Holding(symbol, size, close or current.ref_price, report.support_session)
            skipped.discard(symbol)
        elif report.direction == "sell" and size is not None and symbol in held:
            current = held[symbol]
            held[symbol] = replace(current, weight_pct=min(current.weight_pct, size))
    return held, tuple(sorted(skipped))


def _scale(holdings: dict[str, Holding]) -> tuple[dict[str, Holding], Decimal | None]:
    total = sum((h.weight_pct for h in holdings.values()), ZERO)
    if total <= HUNDRED:
        return holdings, None
    factor = HUNDRED / total
    return {s: replace(h, weight_pct=h.weight_pct * factor) for s, h in holdings.items()}, factor


def _close(prices: Mapping[str, Price], symbol: str) -> Decimal | None:
    price = prices.get(symbol)
    return None if price is None else price.close
