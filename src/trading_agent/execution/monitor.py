"""The stop-loss scan (ADR 0010, research E9, E13). Pure.

Compares each held position's last traded price, confirmed by its current bid
(ADR 0014), with its stop-loss line. It only
*finds* breaches; recording a trigger is the whole hand-off, and the Risk Gate
decides in its own process whether to approve an exit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from trading_agent.execution.broker import Quote, Trade
from trading_agent.execution.fills import Holding

# A trade older than one monitor window isn't a usable price: it could miss a
# real breach or invent one (research E9). Not a risk limit.
MAX_TRADE_AGE = timedelta(minutes=30)
# The confirming bid must be as live as a buy's ask (research E9, ADR 0014).
MAX_BID_AGE = timedelta(seconds=60)
_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class Breach:
    symbol: str
    price: Decimal
    line: Decimal


@dataclass(frozen=True)
class Scan:
    breaches: tuple[Breach, ...]
    stale: tuple[str, ...]  # a trade too old to judge by
    failed: tuple[str, ...]  # no trade, or no usable bid, could be fetched

    @property
    def complete(self) -> bool:
        """Every held position was actually checked (research E13, SC-005)."""
        return not self.stale and not self.failed


def stop_line(avg_entry_price: Decimal, stop_loss_pct: Decimal) -> Decimal:
    return avg_entry_price * (1 - stop_loss_pct / _HUNDRED)


def scan(
    holdings: dict[str, Holding],
    trades: dict[str, Trade | None],
    quotes: dict[str, Quote | None],
    stop_loss_pct: Decimal,
    now: datetime,
    skip: frozenset[str] = frozenset(),
) -> Scan:
    """A breach needs the last trade *and* a live bid at or below the line, so one
    odd print can't end a position (ADR 0014). `trades[symbol]` or `quotes[symbol]`
    is None when fetching it failed; a missing, zero or stale bid is a failed check,
    retried within the window. Symbols in `skip` already have an exit on its way (an
    open sell, an unevaluated trigger, or an approved exit not yet submitted)."""
    breaches, stale, failed = [], [], []
    for symbol in sorted(holdings):
        if symbol in skip:
            continue
        trade = trades.get(symbol)
        if trade is None:
            failed.append(symbol)
            continue
        if now - trade.timestamp > MAX_TRADE_AGE:
            stale.append(symbol)
            continue
        line = stop_line(holdings[symbol].avg_entry_price, stop_loss_pct)
        if trade.price > line:
            continue  # no breach; the bid isn't needed
        quote = quotes.get(symbol)
        if quote is None or quote.bid <= 0 or now - quote.timestamp > MAX_BID_AGE:
            failed.append(symbol)
            continue
        if quote.bid <= line:
            breaches.append(Breach(symbol, trade.price, line))
    return Scan(tuple(breaches), tuple(stale), tuple(failed))
