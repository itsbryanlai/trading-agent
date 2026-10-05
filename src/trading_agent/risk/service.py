"""The Risk Gate's thin caller: loads inputs as ta_risk_gate, runs the pure core,
and persists the verdict (plus any baseline or halt) in one transaction.

It makes no decisions of its own. See specs/002-risk-gate/contracts/gate-interface.md.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from trading_agent.risk import calendar, gate
from trading_agent.risk.config import RiskConfig, load_config
from trading_agent.risk.model import (
    ApprovedOrder,
    Context,
    DecisionRequest,
    Reference,
    Request,
    StopLossRequest,
    Verdict,
)

DEFAULT_CONFIG_PATH = Path("config/risk.yaml")

# Every evaluation takes this transaction-scoped advisory lock, so two can never
# interleave: the daily order cap is a count, and two concurrent evaluations
# could otherwise each see room for one more (research.md G10).
_LOCK_KEY = 0x7269736B  # "risk"


def evaluate_decision(
    conn: psycopg.Connection,
    decision_id,
    *,
    now: datetime | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> Verdict | None:
    """Judge one PM decision. `None` for a hold. Idempotent (FR-017)."""
    config = load_config(config_path)  # a bad file raises here, before any write
    now = now or datetime.now(UTC)
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        existing = _existing(cur, "decision_id", decision_id)
        if existing is not None:
            return existing

        cur.execute(
            "SELECT symbol, direction, size_pct, quote_at_decision, quote_time "
            "FROM decisions WHERE id = %s",
            (decision_id,),
        )
        decision = cur.fetchone()
        if decision is None:
            raise LookupError(f"no decision {decision_id}")
        if decision["direction"] == "hold":
            return None

        request = DecisionRequest(
            symbol=decision["symbol"],
            direction=decision["direction"],
            target_weight_pct=decision["size_pct"],
            quote=decision["quote_at_decision"],
            quote_time=decision["quote_time"],
        )
        return _judge_and_record(cur, request, now, config, decision_id=decision_id)


def evaluate_stop_loss_trigger(
    conn: psycopg.Connection,
    trigger_id,
    *,
    now: datetime | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> Verdict:
    """Judge one stop-loss trigger recorded by Execution's monitor. Idempotent (FR-017)."""
    config = load_config(config_path)
    now = now or datetime.now(UTC)
    with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        existing = _existing(cur, "stop_loss_trigger_id", trigger_id)
        if existing is not None:
            return existing

        cur.execute(
            "SELECT symbol, observed_price, observed_at FROM stop_loss_triggers WHERE id = %s",
            (trigger_id,),
        )
        trigger = cur.fetchone()
        if trigger is None:
            raise LookupError(f"no stop-loss trigger {trigger_id}")
        request = StopLossRequest(
            trigger["symbol"], trigger["observed_price"], trigger["observed_at"]
        )
        return _judge_and_record(cur, request, now, config, trigger_id=trigger_id)


def _judge_and_record(
    cur, request: Request, now: datetime, config: RiskConfig, *, decision_id=None, trigger_id=None
) -> Verdict:
    context, record_baseline = _load_context(cur, request.symbol, now)
    result = gate.evaluate(request, context, config)

    if record_baseline:
        cur.execute(
            "UPDATE system_state SET baseline_trading_day = %s, daily_starting_equity = %s, "
            "updated_at = %s",
            (context.trading_day, context.baseline_equity, now),
        )
    if result.record_halt:
        cur.execute(
            "UPDATE system_state SET halt_triggered_on = %s, updated_at = %s",
            (result.trading_day, now),
        )
    verdict = result.verdict
    cur.execute(
        """
        INSERT INTO risk_verdicts (decision_id, stop_loss_trigger_id, evaluated_at, verdict,
                                   rejection_rule, approved_order, trading_day, config_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            decision_id,
            trigger_id,
            now,
            "approved" if verdict.approved else "rejected",
            verdict.rejection_rule,
            Jsonb(verdict.order.to_json()) if verdict.order else None,
            result.trading_day,
            result.config_version,
        ),
    )
    return verdict


def _existing(cur, column: str, value) -> Verdict | None:
    cur.execute(
        f"SELECT verdict, rejection_rule, approved_order FROM risk_verdicts WHERE {column} = %s",
        (value,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    if row["verdict"] == "approved":
        return Verdict.approve(ApprovedOrder.from_json(row["approved_order"]))
    return Verdict.reject(row["rejection_rule"])


def _load_context(cur, symbol: str, now: datetime) -> tuple[Context, bool]:
    """Everything the core needs, as plain values. Returns (context, record_baseline)."""
    day = calendar.trading_day(now)
    market_open = calendar.market_open(now)

    cur.execute("SELECT qty, avg_entry_price FROM positions WHERE symbol = %s", (symbol,))
    position = cur.fetchone()

    # Latest account state from *this* trading day only (FR-018, G9).
    cur.execute(
        "SELECT equity, cash FROM account_snapshots "
        "WHERE (taken_at AT TIME ZONE 'America/New_York')::date = %s AND taken_at <= %s "
        "ORDER BY taken_at DESC LIMIT 1",
        (day, now),
    )
    snapshot = cur.fetchone()
    lowest_today = _lowest_equity_since_open(cur, day, now)

    # Halt and baseline are judged against this evaluation's own trading day, not
    # the database clock, so the core's inputs are fully determined by `now`.
    cur.execute(
        "SELECT trading_paused, halt_triggered_on, baseline_trading_day, daily_starting_equity "
        "FROM system_state"
    )
    state = cur.fetchone()
    stored_baseline = (
        state["daily_starting_equity"] if state["baseline_trading_day"] == day else None
    )
    baseline, record_baseline = gate.choose_baseline(
        stored_baseline, _pre_open_equity(cur, day, now)
    )

    cur.execute(
        "SELECT count(*) AS n FROM risk_verdicts WHERE trading_day = %s "
        "AND verdict = 'approved' AND approved_order->>'exposure' = 'increase'",
        (day,),
    )
    increases_today = cur.fetchone()["n"]

    cur.execute(
        "SELECT security_type, exchange_mic, market_cap_usd, avg_daily_dollar_volume_usd, "
        "share_price_usd FROM instrument_reference WHERE symbol = %s AND trading_day = %s",
        (symbol, day),
    )
    reference_row = cur.fetchone()

    in_flight = _in_flight(cur, symbol, day)

    context = Context(
        now=now,
        trading_day=day,
        market_open=market_open,
        trading_paused=state["trading_paused"],
        halt_active=state["halt_triggered_on"] == day,
        shares_held=int(position["qty"]) if position else 0,
        avg_entry_price=position["avg_entry_price"] if position else None,
        equity=snapshot["equity"] if snapshot else None,
        cash=snapshot["cash"] if snapshot else None,
        baseline_equity=baseline,
        increase_orders_approved_today=increases_today,
        lowest_equity_today=lowest_today,
        reference=Reference(**reference_row) if reference_row else None,
        **in_flight,
    )
    return context, record_baseline


def _in_flight(cur, symbol: str, day: date) -> dict[str, Decimal]:
    """Today's approvals still working, summed into the four Context numbers
    (ADR 0020, research I3). Read in the same transaction as every other input;
    an earlier day's approval is never in flight (I2)."""
    cur.execute(
        "SELECT "
        "coalesce(sum(unsettled_qty) FILTER (WHERE symbol = %(s)s AND side = 'buy'), 0) "
        "AS buy_qty, "
        "coalesce(sum(unsettled_qty * limit_price) FILTER (WHERE symbol = %(s)s "
        "AND side = 'buy'), 0) AS buy_cost_symbol, "
        "coalesce(sum(unsettled_qty) FILTER (WHERE symbol = %(s)s AND side = 'sell'), 0) "
        "AS sell_qty, "
        "coalesce(sum(unsettled_qty * limit_price) FILTER (WHERE side = 'buy'), 0) "
        "AS buy_cost_all "
        "FROM in_flight_orders WHERE trading_day = %(d)s",
        {"s": symbol, "d": day},
    )
    row = cur.fetchone()
    return {
        "in_flight_buy_qty": row["buy_qty"],
        "in_flight_buy_cost_symbol": row["buy_cost_symbol"],
        "in_flight_sell_qty": row["sell_qty"],
        "in_flight_buy_cost_all": row["buy_cost_all"],
    }


def _lowest_equity_since_open(cur, day: date, now: datetime):
    """The lowest equity among snapshots taken on `day` at or after its open and no
    later than `now`, so a crossing seen between evaluations isn't forgotten
    (ADR 0014 §3). Pre-open snapshots are the baseline, not part of the day."""
    try:
        open_at = calendar.open_time(day)
    except ValueError:
        return None
    cur.execute(
        "SELECT min(equity) AS low FROM account_snapshots "
        "WHERE (taken_at AT TIME ZONE 'America/New_York')::date = %s "
        "AND taken_at >= %s AND taken_at <= %s",
        (day, open_at, now),
    )
    return cur.fetchone()["low"]


def _pre_open_equity(cur, day: date, now: datetime):
    """Equity of the last snapshot taken on `day` before its open (FR-013, G8).

    Restricted to the same New York date: Execution records one every trading
    day before the open, so an older snapshot means that duty was missed, and
    the gate fails closed rather than measuring today's loss from a stale base.
    """
    try:
        open_at = calendar.open_time(day)
    except ValueError:
        return None  # not a session day: no baseline, and the market is closed anyway
    cur.execute(
        "SELECT equity FROM account_snapshots "
        "WHERE (taken_at AT TIME ZONE 'America/New_York')::date = %s AND taken_at < %s "
        "ORDER BY taken_at DESC LIMIT 1",
        (day, min(open_at, now)),
    )
    row = cur.fetchone()
    return row["equity"] if row else None
