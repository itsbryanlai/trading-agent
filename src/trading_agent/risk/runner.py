"""The Risk Gate's runner (ADR 0013, ADR 0019): evaluates the stop-loss triggers
Execution recorded and the decisions the Portfolio Manager wrote, in the gate's
own process, with only the gate's login.

Hosting only: every judgement is `service.evaluate_stop_loss_trigger`'s or
`service.evaluate_decision`'s. A recorded trigger or decision is the whole
hand-off; Execution picks up the approved order on its next tick
(specs/003-execution research E13).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from trading_agent.risk import calendar
from trading_agent.risk.config import RiskConfigError
from trading_agent.risk.service import (
    DEFAULT_CONFIG_PATH,
    evaluate_decision,
    evaluate_stop_loss_trigger,
)

log = logging.getLogger(__name__)


def evaluate_pending_triggers(
    conn: psycopg.Connection, now: datetime, config_path: Path = DEFAULT_CONFIG_PATH
) -> int:
    """Evaluate every trigger observed on `now`'s trading day that has no verdict.

    Returns how many were evaluated. Each trigger is isolated: one that fails is
    logged and the rest still run. A lost database connection propagates, so the
    loop exits for a restart. Triggers from an earlier day are never evaluated.
    """
    today = calendar.trading_day(now)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT t.id, (t.observed_at AT TIME ZONE 'America/New_York')::date AS day
            FROM stop_loss_triggers t
            WHERE NOT EXISTS (SELECT 1 FROM risk_verdicts v WHERE v.stop_loss_trigger_id = t.id)
            ORDER BY t.observed_at, t.id
            """
        )
        pending = cur.fetchall()

    evaluated = 0
    for row in pending:
        if row["day"] != today:
            log.warning("risk gate: trigger %s is from %s, not today; left alone", *row.values())
            continue
        try:
            verdict = evaluate_stop_loss_trigger(conn, row["id"], now=now, config_path=config_path)
        except psycopg.OperationalError:
            raise
        except RiskConfigError as exc:
            log.error("risk gate: risk config invalid (%s); no trigger can be evaluated", exc)
            return evaluated
        except Exception:
            log.exception("risk gate: trigger %s failed; carrying on", row["id"])
            continue
        evaluated += 1
        log.info(
            "risk gate: trigger %s %s",
            row["id"],
            "approved" if verdict.approved else f"rejected ({verdict.rejection_rule})",
        )
    return evaluated


def evaluate_pending_decisions(
    conn: psycopg.Connection, now: datetime, config_path: Path = DEFAULT_CONFIG_PATH
) -> int:
    """Evaluate every buy or sell decision written on `now`'s trading day that has no verdict.

    Returns how many were evaluated. Holds get none, and decisions from an earlier
    day are never picked (the query filters to today, so they are not logged each
    pass either). Each decision is isolated: one that fails is logged and the rest
    still run. A lost database connection propagates, so the loop exits for a restart.
    An invalid risk config stops the pass, logged once.
    """
    today = calendar.trading_day(now)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT d.id
            FROM decisions d
            WHERE (d.generated_at AT TIME ZONE 'America/New_York')::date = %s
              AND d.direction <> 'hold'
              AND NOT EXISTS (SELECT 1 FROM risk_verdicts v WHERE v.decision_id = d.id)
            ORDER BY d.generated_at, d.id
            """,
            (today,),
        )
        pending = cur.fetchall()

    evaluated = 0
    for row in pending:
        try:
            verdict = evaluate_decision(conn, row["id"], now=now, config_path=config_path)
        except psycopg.OperationalError:
            raise
        except RiskConfigError as exc:
            log.error("risk gate: risk config invalid (%s); no decision can be evaluated", exc)
            return evaluated
        except Exception:
            log.exception("risk gate: decision %s failed; carrying on", row["id"])
            continue
        if verdict is None:
            continue
        evaluated += 1
        log.info(
            "risk gate: decision %s %s",
            row["id"],
            "approved" if verdict.approved else f"rejected ({verdict.rejection_rule})",
        )
    return evaluated
