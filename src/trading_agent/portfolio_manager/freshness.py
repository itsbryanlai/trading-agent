"""When a quote is fresh (specs/008-portfolio-manager research P4; FR-003). Pure.

A quote is fresh when its price is positive, its trade time is set, is at or after
today's open and no more than 60 seconds ahead of `now` (clock skew), and is at most
`max_age` old. `now` is the time the quote was *fetched*, not the run's start
(analyze F1): the caller reads its clock as each quote arrives. This module never
reads the clock itself.
"""

from __future__ import annotations

from datetime import timedelta

from trading_agent.reference.provider import Quote
from trading_agent.risk import calendar

QUOTE_MISSING = "quote_missing"
QUOTE_STALE = "quote_stale"
CLOCK_SKEW = timedelta(seconds=60)


def is_fresh(quote: Quote, now, max_age: timedelta) -> bool:
    if quote.current is None or quote.current <= 0 or quote.timestamp is None:
        return False
    today = calendar.trading_day(now)
    if not calendar.is_session(today) or quote.timestamp < calendar.open_time(today):
        return False
    if quote.timestamp > now + CLOCK_SKEW:
        return False
    return now - quote.timestamp <= max_age


def staleness_reason(quote: Quote | None, now, max_age: timedelta) -> str | None:
    """None when the quote is fresh; `quote_missing` when there is none (the fetch
    failed or the phase ran out of time); otherwise `quote_stale`."""
    if quote is None:
        return QUOTE_MISSING
    return None if is_fresh(quote, now, max_age) else QUOTE_STALE
