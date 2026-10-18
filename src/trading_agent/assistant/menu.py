"""The query menu and the validation of the plan call's choices (research A10;
contracts/query-menu.md).

Pure. The model picks entries by name; this module decides which choices may run. Nothing the
model wrote reaches SQL except through a `ValidChoice`: a known name, only the parameters that
entry takes, a known symbol, and a date span inside the limit. The reasons a choice is dropped
are A10's, checked in A10's order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SYMBOL_PATTERN = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
RESEARCH_AGENTS = ("research", "opportunistic_identifier")
RUN_AGENTS = ("research", "opportunistic_identifier", "portfolio_manager")
SECTIONS = ("risk", "schedule", "models")
DATES = ("from_date", "to_date")

REASONS = (
    "unknown_query",
    "unexpected_parameter",
    "missing_parameter",
    "unknown_symbol",
    "unknown_agent",
    "bad_dates",
    "unknown_section",
    "duplicate",
    "too_many",
)


@dataclass(frozen=True)
class RawChoice:
    """One choice as the plan call returned it: all six keys, `""` meaning not given."""

    name: str
    symbol: str = ""
    agent: str = ""
    from_date: str = ""
    to_date: str = ""
    section: str = ""


@dataclass(frozen=True)
class Entry:
    name: str
    takes: tuple[str, ...]
    returns: str
    required: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()
    sections: tuple[str, ...] = ()
    default_days: int | None = None  # days ending today when dates are not given; None: no dates


@dataclass(frozen=True)
class ValidChoice:
    name: str
    symbol: str
    agent: str
    section: str
    from_date: date | None
    to_date: date | None
    start: datetime | None  # UTC: the start of from_date in the owner's zone
    end: datetime | None  # UTC: the start of the day after to_date in the owner's zone


@dataclass(frozen=True)
class Dropped:
    name: str  # the entry's name when it is on the menu, else "unknown"
    reason: str


ENTRIES: dict[str, Entry] = {
    entry.name: entry
    for entry in (
        Entry(
            "portfolio_now",
            (),
            "positions, the latest account snapshot, trading_paused, the daily-loss halt "
            "and its baseline",
        ),
        Entry(
            "account_history",
            DATES,
            "account snapshots (equity, cash) in the date range",
            default_days=7,
        ),
        Entry(
            "decisions",
            ("symbol", *DATES),
            "Portfolio Manager decisions with their verdict, order status, fill, quote "
            "and cited reports",
            default_days=1,
        ),
        Entry(
            "reports",
            ("agent", "symbol", *DATES),
            "analyst reports (direction, conviction, size, status, rationale, sources)",
            agents=RESEARCH_AGENTS,
            default_days=1,
        ),
        Entry(
            "orders",
            ("symbol", *DATES),
            "orders with status and fills, execution refusals, and orders in flight",
            default_days=1,
        ),
        Entry(
            "stop_losses",
            ("symbol", *DATES),
            "stop-loss triggers with their verdict and order status",
            default_days=7,
        ),
        Entry(
            "journal",
            DATES,
            "per-session journal rows (equity open and close, summary, attribution)",
            default_days=10,
        ),
        Entry(
            "agent_runs",
            ("agent", *DATES),
            "orchestrator runs per agent (reason, slot, times, outcome)",
            agents=RUN_AGENTS,
            default_days=1,
        ),
        Entry(
            "universe",
            ("symbol",),
            "the latest instrument reference row for one symbol",
            required=("symbol",),
        ),
        Entry(
            "settings",
            ("section",),
            "the deployed risk, schedule and model settings",
            sections=SECTIONS,
        ),
    )
}


def validate(
    raw_choices,
    *,
    known_symbols,
    today_local: date,
    zone: ZoneInfo,
    max_span_days: int,
    max_queries: int,
) -> tuple[list[ValidChoice], list[Dropped]]:
    """Split the raw choices into those that may run and those dropped with a reason."""
    valid: list[ValidChoice] = []
    dropped: list[Dropped] = []
    for raw in raw_choices:
        entry = ENTRIES.get(raw.name) if isinstance(raw.name, str) else None
        label = entry.name if entry else "unknown"
        outcome = _check(raw, entry, known_symbols, today_local, zone, max_span_days)
        if isinstance(outcome, str):
            dropped.append(Dropped(label, outcome))
        elif outcome in valid:
            dropped.append(Dropped(label, "duplicate"))
        elif len(valid) >= max_queries:
            dropped.append(Dropped(label, "too_many"))
        else:
            valid.append(outcome)
    return valid, dropped


def _check(raw, entry, known_symbols, today_local, zone, max_span_days):
    """A ValidChoice, or the reason the choice is dropped."""
    if entry is None:
        return "unknown_query"
    values = {key: getattr(raw, key) for key in ("symbol", "agent", *DATES, "section")}
    if any(values[key] != "" for key in values if key not in entry.takes):
        return "unexpected_parameter"
    if any(values[key] == "" for key in entry.required):
        return "missing_parameter"
    if "symbol" in entry.takes and values["symbol"] != "" and not _known(
        values["symbol"], known_symbols
    ):
        return "unknown_symbol"
    if values["agent"] != "" and values["agent"] not in entry.agents:
        return "unknown_agent"
    span = _span(values, entry, today_local, zone, max_span_days)
    if span is None:
        return "bad_dates"
    if values["section"] != "" and values["section"] not in entry.sections:
        return "unknown_section"
    return ValidChoice(
        entry.name, values["symbol"], values["agent"], values["section"], *span
    )


def _known(symbol, known_symbols) -> bool:
    return (
        isinstance(symbol, str)
        and SYMBOL_PATTERN.fullmatch(symbol) is not None
        and symbol in known_symbols
    )


def _span(values, entry, today_local, zone, max_span_days):
    """(from_date, to_date, start, end), (None,) * 4 for a dateless entry, or None if bad."""
    if entry.default_days is None:
        return (None, None, None, None)
    first = _day(values["from_date"], today_local - timedelta(days=entry.default_days - 1))
    last = _day(values["to_date"], today_local)
    if first is None or last is None or first > last:
        return None
    if (last - first).days + 1 > max_span_days:
        return None
    start = datetime.combine(first, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    return (first, last, start, end)


def _day(text, default: date) -> date | None:
    if text == "":
        return default
    if not isinstance(text, str) or _ISO_DATE.fullmatch(text) is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
