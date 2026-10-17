"""The day's facts from the read rows (research J10).

Pure. Copies only numbers, dates, closed-set codes and well-formed tickers into `DayFacts`;
every other column of the rows (anything a model or the broker wrote) is never looked at.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from trading_agent.journal.model import DayFacts, JournalReads

_NEW_YORK = ZoneInfo("America/New_York")
_CODE = re.compile(r"[a-z_]{1,40}")
_TICKER = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")
_DIRECTIONS = ("buy", "sell", "hold")
_ORDER_STATUSES = ("submitted", "partially_filled", "filled", "rejected", "canceled", "expired")
DAILY_LOSS_HALT = "daily_loss_halt"
DAILY_LOSS_LINE_CROSSED = "daily_loss_line_crossed"


def _ny_date(moment: datetime) -> date:
    return moment.astimezone(_NEW_YORK).date()


def _code(value: Any) -> str:
    """A closed-set code, or `other` for anything that isn't shaped like one."""
    if isinstance(value, str) and _CODE.fullmatch(value):
        return value
    return "other"


def day_facts(
    rows: JournalReads,
    day: date,
    missed_sessions: tuple[date, ...],
    unpriced_count: int,
) -> DayFacts:
    """Apply research J10's day and counting rules to the rows of one snapshot."""
    if rows.snapshot_open is None or rows.snapshot_close is None:
        raise ValueError("the day has no account snapshot")

    decisions = [d for d in rows.decisions if _ny_date(d["generated_at"]) == day]
    today_ids = {d["id"] for d in decisions}
    by_decision: dict[Any, list[dict[str, Any]]] = {}
    for verdict in rows.verdicts:
        if verdict["decision_id"] in today_ids:
            by_decision.setdefault(verdict["decision_id"], []).append(verdict)

    approved = rejected = 0
    rules: Counter[str] = Counter()
    for own in by_decision.values():
        if any(v["verdict"] == "approved" for v in own):
            approved += 1
        elif any(v["verdict"] == "rejected" for v in own):
            rejected += 1
            rules[_code(own[0]["rejection_rule"])] += 1

    day_verdicts = [v for v in rows.verdicts if v["trading_day"] == day]
    stop_verdicts = [v for v in day_verdicts if v["stop_loss_trigger_id"] is not None]
    day_verdict_ids = {v["id"] for v in day_verdicts}
    statuses = Counter(o["status"] for o in rows.orders if o["risk_verdict_id"] in day_verdict_ids)
    refusals = Counter(
        _code(r["reason"]) for r in rows.refusals if _ny_date(r["refused_at"]) == day
    )
    breaker = any(v["rejection_rule"] == DAILY_LOSS_HALT for v in day_verdicts) or (
        refusals[DAILY_LOSS_LINE_CROSSED] > 0
    )

    symbols = {d["symbol"] for d in decisions}
    return DayFacts(
        day=day,
        equity_open=Decimal(rows.snapshot_open["equity"]),
        equity_close=Decimal(rows.snapshot_close["equity"]),
        close_taken_at=rows.snapshot_close["taken_at"],
        breaker_triggered=breaker,
        decisions=len(decisions),
        by_direction={k: sum(1 for d in decisions if d["direction"] == k) for k in _DIRECTIONS},
        approved=approved,
        rejected=rejected,
        rejection_rules=dict(rules),
        orders=sum(statuses.values()),
        order_statuses={k: statuses[k] for k in _ORDER_STATUSES},
        refusals=dict(refusals),
        stop_loss_triggers=sum(1 for t in rows.triggers if _ny_date(t["observed_at"]) == day),
        stop_loss_approved=sum(1 for v in stop_verdicts if v["verdict"] == "approved"),
        stop_loss_rejected=sum(1 for v in stop_verdicts if v["verdict"] == "rejected"),
        decided_symbols=tuple(sorted(s for s in symbols if _well_formed(s))),
        malformed_symbols=sum(1 for s in symbols if not _well_formed(s)),
        missed_sessions=missed_sessions,
        unpriced_count=unpriced_count,
    )


def _well_formed(symbol: Any) -> bool:
    return isinstance(symbol, str) and _TICKER.fullmatch(symbol) is not None
