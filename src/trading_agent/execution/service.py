"""Execution's thin service: reads rows as ta_execution, talks to the broker
through the port, runs the pure core, and records the outcome.

It makes no decisions of its own; those are in checks.py, fills.py and
monitor.py. See specs/003-execution/contracts/execution-interface.md and
research.md E4, E5, E13.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from trading_agent.execution import checks
from trading_agent.execution.broker import (
    Broker,
    BrokerOrder,
    BrokerUnavailable,
    OrderRejected,
    OrderRequest,
)
from trading_agent.execution.ids import order_id
from trading_agent.execution.model import (
    Approval,
    BuyLive,
    ExitLive,
    Outcome,
    Refuse,
    Retry,
    Session,
    Submit,
    TickReport,
)
from trading_agent.risk import calendar
from trading_agent.risk.config import RiskConfig, RiskConfigError, load_config
from trading_agent.risk.model import ApprovedOrder

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/risk.yaml")

# One approval at a time, across processes: a key distinct from the gate's
# (research E5). "exec".
_LOCK_KEY = 0x65786563

# After a submission timed out, wait this long for the broker's lookup to catch
# up before trying again, so a lagging lookup can't cause a second order (E5).
MAYBE_PLACED_WAIT = timedelta(minutes=2)

_OPEN_STATUSES = ("submitted", "partially_filled")


class NotAutocommit(Exception):
    """Execution needs each unit of work to be a real transaction (research E5)."""


@dataclass
class _Held:
    """Per-process memory. Losing it on restart only repeats harmless work."""

    maybe_placed: dict[UUID, datetime]


class Executor:
    """Holds the broker, the ta_execution connection, and a little per-process state."""

    def __init__(
        self,
        broker: Broker,
        conn: psycopg.Connection,
        config_path: Path = DEFAULT_CONFIG_PATH,
        *,
        _allow_savepoints: bool = False,
    ) -> None:
        self.broker = broker
        self.conn = conn
        self.config_path = config_path
        self._allow_savepoints = _allow_savepoints
        self._held = _Held(maybe_placed={})

    # --- entry point -------------------------------------------------------

    def tick(self, now: datetime) -> TickReport:
        """One pass of every duty that is due at `now` (research E13)."""
        self._require_autocommit()
        report = TickReport()
        session = session_at(now)
        self._isolated("sweep", report, lambda: self._sweep_lapsed(session, report))
        for approval in self._pending_approvals(session.today):
            self._isolated(
                f"verdict {approval.verdict_id}",
                report,
                lambda a=approval: self._process_approval(a, session, report),
            )
        return report

    # --- helpers shared by every duty ---------------------------------------

    def _require_autocommit(self) -> None:
        if not self.conn.autocommit and not self._allow_savepoints:
            raise NotAutocommit("Execution's connection must be autocommit (research E5)")

    def _isolated(self, what: str, report: TickReport, work) -> None:
        """Run one unit of work; a failure is logged and the tick carries on (E5).

        A lost database connection is not a per-unit failure: it propagates so the
        runner exits and the platform restarts it (ADR 0013).
        """
        try:
            work()
        except psycopg.OperationalError:
            raise
        except Exception:
            report.errors += 1
            log.exception("execution: %s failed; carrying on with the rest of the tick", what)

    def _cursor(self):
        return self.conn.cursor(row_factory=dict_row)

    # --- approvals (E5, E6) ------------------------------------------------

    def _pending_approvals(self, today) -> list[Approval]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT v.id, v.approved_order
                FROM risk_verdicts v
                WHERE v.verdict = 'approved' AND v.trading_day = %s
                  AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.risk_verdict_id = v.id)
                  AND NOT EXISTS (SELECT 1 FROM execution_refusals r
                                  WHERE r.risk_verdict_id = v.id)
                ORDER BY (v.approved_order->>'side') = 'buy', v.evaluated_at, v.id
                """,
                (today,),
            )
            rows = cur.fetchall()
        return [Approval(row["id"], ApprovedOrder.from_json(row["approved_order"])) for row in rows]

    def _process_approval(self, approval: Approval, session: Session, report: TickReport) -> None:
        try:
            with self.conn.transaction(), self._cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
                if self._has_outcome(cur, approval.verdict_id):
                    return
                if self._settle_from_broker(cur, approval, session, report):
                    return
                if self._waiting_on_maybe_placed(approval, session):
                    report.retried += 1
                    return
                outcome = self._judge(cur, approval, session)
                self._act(cur, approval, outcome, session, report)
        except BrokerUnavailable as exc:
            report.retried += 1
            log.warning("execution: verdict %s retried: %s", approval.verdict_id, exc)

    def _has_outcome(self, cur, verdict_id: UUID) -> bool:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM orders WHERE risk_verdict_id = %(v)s) "
            "OR EXISTS (SELECT 1 FROM execution_refusals WHERE risk_verdict_id = %(v)s) AS done",
            {"v": verdict_id},
        )
        return cur.fetchone()["done"]

    def _settle_from_broker(
        self, cur, approval: Approval, session: Session, report: TickReport
    ) -> bool:
        """E5 steps 2-3: refuse a clashing identifier, else adopt an order the broker
        already has under it. True if the approval now has an outcome."""
        oid = order_id(approval.verdict_id, approval.order)
        cur.execute(
            "SELECT risk_verdict_id FROM orders WHERE id = %s AND risk_verdict_id <> %s",
            (oid, approval.verdict_id),
        )
        clash = cur.fetchone()
        if clash is not None:
            outcome = checks.clash_refusal(approval, clash["risk_verdict_id"])
            self._record_refusal(cur, approval, outcome, session)
            report.refused += 1
            return True
        found = self.broker.find_order(oid)
        if found is not None:
            self._record_order(cur, approval, found, session.now)
            self._held.maybe_placed.pop(approval.verdict_id, None)
            report.recovered += 1
            log.info("execution: verdict %s already at the broker as %s", approval.verdict_id, oid)
            return True
        return False

    def _waiting_on_maybe_placed(self, approval: Approval, session: Session) -> bool:
        since = self._held.maybe_placed.get(approval.verdict_id)
        if since is None:
            return False
        if session.now - since < MAYBE_PLACED_WAIT:
            return True
        del self._held.maybe_placed[approval.verdict_id]
        return False

    def _judge(self, cur, approval: Approval, session: Session) -> Outcome:
        if approval.order.side == "buy":
            return self._judge_buy(cur, approval, session)
        return self._judge_exit(cur, approval, session)

    def _judge_buy(self, cur, approval: Approval, session: Session) -> Outcome:
        cur.execute("SELECT trading_paused FROM system_state")
        paused = cur.fetchone()["trading_paused"]
        live = BuyLive(
            paused=paused,
            baseline=baseline_equity(cur, session.today),
            config=self._load_config(),
        )
        early = checks.precheck_buy(approval, live, session)
        if early is not None:
            return early

        # FR-004: fetch and record live account state immediately before a buy.
        account = self.broker.get_account()
        cur.execute(
            "INSERT INTO account_snapshots (taken_at, equity, cash, buying_power) "
            "VALUES (%s, %s, %s, %s)",
            (session.now, account.equity, account.cash, account.buying_power),
        )
        symbol = approval.order.symbol
        held = self._held_qty(symbol)
        open_qty, open_cost = open_buys(cur, symbol)
        live = BuyLive(
            paused=paused,
            baseline=live.baseline,
            config=live.config,
            equity=account.equity,
            cash=account.cash,
            min_equity_since_open=min_equity_since_open(cur, session),
            held_qty=held,
            open_buy_qty_symbol=open_qty,
            open_buy_cost_all=open_cost,
            ask=self.broker.get_latest_ask(symbol),
        )
        return checks.check_buy(approval, live, session)

    def _judge_exit(self, cur, approval: Approval, session: Session) -> Outcome:
        symbol = approval.order.symbol
        live = ExitLive(
            held_qty=self._held_qty(symbol), open_sell_qty_symbol=open_sells(cur, symbol)
        )
        return checks.check_exit(approval, live, session)

    def _held_qty(self, symbol: str) -> Decimal:
        for position in self.broker.get_positions():
            if position.symbol == symbol:
                return position.qty
        return Decimal(0)

    def _load_config(self) -> RiskConfig | None:
        try:
            return load_config(self.config_path)
        except RiskConfigError as exc:
            log.error("execution: risk config failed to load (%s); buys are held", exc)
            return None

    def _act(
        self, cur, approval: Approval, outcome: Outcome, session: Session, report: TickReport
    ) -> None:
        if isinstance(outcome, Retry):
            report.retried += 1
            log.info("execution: verdict %s retried: %s", approval.verdict_id, outcome.why)
        elif isinstance(outcome, Refuse):
            self._record_refusal(cur, approval, outcome, session)
            report.refused += 1
            log.info("execution: verdict %s refused: %s", approval.verdict_id, outcome.reason)
        elif isinstance(outcome, Submit):
            self._submit(cur, approval, outcome.request, session, report)

    def _submit(
        self, cur, approval: Approval, request: OrderRequest, session: Session, report: TickReport
    ) -> None:
        try:
            placed = self.broker.submit_order(request)
        except OrderRejected as exc:
            # A duplicate-id rejection after a lagging lookup must not hide a live
            # order, so ask again before recording a rejection (E5).
            again = self.broker.find_order(request.client_order_id)
            if again is not None:
                self._record_order(cur, approval, again, session.now)
                report.recovered += 1
                return
            self._record_rejection(cur, approval, request, exc.reason, session.now)
            report.refused += 1
            log.warning("execution: broker rejected %s: %s", request.client_order_id, exc.reason)
            return
        except BrokerUnavailable:
            # Maybe placed: roll back, and let a later lookup settle it (E5).
            self._held.maybe_placed[approval.verdict_id] = session.now
            raise
        self._record_order(cur, approval, placed, session.now)
        report.submitted += 1
        log.info("execution: submitted %s", request.client_order_id)

    # --- lapsed approvals (E4) ---------------------------------------------

    def _sweep_lapsed(self, session: Session, report: TickReport) -> None:
        """Every approval whose trading day is over gets exactly one outcome. The
        broker is asked first, so a real order is never marked expired (E4)."""
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT v.id, v.approved_order
                FROM risk_verdicts v
                WHERE v.verdict = 'approved'
                  AND (v.trading_day < %s OR (v.trading_day = %s AND %s))
                  AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.risk_verdict_id = v.id)
                  AND NOT EXISTS (SELECT 1 FROM execution_refusals r
                                  WHERE r.risk_verdict_id = v.id)
                ORDER BY v.trading_day, v.id
                """,
                (session.today, session.today, session.after_close),
            )
            rows = cur.fetchall()
        for row in rows:
            approval = Approval(row["id"], ApprovedOrder.from_json(row["approved_order"]))
            self._isolated(
                f"lapsed verdict {approval.verdict_id}",
                report,
                lambda a=approval: self._expire(a, session, report),
            )

    def _expire(self, approval: Approval, session: Session, report: TickReport) -> None:
        try:
            with self.conn.transaction(), self._cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
                if self._has_outcome(cur, approval.verdict_id):
                    return
                if self._settle_from_broker(cur, approval, session, report):
                    return
                outcome = checks.expiry_check(approval, session)
                if not isinstance(outcome, Refuse):
                    raise AssertionError(f"lapsed verdict {approval.verdict_id} not expired")
                self._record_refusal(cur, approval, outcome, session)
                report.refused += 1
        except BrokerUnavailable as exc:
            report.retried += 1
            log.warning("execution: lapsed verdict %s left for later: %s", approval.verdict_id, exc)

    # --- recording ---------------------------------------------------------

    def _record_refusal(self, cur, approval: Approval, outcome: Refuse, session: Session) -> None:
        cur.execute(
            "INSERT INTO execution_refusals (risk_verdict_id, reason, details, refused_at) "
            "VALUES (%s, %s, %s, %s)",
            (approval.verdict_id, outcome.reason, Jsonb(outcome.details), session.now),
        )

    def _record_order(self, cur, approval: Approval, order: BrokerOrder, now: datetime) -> None:
        """Recorded unfilled: the next sync applies any fills to positions (E8)."""
        status = "rejected" if order.status == "rejected" else "submitted"
        cur.execute(
            """
            INSERT INTO orders (id, risk_verdict_id, submitted_at, broker_order_id, status,
                                limit_price, broker_reason, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                order_id(approval.verdict_id, approval.order),
                approval.verdict_id,
                order.submitted_at,
                order.broker_order_id,
                status,
                order.limit_price if approval.order.side == "buy" else None,
                order.reason,
                now,
            ),
        )

    def _record_rejection(
        self, cur, approval: Approval, request: OrderRequest, reason: str, now: datetime
    ) -> None:
        cur.execute(
            """
            INSERT INTO orders (id, risk_verdict_id, submitted_at, status, limit_price,
                                broker_reason, updated_at)
            VALUES (%s, %s, %s, 'rejected', %s, %s, %s)
            """,
            (request.client_order_id, approval.verdict_id, now, request.limit_price, reason, now),
        )


# --- reads shared with tests -----------------------------------------------


def session_at(now: datetime) -> Session:
    today = calendar.trading_day(now)
    after_close = not calendar.is_session(today) or now >= calendar.close_time(today)
    return Session(
        now=now, today=today, market_open=calendar.market_open(now), after_close=after_close
    )


def baseline_equity(cur, today) -> Decimal | None:
    """The gate's own rule (002 G8, research E11): the last snapshot on today's New
    York date taken before today's open."""
    if not calendar.is_session(today):
        return None
    cur.execute(
        "SELECT equity FROM account_snapshots "
        "WHERE (taken_at AT TIME ZONE 'America/New_York')::date = %s AND taken_at < %s "
        "ORDER BY taken_at DESC LIMIT 1",
        (today, calendar.open_time(today)),
    )
    row = cur.fetchone()
    return row["equity"] if row else None


def min_equity_since_open(cur, session: Session) -> Decimal | None:
    """The lowest equity recorded since today's open (research E6 row 7)."""
    if not calendar.is_session(session.today):
        return None
    cur.execute(
        "SELECT min(equity) AS low FROM account_snapshots "
        "WHERE (taken_at AT TIME ZONE 'America/New_York')::date = %s "
        "AND taken_at >= %s AND taken_at <= %s",
        (session.today, calendar.open_time(session.today), session.now),
    )
    return cur.fetchone()["low"]


def _open_orders(cur, side: str):
    # orders has no symbol, side or qty; they come from the approval (E6).
    cur.execute(
        """
        SELECT v.approved_order->>'symbol' AS symbol,
               (v.approved_order->>'qty')::numeric - coalesce(o.fill_qty, 0) AS remaining,
               o.limit_price
        FROM orders o JOIN risk_verdicts v ON v.id = o.risk_verdict_id
        WHERE o.status = ANY(%s) AND v.approved_order->>'side' = %s
        """,
        (list(_OPEN_STATUSES), side),
    )
    return cur.fetchall()


def open_buys(cur, symbol: str) -> tuple[Decimal, Decimal]:
    """(unfilled qty of our open buys of `symbol`, unfilled cost of all our open buys)."""
    qty, cost = Decimal(0), Decimal(0)
    for row in _open_orders(cur, "buy"):
        remaining = max(row["remaining"], Decimal(0))
        cost += remaining * row["limit_price"]
        if row["symbol"] == symbol:
            qty += remaining
    return qty, cost


def open_sells(cur, symbol: str) -> Decimal:
    rows = [row for row in _open_orders(cur, "sell") if row["symbol"] == symbol]
    return sum((max(row["remaining"], Decimal(0)) for row in rows), Decimal(0))
