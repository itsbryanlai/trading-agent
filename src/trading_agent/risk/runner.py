"""The Risk Gate's trigger runner (ADR 0013): evaluates stop-loss triggers that
Execution recorded, in the gate's own process, with only the gate's login.

Hosting only: every judgement is `service.evaluate_stop_loss_trigger`'s. A
recorded trigger is the whole hand-off from Execution; Execution picks up the
approved exit on its next tick (specs/003-execution research E13).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from trading_agent.risk import calendar
from trading_agent.risk.config import RiskConfigError
from trading_agent.risk.service import DEFAULT_CONFIG_PATH, evaluate_stop_loss_trigger

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
