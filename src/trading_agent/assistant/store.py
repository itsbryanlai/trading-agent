"""The menu's SQL, run in one read-only transaction per question (research A15, A11;
contracts/query-menu.md).

Every query is a module-level constant with bound parameters; model output reaches SQL only
through a validated `ValidChoice`. The login can't write (ADR 0004), and the transaction is
`REPEATABLE READ READ ONLY` as well, so a question's results come from one snapshot. Every
timestamp comes back in the owner's zone and in ET; untrusted text is cut. The `settings`
entry reads files, not the database, and is not run here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg import IsolationLevel
from psycopg.rows import dict_row

from trading_agent.assistant.menu import QueryResult, ValidChoice

ET = ZoneInfo("America/New_York")
_UNTRUSTED = {"reasoning_md", "rationale_md", "summary_md", "broker_reason", "detail"}
_SOURCE_KEYS = ("title", "publisher", "url", "relevance", "published_at")

_RANGE = "{col} >= %(start)s AND {col} < %(end)s"
_SYMBOL = "(%(symbol)s::text = '' OR {col} = %(symbol)s::text)"
_AGENT = "(%(agent)s::text = '' OR {col} = %(agent)s::text)"
_DAYS = "{col} BETWEEN %(first_day)s AND %(last_day)s"
_LIMIT = "LIMIT %(limit)s"
_ORDER_SYMBOL = "v.approved_order->>'symbol'"

KNOWN_SYMBOLS = """
SELECT symbol FROM positions
UNION SELECT symbol FROM reports WHERE symbol IS NOT NULL
UNION SELECT symbol FROM decisions
UNION SELECT symbol FROM stop_loss_triggers
UNION SELECT symbol FROM instrument_reference
"""
KNOWN_AMONG = """
SELECT symbol FROM positions WHERE symbol = ANY(%(symbols)s::text[])
UNION SELECT symbol FROM reports WHERE symbol = ANY(%(symbols)s::text[])
UNION SELECT symbol FROM decisions WHERE symbol = ANY(%(symbols)s::text[])
UNION SELECT symbol FROM stop_loss_triggers WHERE symbol = ANY(%(symbols)s::text[])
UNION SELECT symbol FROM instrument_reference WHERE symbol = ANY(%(symbols)s::text[])
"""
POSITIONS = f"""
SELECT symbol, qty, avg_entry_price, updated_at FROM positions ORDER BY symbol {_LIMIT}
"""
ACCOUNT_LATEST = """
SELECT equity, cash, buying_power, taken_at FROM account_snapshots
ORDER BY taken_at DESC LIMIT 1
"""
SYSTEM_STATE = """
SELECT e.trading_paused, e.daily_loss_halt_active, e.daily_starting_equity,
       e.current_trading_date, e.updated_at, s.halt_triggered_on, s.baseline_trading_day
FROM system_state_effective e CROSS JOIN system_state s
"""
ACCOUNT_HISTORY = f"""
SELECT equity, cash, taken_at FROM account_snapshots
WHERE {_RANGE.format(col="taken_at")} ORDER BY taken_at DESC {_LIMIT}
"""
DECISIONS = f"""
SELECT d.id, d.generated_at, d.symbol, d.direction, d.size_pct, d.quote_at_decision,
       d.quote_time, d.reasoning_md,
       v.verdict, v.rejection_rule, v.evaluated_at,
       o.status AS order_status, o.fill_price, o.fill_qty, o.updated_at AS order_updated_at,
       COALESCE((SELECT json_agg(json_build_object('id', r.id, 'agent', r.agent,
                                                   'direction', r.direction)
                                 ORDER BY r.generated_at)
                 FROM decision_reports dr JOIN reports r ON r.id = dr.report_id
                 WHERE dr.decision_id = d.id), '[]'::json) AS cited_reports
FROM decisions d
LEFT JOIN risk_verdicts v ON v.decision_id = d.id
LEFT JOIN orders o ON o.risk_verdict_id = v.id
WHERE {_RANGE.format(col="d.generated_at")} AND {_SYMBOL.format(col="d.symbol")}
ORDER BY d.generated_at DESC {_LIMIT}
"""
REPORTS = f"""
SELECT id, generated_at, agent, symbol, direction, conviction, suggested_size_pct,
       expires_at, status, rationale_md, sources
FROM reports_with_status
WHERE {_RANGE.format(col="generated_at")} AND {_AGENT.format(col="agent")}
  AND {_SYMBOL.format(col="symbol")}
ORDER BY generated_at DESC {_LIMIT}
"""
ORDERS = f"""
SELECT o.id, o.submitted_at, {_ORDER_SYMBOL} AS symbol, v.approved_order->>'side' AS side,
       v.approved_order->>'qty' AS qty, o.limit_price, o.status, o.fill_price, o.fill_qty,
       o.broker_reason, o.updated_at
FROM orders o JOIN risk_verdicts v ON v.id = o.risk_verdict_id
WHERE {_RANGE.format(col="o.submitted_at")} AND {_SYMBOL.format(col=_ORDER_SYMBOL)}
ORDER BY o.submitted_at DESC {_LIMIT}
"""
EXECUTION_REFUSALS = f"""
SELECT x.id, x.refused_at, x.reason, x.details, {_ORDER_SYMBOL} AS symbol,
       v.approved_order->>'side' AS side, v.approved_order->>'qty' AS qty
FROM execution_refusals x JOIN risk_verdicts v ON v.id = x.risk_verdict_id
WHERE {_RANGE.format(col="x.refused_at")} AND {_SYMBOL.format(col=_ORDER_SYMBOL)}
ORDER BY x.refused_at DESC {_LIMIT}
"""
IN_FLIGHT = f"""
SELECT v.trading_day, v.symbol, v.side, v.unsettled_qty, v.limit_price
FROM in_flight_orders v WHERE {_SYMBOL.format(col="v.symbol")}
ORDER BY v.trading_day DESC, v.symbol {_LIMIT}
"""
STOP_LOSSES = f"""
SELECT t.id, t.symbol, t.observed_price, t.observed_at, v.verdict, v.rejection_rule,
       v.evaluated_at, o.status AS order_status, o.fill_price, o.fill_qty
FROM stop_loss_triggers t
LEFT JOIN risk_verdicts v ON v.stop_loss_trigger_id = t.id
LEFT JOIN orders o ON o.risk_verdict_id = v.id
WHERE {_RANGE.format(col="t.observed_at")} AND {_SYMBOL.format(col="t.symbol")}
ORDER BY t.observed_at DESC {_LIMIT}
"""
JOURNAL = f"""
SELECT trading_day, equity_open, equity_close, summary_md, per_agent_attribution, written_at
FROM journal WHERE {_DAYS.format(col="trading_day")} ORDER BY trading_day DESC {_LIMIT}
"""
AGENT_RUNS = f"""
SELECT id, agent, trading_day, reason, slot_key, slot_at, started_at, finished_at, outcome,
       detail
FROM orchestrator_runs
WHERE {_DAYS.format(col="trading_day")} AND {_AGENT.format(col="agent")}
ORDER BY trading_day DESC, COALESCE(started_at, slot_at) DESC NULLS LAST, id {_LIMIT}
"""
UNIVERSE = """
SELECT symbol, trading_day, security_type, exchange_mic, market_cap_usd,
       avg_daily_dollar_volume_usd, share_price_usd, fetched_at
FROM instrument_reference WHERE symbol = %(symbol)s ORDER BY trading_day DESC LIMIT 1
"""

# Each entry runs one or more (part tag, SQL) pairs; a lone part has no tag.
PLANS: dict[str, tuple[tuple[str | None, str], ...]] = {
    "portfolio_now": (
        ("position", POSITIONS),
        ("account_snapshot", ACCOUNT_LATEST),
        ("system_state", SYSTEM_STATE),
    ),
    "account_history": ((None, ACCOUNT_HISTORY),),
    "decisions": ((None, DECISIONS),),
    "reports": ((None, REPORTS),),
    "orders": (
        ("order", ORDERS),
        ("execution_refusal", EXECUTION_REFUSALS),
        ("in_flight_order", IN_FLIGHT),
    ),
    "stop_losses": ((None, STOP_LOSSES),),
    "journal": ((None, JOURNAL),),
    "agent_runs": ((None, AGENT_RUNS),),
    "universe": ((None, UNIVERSE),),
}


class StoreError(Exception):
    """The database failed. Names the exception type only."""


def default_connect(url: str):
    return psycopg.connect(url, connect_timeout=10, row_factory=dict_row)


class PostgresStore:
    def __init__(
        self,
        connect: Callable | None,
        url: str,
        zone: ZoneInfo,
        statement_timeout_seconds: int,
        max_rows: int,
        text_field_chars: int,
    ) -> None:
        self._connect = connect or default_connect
        self._url = url
        self._zone = zone
        self._timeout_ms = statement_timeout_seconds * 1000
        self._max_rows = max_rows
        self._text_chars = text_field_chars

    def __repr__(self) -> str:
        return "PostgresStore(<url hidden>)"

    __str__ = __repr__

    def known_symbols(self) -> set[str]:
        rows = self._in_transaction(lambda cur: _all(cur, KNOWN_SYMBOLS, {}))
        return {row["symbol"] for row in rows if isinstance(row["symbol"], str)}

    def known(self, symbols) -> set[str]:
        """Which of `symbols` appear in any of the five sources (one transaction)."""
        return self._in_transaction(lambda cur: _known(cur, symbols))

    def read_question(self, symbols, validate: Callable) -> tuple[list, list, list[QueryResult]]:
        """The symbol lookup, the validation and the reads in one transaction, so a symbol
        accepted here is read from the same snapshot. `validate(known)` returns the valid
        choices and the dropped ones; the result is both, then the results of the valid
        choices that are database entries (not `settings`), in order."""

        def work(cur):
            valid, dropped = validate(_known(cur, symbols))
            database = [c for c in valid if c.name != "settings"]
            return valid, dropped, [self._one(cur, c) for c in database]

        return self._in_transaction(work)

    def run(self, choices: list[ValidChoice]) -> list[QueryResult]:
        """All of a question's choices, from one snapshot. `settings` is not a database entry."""
        for choice in choices:
            if choice.name not in PLANS:
                raise ValueError(f"{choice.name} is not a database entry")
        return self._in_transaction(lambda cur: [self._one(cur, c) for c in choices])

    def _in_transaction(self, work):
        connection = None
        try:
            connection = self._connect(self._url)
            connection.isolation_level = IsolationLevel.REPEATABLE_READ
            connection.read_only = True
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT set_config('statement_timeout', %s, true)", (str(self._timeout_ms),)
                )
                return work(cur)
        except (psycopg.Error, OSError) as exc:
            raise StoreError(f"database: {type(exc).__name__}") from None
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                    connection.close()
                except (psycopg.Error, OSError):
                    pass

    def _one(self, cur, choice: ValidChoice) -> QueryResult:
        params = self._params(choice)
        rows: list[dict] = []
        cut = False
        for tag, statement in PLANS[choice.name]:
            fetched = _all(cur, statement, params)
            if len(fetched) > self._max_rows and "%(limit)s" in statement:
                fetched, cut = fetched[: self._max_rows], True
            rows.extend(({"part": tag} if tag else {}) | self._row(r) for r in fetched)
        return QueryResult(choice.name, self._given(choice), rows, cut)

    def _params(self, choice: ValidChoice) -> dict:
        params = {
            "symbol": choice.symbol,
            "agent": choice.agent,
            "limit": self._max_rows + 1,
            "start": choice.start,
            "end": choice.end,
            "first_day": None,
            "last_day": None,
        }
        if choice.start is not None and choice.end is not None:
            params["first_day"] = choice.start.astimezone(ET).date()
            params["last_day"] = (choice.end - timedelta(microseconds=1)).astimezone(ET).date()
        return params

    def _given(self, choice: ValidChoice) -> dict:
        given = {k: v for k in ("symbol", "agent", "section") if (v := getattr(choice, k))}
        if choice.start is not None and choice.end is not None:
            given["from_date"] = choice.from_date.isoformat()
            given["to_date"] = choice.to_date.isoformat()
            given["span"] = {"start": self._stamp(choice.start), "end": self._stamp(choice.end)}
        return given

    def _stamp(self, moment: datetime) -> dict:
        return {
            "owner": moment.astimezone(self._zone).isoformat(),
            "et": moment.astimezone(ET).isoformat(),
        }

    def _row(self, row: dict) -> dict:
        return {key: self._value(key, value) for key, value in row.items()}

    def _value(self, key: str, value):
        if key == "details":
            return self._cut(json.dumps(value, separators=(",", ":"), ensure_ascii=False))
        if key == "sources":
            return self._sources(value)
        if isinstance(value, datetime):
            return self._stamp(value)
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal | UUID):
            return str(value)
        if isinstance(value, str) and key in _UNTRUSTED:
            return self._cut(value)
        return value

    def _sources(self, sources) -> list:
        if not isinstance(sources, list):
            return []
        return [
            {
                key: self._cut(item[key]) if isinstance(item[key], str) else item[key]
                for key in _SOURCE_KEYS
                if key in item and isinstance(item[key], str | int | float | None)
            }
            for item in sources
            if isinstance(item, dict)
        ]

    def _cut(self, text: str) -> str:
        return text[: self._text_chars]


def _known(cur, symbols) -> set[str]:
    wanted = sorted(s for s in symbols if isinstance(s, str))
    if not wanted:
        return set()
    rows = _all(cur, KNOWN_AMONG, {"symbols": wanted})
    return {row["symbol"] for row in rows if isinstance(row["symbol"], str)}


def _all(cur, statement: str, params: dict) -> list[dict]:
    cur.execute(statement, params)
    return cur.fetchall()
