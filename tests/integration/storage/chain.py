"""Row builders for the report -> decision -> verdict -> order chain.

All inserts run as whatever role the connection currently has; tests wrap them
in `as_role` when the writer's identity matters.
"""

from __future__ import annotations

import json

import psycopg

SOURCES = json.dumps(
    [
        {
            "title": "Q3 earnings beat",
            "url": "https://example.com/aapl-q3",
            "publisher": "Example Wire",
            "published_at": "2026-09-27T12:00:00Z",
        }
    ]
)


def insert_report(
    conn: psycopg.Connection,
    agent: str = "research",
    symbol: str = "AAPL",
    expires_in: str = "6 hours",
) -> str:
    """`expires_in` may be negative (e.g. '-1 hour') for an already-expired report."""
    return conn.execute(
        """
        INSERT INTO reports (agent, symbol, direction, conviction, suggested_size_pct,
                             sources, rationale_md, generated_at, expires_at)
        VALUES (%s, %s, 'buy', 4, 5, %s::jsonb, 'thesis',
                now() - interval '1 day', now() + %s::interval)
        RETURNING id
        """,
        (agent, symbol, SOURCES, expires_in),
    ).fetchone()["id"]


def insert_decision(conn: psycopg.Connection, report_ids: list[str], symbol: str = "AAPL") -> str:
    decision_id = conn.execute(
        """
        INSERT INTO decisions (symbol, direction, size_pct, reasoning_md, quote_at_decision)
        VALUES (%s, 'buy', 5, 'both analysts converged', 187.25)
        RETURNING id
        """,
        (symbol,),
    ).fetchone()["id"]
    for report_id in report_ids:
        conn.execute(
            "INSERT INTO decision_reports (decision_id, report_id) VALUES (%s, %s)",
            (decision_id, report_id),
        )
    return decision_id


APPROVED_ORDER = json.dumps(
    {"symbol": "AAPL", "side": "buy", "qty": 10, "limit_price": 187.25, "time_in_force": "day"}
)


def insert_verdict(
    conn: psycopg.Connection,
    decision_id: str | None,
    approved: bool = True,
    trigger_id: str | None = None,
) -> str:
    """A verdict for a decision, or (with decision_id=None) for a stop-loss trigger."""
    if approved:
        outcome = ("approved", None, APPROVED_ORDER)
    else:
        outcome = ("rejected", "max_position_pct", None)
    return conn.execute(
        """
        INSERT INTO risk_verdicts (decision_id, stop_loss_trigger_id, verdict, rejection_rule,
                                   approved_order, trading_day, config_version)
        VALUES (%s, %s, %s, %s, %s::jsonb, current_date, 'test')
        RETURNING id
        """,
        (decision_id, trigger_id, *outcome),
    ).fetchone()["id"]


def insert_trigger(conn: psycopg.Connection, symbol: str = "AAPL", observed: str = "160") -> str:
    return conn.execute(
        "INSERT INTO stop_loss_triggers (symbol, observed_price) VALUES (%s, %s) RETURNING id",
        (symbol, observed),
    ).fetchone()["id"]


def order_id_for(verdict_id, side: str = "buy", symbol: str = "AAPL", day: str = "2026-09-28"):
    """The ADR 0012 identifier: the suffix must name the order's own verdict."""
    return f"{day}-{symbol}-{side}-{str(verdict_id)[:8]}"


def insert_order(
    conn: psycopg.Connection,
    verdict_id: str,
    side: str = "buy",
    symbol: str = "AAPL",
    limit_price: str | None = "187.25",
) -> str:
    """A submitted order; a buy carries a limit price and a sell doesn't (migration 0007)."""
    return conn.execute(
        "INSERT INTO orders (id, risk_verdict_id, status, limit_price) "
        "VALUES (%s, %s, 'submitted', %s) RETURNING id",
        (
            order_id_for(verdict_id, side, symbol),
            verdict_id,
            limit_price if side == "buy" else None,
        ),
    ).fetchone()["id"]


def insert_refusal(conn: psycopg.Connection, verdict_id: str, reason: str = "trading_paused"):
    return conn.execute(
        "INSERT INTO execution_refusals (risk_verdict_id, reason, details, refused_at) "
        "VALUES (%s, %s, '{}'::jsonb, now()) RETURNING id",
        (verdict_id, reason),
    ).fetchone()["id"]
