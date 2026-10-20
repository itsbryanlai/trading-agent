"""Read `journal.per_agent_attribution`, schema version 1 (research D21, FR-010).

Contract: specs/012-journal-writer/contracts/attribution.md. Pure and strict:

- Numbers are decimal *strings*. A JSON number, a float, a boolean or a non-numeric string
  makes the whole row `Unreadable`; nothing is coerced.
- Unknown keys are ignored. A missing required key, another schema version, or a wrong type
  also makes the row `Unreadable`. The page still shows such a row's summary.
- Usage counts are checked separately: a missing or non-integer count flags that agent's usage
  for that row and leaves it out of the totals, but the rest of the row stays readable.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

SCHEMA_VERSION = 1
USAGE_FIELDS = (
    "written",
    "argued",
    "no_action",
    "cited",
    "cited_decisions",
    "approved",
    "filled",
)
_DECIMAL = re.compile(r"^-?\d+(\.\d+)?$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Unreadable:
    reason: str


class _Bad(Exception):
    """Internal: the row can't be read. Carries a short reason, never the value."""


@dataclass(frozen=True)
class Holding:
    symbol: str
    weight_pct: Decimal
    ref_price: Decimal
    support_session: date | None


@dataclass(frozen=True)
class Book:
    started_on: date
    index: Decimal
    day_return: Decimal
    holdings: tuple[Holding, ...]
    scaled_by: Decimal | None
    late_reports: int
    unpriced: tuple[str, ...]
    skipped_targets: tuple[str, ...]
    exited_sell: tuple[str, ...]
    exited_holding_limit: tuple[str, ...]
    usage: Mapping[str, int] | None  # None: missing or malformed, flagged


@dataclass(frozen=True)
class Attribution:
    trading_day: date
    previous_trading_day: date | None
    sessions_covered: int | None
    missed_sessions: tuple[date, ...]
    account_return: Decimal | None
    account_base: str | None
    close_taken_at: datetime | None
    agents: Mapping[str, Book]


# --- field readers --------------------------------------------------------------------------


def _decimal(value: object, what: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL.match(value):
        raise _Bad(f"{what}: not a decimal string")
    return Decimal(value)


def _optional_decimal(value: object, what: str) -> Decimal | None:
    return None if value is None else _decimal(value, what)


def _day(value: object, what: str) -> date:
    if not isinstance(value, str) or not _DAY.match(value):
        raise _Bad(f"{what}: not an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _Bad(f"{what}: not an ISO date") from None


def _optional_day(value: object, what: str) -> date | None:
    return None if value is None else _day(value, what)


def _count(value: object, what: str) -> int:
    if type(value) is not int or value < 0:
        raise _Bad(f"{what}: not a count")
    return value


def _mapping(value: object, what: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise _Bad(f"{what}: not an object")
    return value


def _strings(value: object, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _Bad(f"{what}: not a list of text")
    return tuple(value)


def _required(obj: Mapping, key: str, what: str):
    if key not in obj:
        raise _Bad(f"{what}.{key}: missing")
    return obj[key]


def _holding(symbol: str, value: object) -> Holding:
    holding = _mapping(value, f"holdings.{symbol}")
    return Holding(
        symbol=symbol,
        weight_pct=_decimal(holding.get("weight_pct"), "weight_pct"),
        ref_price=_decimal(holding.get("ref_price"), "ref_price"),
        support_session=_optional_day(holding.get("support_session"), "support_session"),
    )


def _usage(value: object) -> Mapping[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return {name: _count(value.get(name), f"usage.{name}") for name in USAGE_FIELDS}
    except _Bad:
        return None


def _book(name: str, value: object) -> Book:
    obj = _mapping(value, f"agents.{name}")
    what = f"agents.{name}"
    holdings = _mapping(obj.get("holdings") or {}, f"{what}.holdings")
    exited = _mapping(obj.get("exited") or {}, f"{what}.exited")
    late = obj.get("late_reports", 0)
    return Book(
        started_on=_day(_required(obj, "started_on", what), f"{what}.started_on"),
        index=_decimal(_required(obj, "index", what), f"{what}.index"),
        day_return=_decimal(_required(obj, "day_return", what), f"{what}.day_return"),
        holdings=tuple(_holding(str(s), h) for s, h in holdings.items()),
        scaled_by=_optional_decimal(obj.get("scaled_by"), f"{what}.scaled_by"),
        late_reports=_count(late, f"{what}.late_reports"),
        unpriced=_strings(obj.get("unpriced"), f"{what}.unpriced"),
        skipped_targets=_strings(obj.get("skipped_targets"), f"{what}.skipped_targets"),
        exited_sell=_strings(exited.get("sell"), f"{what}.exited.sell"),
        exited_holding_limit=_strings(exited.get("holding_limit"), f"{what}.exited.holding_limit"),
        usage=_usage(obj.get("usage")),
    )


def _close_taken_at(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise _Bad("account.close_taken_at: not an ISO time with offset")
    return parsed


def _read(obj: object) -> Attribution:
    row = _mapping(obj, "row")
    version = _required(row, "schema_version", "row")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise _Bad("unknown schema version")
    account = _mapping(_required(row, "account", "row"), "account")
    agents = _mapping(_required(row, "agents", "row"), "agents")
    missed = row.get("missed_sessions") or []
    if not isinstance(missed, list):
        raise _Bad("missed_sessions: not a list")
    covered = row.get("sessions_covered")
    return Attribution(
        trading_day=_day(_required(row, "trading_day", "row"), "trading_day"),
        previous_trading_day=_optional_day(row.get("previous_trading_day"), "previous_trading_day"),
        sessions_covered=None if covered is None else _count(covered, "sessions_covered"),
        missed_sessions=tuple(_day(d, "missed_sessions") for d in missed),
        account_return=_optional_decimal(_required(account, "return", "account"), "account.return"),
        account_base=account.get("base") if isinstance(account.get("base"), str) else None,
        close_taken_at=_close_taken_at(account.get("close_taken_at")),
        agents={str(name): _book(str(name), value) for name, value in agents.items()},
    )


def parse(obj: object) -> Attribution | Unreadable:
    """The row's attribution, or `Unreadable` with a short reason (never the offending value)."""
    try:
        return _read(obj)
    except _Bad as exc:
        return Unreadable(str(exc))


# --- the journal page's figures -------------------------------------------------------------

Row = tuple[date, "Attribution | Unreadable"]
Series = list[tuple[date, "Decimal | None"]]


def comparison(rows: Sequence[Row]) -> dict[str, Series]:
    """One series per analyst, one point per row, oldest first.

    An analyst's series holds its stored `index` from its `started_on` day; before that, on a
    row without it, or on an unreadable row, it holds None (a gap). Rebase each series to its
    own first point with `charts.rebase`. The account line is not here: it comes from the
    journal rows' `equity_close`, not from attribution (owner, review of T024).
    """
    ordered = sorted(rows, key=lambda row: row[0])
    names = sorted(
        {name for _, parsed in ordered if isinstance(parsed, Attribution) for name in parsed.agents}
    )
    series: dict[str, Series] = {name: [] for name in names}
    for day, parsed in ordered:
        readable = isinstance(parsed, Attribution)
        for name in names:
            book = parsed.agents.get(name) if readable else None
            value = book.index if book is not None and day >= book.started_on else None
            series[name].append((day, value))
    return series


@dataclass(frozen=True)
class UsageTotals:
    totals: Mapping[str, Mapping[str, int]]
    flagged: tuple[tuple[date, str | None], ...]  # (day, agent) skipped; agent None: whole row


def usage_totals(rows: Sequence[Row]) -> UsageTotals:
    """Each analyst's seven counts summed over the rows.

    A row that can't be read, or an agent whose usage is missing or malformed in a row, adds
    nothing and is listed in `flagged` so the page can say the totals are partial.
    """
    totals: dict[str, dict[str, int]] = {}
    flagged: list[tuple[date, str | None]] = []
    for day, parsed in sorted(rows, key=lambda row: row[0]):
        if not isinstance(parsed, Attribution):
            flagged.append((day, None))
            continue
        for name, book in parsed.agents.items():
            bucket = totals.setdefault(name, dict.fromkeys(USAGE_FIELDS, 0))
            if book.usage is None:
                flagged.append((day, name))
                continue
            for field in USAGE_FIELDS:
                bucket[field] += book.usage[field]
    return UsageTotals(totals, tuple(flagged))
