"""The fixed summary template (contracts/summary-template.md, research J10).

Pure. It reads `DayFacts` only, which holds numbers, dates, closed-set codes and tickers, so
no text written by a model or the broker, and no attribution, can reach the output.
"""

from __future__ import annotations

from datetime import UTC
from decimal import ROUND_HALF_EVEN, Decimal

from trading_agent.journal.model import DayFacts

SUMMARY_VERSION = "0.1"
MAX_RULES = 8
MAX_SYMBOLS = 30
MAX_MISSED = 10
_CENT = Decimal("0.01")


def render(facts: DayFacts) -> str:
    """The day's summary, the fixed lines first (at most 2,000 characters in practice)."""
    d = facts.by_direction
    s = facts.order_statuses
    lines = [
        f"## {facts.day.isoformat()}",
        _equity_line(facts),
        "- Daily-loss breaker: " + ("triggered" if facts.breaker_triggered else "not triggered"),
        f"- Decisions: {facts.decisions} (buy {d.get('buy', 0)}, sell {d.get('sell', 0)}, "
        f"hold {d.get('hold', 0)}); approved {facts.approved}, rejected {facts.rejected}",
        "- Rejected by rule: " + _counts(facts.rejection_rules, MAX_RULES),
        f"- Orders: {facts.orders} submitted; filled {s.get('filled', 0)}, "
        f"partially filled {s.get('partially_filled', 0)}, expired {s.get('expired', 0)}, "
        f"rejected {s.get('rejected', 0)}, canceled {s.get('canceled', 0)}, "
        f"open {s.get('submitted', 0)}",
        "- Execution refusals: " + _counts(facts.refusals, MAX_RULES),
        f"- Stop-loss exits: {facts.stop_loss_triggers} triggers; "
        f"approved {facts.stop_loss_approved}, rejected {facts.stop_loss_rejected}",
        "- Decided symbols: " + _symbols(facts),
        "- Notes: " + _notes(facts),
    ]
    return "\n".join(lines) + "\n"


def _money(value: Decimal) -> str:
    return f"{value.quantize(_CENT, rounding=ROUND_HALF_EVEN):f}"


def _equity_line(facts: DayFacts) -> str:
    change = facts.equity_close - facts.equity_open
    if facts.equity_open == 0:
        pct = "n/a"
    else:
        pct = _money(change / facts.equity_open * 100) + "%"
    taken = ""
    if facts.close_taken_at is not None:
        taken = f" ({facts.close_taken_at.astimezone(UTC):%H:%M} UTC)"
    return (
        f"- Equity: {_money(facts.equity_open)} at the open, "
        f"{_money(facts.equity_close)} last recorded{taken}; "
        f"change {_money(change)} ({pct})"
    )


def _counts(counts: dict[str, int], limit: int) -> str:
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    if not items:
        return "none"
    shown = [f"{name} {n}" for name, n in items[:limit]]
    if len(items) > limit:
        shown.append(f"and {len(items) - limit} more")
    return ", ".join(shown)


def _symbols(facts: DayFacts) -> str:
    parts = list(facts.decided_symbols[:MAX_SYMBOLS])
    if len(facts.decided_symbols) > MAX_SYMBOLS:
        parts.append(f"and {len(facts.decided_symbols) - MAX_SYMBOLS} more")
    if facts.malformed_symbols:
        parts.append(f"{facts.malformed_symbols} malformed")
    return ", ".join(parts) or "none"


def _notes(facts: DayFacts) -> str:
    parts = []
    if facts.missed_sessions:
        dates = [m.isoformat() for m in facts.missed_sessions[:MAX_MISSED]]
        if len(facts.missed_sessions) > MAX_MISSED:
            dates.append(f"and {len(facts.missed_sessions) - MAX_MISSED} more")
        parts.append("missed sessions " + ", ".join(dates))
    if facts.unpriced_count:
        noun = "symbol" if facts.unpriced_count == 1 else "symbols"
        parts.append(f"{facts.unpriced_count} {noun} unpriced")
    return "; ".join(parts) or "none"
