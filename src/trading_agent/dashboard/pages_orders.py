"""View models for order history (FR-008, contracts/routes.md query validation).

Pure. One row per risk verdict: its cause (a decision or a stop-loss trigger), the verdict and
rule, and what happened next (an order, Execution's refusal, or neither yet). Every stored
text is returned as plain text for the template to escape; nothing here makes `Markup`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from trading_agent.dashboard import render, timefmt
from trading_agent.dashboard.pages import money

SYMBOL = re.compile(r"^[A-Z.\-]{1,10}$")
ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PAGE = re.compile(r"^\d{1,6}$")
COLLAPSE_AT = 300
AWAITING = "Awaiting Execution."
NO_ORDER = "No order."


@dataclass(frozen=True)
class Filters:
    symbol: str | None = None
    day_from: date | None = None
    day_to: date | None = None
    page: int = 1


def _day(raw: str | None) -> date | None:
    if raw is None or not ISO_DAY.match(raw.strip()):
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def parse_filters(
    symbol: str | None, day_from: str | None, day_to: str | None, page: str | None
) -> Filters:
    """Bad values are ignored, never errors."""
    clean = symbol.strip().upper() if symbol else None
    number = int(page.strip()) if page and PAGE.match(page.strip()) else 1
    return Filters(
        symbol=clean if clean and SYMBOL.match(clean) else None,
        day_from=_day(day_from),
        day_to=_day(day_to),
        page=max(number, 1),
    )


def bounds(filters: Filters, zone: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    """The owner-zone days as UTC instants; the end day is exclusive (the day after)."""

    def start(day: date) -> datetime:
        return datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)

    since = None if filters.day_from is None else start(filters.day_from)
    until = None
    if filters.day_to is not None and filters.day_to < date.max:
        until = start(filters.day_to + timedelta(days=1))
    return since, until


@dataclass(frozen=True)
class Text:
    """Plain text; a long one carries a short summary so the page can collapse it."""

    full: str
    summary: str | None = None


def text(value: object) -> Text:
    full = str(value)
    return Text(full, render.excerpt(full, COLLAPSE_AT) if len(full) > COLLAPSE_AT else None)


@dataclass(frozen=True)
class OrderRow:
    verdict_id: str
    evaluated_at: str
    cause_kind: str  # "decision" | "stop_loss"
    symbol: str
    cause: str
    cause_detail: str
    verdict: str
    rule: str | None
    approved: str | None
    outcome_kind: str  # "order" | "refusal" | "order_and_refusal" | "awaiting" | "none"
    outcome: tuple[Text, ...]


@dataclass(frozen=True)
class OrdersView:
    rows: tuple[OrderRow, ...]
    filters: Filters
    symbol_value: str
    from_value: str
    to_value: str
    prev_url: str | None
    next_url: str | None
    empty_text: str | None


def _plain(value: Decimal) -> str:
    shown = f"{value:f}"
    return shown.rstrip("0").rstrip(".") if "." in shown else shown


def _approved_summary(order: Mapping | None) -> str | None:
    if not order:
        return None
    parts = [str(order.get("side", "")), str(order.get("qty", "")), str(order.get("symbol", ""))]
    summary = " ".join(p for p in parts if p)
    if order.get("limit_price") is not None:
        summary += f", limit {order['limit_price']}"
    return summary


def _cause(row: Mapping, zone: ZoneInfo) -> tuple[str, str, str, str]:
    if row["decision_id"] is not None:
        headline = f"{row['direction']} {row['d_symbol']}, target {_plain(row['size_pct'])}%"
        decided = timefmt.moment(row["decided_at"], zone)
        detail = f"Decided {decided}; quote {_plain(row['quote_at_decision'])}"
        return "decision", row["d_symbol"], headline, detail
    price = money(row["observed_price"])
    detail = f"Price seen {price} at {timefmt.moment(row['observed_at'], zone)}"
    return "stop_loss", row["t_symbol"], f"stop-loss exit {row['t_symbol']}", detail


def _order_lines(row: Mapping, zone: ZoneInfo) -> list[Text]:
    lines = [text(f"Order {row['status']}, submitted {timefmt.moment(row['submitted_at'], zone)}")]
    if row["limit_price"] is not None:
        lines.append(text(f"Limit price {money(row['limit_price'])}"))
    if row["fill_qty"] is not None:
        price = "" if row["fill_price"] is None else f" at {money(row['fill_price'])}"
        lines.append(text(f"Filled {_plain(row['fill_qty'])}{price}"))
    if row["broker_reason"]:
        lines.append(text(f"Broker reason: {row['broker_reason']}"))
    return lines


def _detail_lines(details: object) -> list[Text]:
    if isinstance(details, Mapping):
        items = [(str(key), value) for key, value in details.items()]
    elif details in (None, {}, []):
        return []
    else:
        return [text(json.dumps(details, default=str))]
    return [
        text(f"{key}: {value if isinstance(value, str) else json.dumps(value, default=str)}")
        for key, value in items
    ]


def _refusal_lines(row: Mapping, zone: ZoneInfo) -> list[Text]:
    when = timefmt.moment(row["refused_at"], zone)
    lines = [text(f"Refused by Execution: {row['refusal_reason']}, {when}")]
    return lines + _detail_lines(row["refusal_details"])


def _outcome(row: Mapping, zone: ZoneInfo) -> tuple[str, tuple[Text, ...]]:
    has_order, has_refusal = row["order_id"] is not None, row["refusal_reason"] is not None
    lines: list[Text] = []
    if has_order:
        lines += _order_lines(row, zone)
    if has_refusal:
        lines += _refusal_lines(row, zone)
    if has_order and has_refusal:
        return "order_and_refusal", tuple(lines)
    if has_order:
        return "order", tuple(lines)
    if has_refusal:
        return "refusal", tuple(lines)
    if row["verdict"] == "approved":
        return "awaiting", (Text(AWAITING),)
    return "none", (Text(NO_ORDER),)


def order_row(row: Mapping, zone: ZoneInfo) -> OrderRow:
    kind, symbol, headline, detail = _cause(row, zone)
    outcome_kind, lines = _outcome(row, zone)
    return OrderRow(
        verdict_id=str(row["id"]),
        evaluated_at=timefmt.moment(row["evaluated_at"], zone),
        cause_kind=kind,
        symbol=symbol,
        cause=headline,
        cause_detail=detail,
        verdict=row["verdict"],
        rule=row["rejection_rule"],
        approved=_approved_summary(row["approved_order"]),
        outcome_kind=outcome_kind,
        outcome=lines,
    )


def _url(filters: Filters, page: int) -> str:
    query = {}
    if filters.symbol:
        query["symbol"] = filters.symbol
    if filters.day_from:
        query["from"] = filters.day_from.isoformat()
    if filters.day_to:
        query["to"] = filters.day_to.isoformat()
    if page > 1:
        query["page"] = str(page)
    return "/orders" + (f"?{urlencode(query)}" if query else "")


def orders_view(
    rows: Sequence[Mapping], filters: Filters, page_size: int, zone: ZoneInfo
) -> OrdersView:
    """`rows` holds up to `page_size + 1`; the extra one only says a next page exists."""
    shown = rows[:page_size]
    return OrdersView(
        rows=tuple(order_row(row, zone) for row in shown),
        filters=filters,
        symbol_value=filters.symbol or "",
        from_value=filters.day_from.isoformat() if filters.day_from else "",
        to_value=filters.day_to.isoformat() if filters.day_to else "",
        prev_url=_url(filters, filters.page - 1) if filters.page > 1 else None,
        next_url=_url(filters, filters.page + 1) if len(rows) > page_size else None,
        empty_text=None if shown else "No orders match.",
    )
