"""Execution's thin service: reads rows as ta_execution, talks to the broker
through the port, runs the pure core, and records the outcome.

It makes no decisions of its own; those are in checks.py, fills.py and
monitor.py. See specs/003-execution/contracts/execution-interface.md and
research.md E4, E5, E13.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from trading_agent.execution import checks, fills, monitor
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
from trading_agent.execution.schedule import monitor_window, pre_open_due
from trading_agent.risk import calendar
from trading_agent.risk.config import RiskConfig, RiskConfigError, load_config
from trading_agent.risk.model import ApprovedOrder

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/risk.yaml")

# One approval at a time, across processes: a key distinct from the gate's
# (research E5). "exec".
_LOCK_KEY = 0x65786563
# Session-level, for the process's lifetime: one Execution at a time. "exe1".
_SINGLE_INSTANCE_KEY = 0x65786531

# After a submission timed out, wait this long for the broker's lookup to catch
# up before trying again, so a lagging lookup can't cause a second order (E5).
MAYBE_PLACED_WAIT = timedelta(minutes=2)

# No submission this close to the close (research E16): the checks ran moments
# earlier, and a day order that reaches the broker after the close is held for the
# next session. An order-logic parameter, not a risk limit.
NO_SUBMIT_BEFORE_CLOSE = timedelta(seconds=30)

_OPEN_STATUSES = ("submitted", "partially_filled")

# Alpaca's order object carries no rejection reason (alpaca-py 0.44), so a
# rejection that arrives by polling rather than at submission records this (FR-012).
NO_BROKER_REASON = "rejected by the broker after acceptance; the broker gave no reason"

# A trigger without a verdict this long after it was recorded means the gate's
# trigger runner isn't running (ADR 0013).
UNEVALUATED_AFTER = timedelta(minutes=5)


class NotAutocommit(Exception):
    """Execution needs each unit of work to be a real transaction (research E5)."""


class AnotherExecutionRunning(Exception):
    """Only one Execution may run at a time (research E16)."""


@dataclass(frozen=True)
class _Unresolved:
    """A submission that timed out: it may be live at the broker (research E5, E16)."""

    since: datetime
    side: str
    symbol: str


@dataclass
class _Held:
    """Per-process memory. Losing it on restart only repeats harmless work, except
    the unresolved placements: see research E16 on what a restart forgets."""

    maybe_placed: dict[UUID, _Unresolved] = field(default_factory=dict)
    # Verdicts whose submission ever timed out: a later rejection of the same
    # identifier is a duplicate of a live order, never a real rejection (E16).
    timed_out_ever: set[UUID] = field(default_factory=set)
    # Stop-loss windows whose check succeeded for every held position (E13).
    windows_done: set[datetime] = field(default_factory=set)
    # Stop-loss windows that already have their account snapshot (ADR 0014).
    snapshot_windows: set[datetime] = field(default_factory=set)
    # symbol -> the windows in which its last trade was stale.
    stale_windows: dict[str, set[datetime]] = field(default_factory=dict)


class Executor:
    """Holds the broker, the ta_execution connection, and a little per-process state."""

    def __init__(
        self,
        broker: Broker,
        conn: psycopg.Connection,
        config_path: Path = DEFAULT_CONFIG_PATH,
        *,
        clock: Callable[[], datetime] | None = None,
        _allow_savepoints: bool = False,
    ) -> None:
        self.broker = broker
        self.conn = conn
        self.config_path = config_path
        # Re-read immediately before each submission (research E16): a tick's own
        # `now` can be seconds stale by then.
        self.clock = clock or (lambda: datetime.now(UTC))
        self._allow_savepoints = _allow_savepoints
        self._held = _Held()

    # --- entry points ------------------------------------------------------

    def startup(self) -> None:
        """The paper-only guard, before anything else touches the broker or the
        database (FR-013); then the connection check (research E5)."""
        self.broker.verify_paper()
        self._require_autocommit()
        # Held for the connection's lifetime: a second Execution (e.g. an overlapping
        # redeploy) would sync and reconcile outside the per-approval lock (E16).
        with self._cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS mine", (_SINGLE_INSTANCE_KEY,))
            if not cur.fetchone()["mine"]:
                raise AnotherExecutionRunning("another Execution holds the single-instance lock")
        report = TickReport()
        self._isolated("positions", report, lambda: self._positions_pass(report))

    def tick(self, now: datetime) -> TickReport:
        """One pass of every duty that is due at `now` (research E13)."""
        self._require_autocommit()
        report = TickReport()
        session = session_at(now)
        # Positions first: the gate sizes from them and the monitor measures from
        # them (research E8).
        self._isolated("positions", report, lambda: self._positions_pass(report))
        self._isolated("sweep", report, lambda: self._sweep_lapsed(session, report))
        pending: list[dict] = []
        self._isolated("approval list", report, lambda: pending.extend(self._pending(session)))
        for row in pending:
            # Parsed inside its own unit: one malformed verdict can't stop the rest,
            # least of all the stop-loss monitor below (research E16).
            self._isolated(
                f"verdict {row['id']}",
                report,
                lambda r=row: self._process_approval(_approval(r), session, report),
            )
        self._isolated("window snapshot", report, lambda: self._window_snapshot(session, report))
        self._isolated("stop-loss monitor", report, lambda: self._run_monitor(session, report))
        self._isolated(
            "unevaluated triggers", report, lambda: self._check_unevaluated(session, report)
        )
        self._isolated("pre-open snapshot", report, lambda: self._pre_open(session, report))
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

    def _pending(self, session: Session) -> list[dict]:
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
                (session.today,),
            )
            return cur.fetchall()

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
                blocker = self._blocked_by_unresolved(approval)
                if blocker is not None:
                    report.retried += 1
                    log.info(
                        "execution: verdict %s waits: placement %s is unresolved",
                        approval.verdict_id,
                        blocker,
                    )
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
        invalid = checks.symbol_refusal(approval)
        if invalid is not None:
            self._record_refusal(cur, approval, invalid, session)
            report.refused += 1
            return True
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
        unresolved = self._held.maybe_placed.get(approval.verdict_id)
        if unresolved is None:
            return False
        if session.now - unresolved.since < MAYBE_PLACED_WAIT:
            return True
        del self._held.maybe_placed[approval.verdict_id]
        return False

    def _blocked_by_unresolved(self, approval: Approval) -> UUID | None:
        """A placement that may be live isn't in the open-order sums (research E16).
        While one is unresolved, no buy goes out (it could breach the reserve or the
        ceiling), and no exit of the same symbol if it is a sell (it could oversell)."""
        for verdict_id, unresolved in self._held.maybe_placed.items():
            if verdict_id == approval.verdict_id:
                continue
            if approval.order.side == "buy":
                return verdict_id
            if unresolved.side == "sell" and unresolved.symbol == approval.order.symbol:
                return verdict_id
        return None

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
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (session.now, account.equity, account.cash, account.buying_power),
        )
        snapshot_id = str(cur.fetchone()["id"])
        lowest = lowest_snapshot_since_open(cur, session)
        live = dataclasses.replace(
            live,
            equity=account.equity,
            cash=account.cash,
            min_equity_since_open=lowest["equity"] if lowest else None,
            snapshot_id=snapshot_id,
            min_snapshot_id=str(lowest["id"]) if lowest else None,
        )
        # Row 7 before any other broker call: a crossing is refused (and the snapshot
        # showing it committed) even if a later call would have failed (E16).
        crossed = checks.loss_line_refusal(live)
        if crossed is not None:
            return crossed
        symbol = approval.order.symbol
        open_qty, open_cost = open_buys(cur, symbol)
        live = dataclasses.replace(
            live,
            held_qty=self._held_qty(symbol),
            open_buy_qty_symbol=open_qty,
            open_buy_cost_all=open_cost,
            ask=self.broker.get_latest_quote(symbol),
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
            # Nothing from a retried attempt is kept, including the pre-buy snapshot,
            # so an unusable quote doesn't write one every minute (T020, T061).
            raise psycopg.Rollback()
        elif isinstance(outcome, Refuse):
            self._record_refusal(cur, approval, outcome, session)
            report.refused += 1
            log.info("execution: verdict %s refused: %s", approval.verdict_id, outcome.reason)
        elif isinstance(outcome, Submit):
            self._submit(cur, approval, outcome.request, session, report)

    def _submit(
        self, cur, approval: Approval, request: OrderRequest, session: Session, report: TickReport
    ) -> None:
        at = self.clock()
        if not submittable(at, approval.order.trading_day):
            # The checks ran against the tick's start; the market is closed or about
            # to close by now. Alpaca would hold a late day order for the next
            # session, unchecked (research E16). Retry; it lapses at the close.
            report.retried += 1
            log.info("execution: verdict %s not submitted at %s", approval.verdict_id, at)
            raise psycopg.Rollback()
        try:
            placed = self.broker.submit_order(request)
        except OrderRejected as exc:
            # A duplicate-id rejection after a lagging lookup must not hide a live
            # order, so ask again before recording a rejection (E5).
            again = self.broker.find_order(request.client_order_id)
            if again is not None:
                self._record_order(cur, approval, again, session.now)
                self._held.maybe_placed.pop(approval.verdict_id, None)
                report.recovered += 1
                return
            if approval.verdict_id in self._held.timed_out_ever:
                # This identifier timed out before: the rejection is the broker
                # refusing a duplicate of a live order. Keep looking; record nothing.
                self._mark_unresolved(approval, at)
                report.retried += 1
                log.warning(
                    "execution: %s rejected after an earlier timeout (%s); treated as live",
                    request.client_order_id,
                    exc.reason,
                )
                raise psycopg.Rollback() from exc
            self._record_rejection(cur, approval, request, exc.reason, session.now)
            report.refused += 1
            log.warning("execution: broker rejected %s: %s", request.client_order_id, exc.reason)
            return
        except BrokerUnavailable:
            # Maybe placed: roll back, and let a later lookup settle it (E5).
            self._mark_unresolved(approval, at)
            self._held.timed_out_ever.add(approval.verdict_id)
            raise
        self._record_order(cur, approval, placed, session.now)
        report.submitted += 1
        log.info("execution: submitted %s", request.client_order_id)

    def _mark_unresolved(self, approval: Approval, since: datetime) -> None:
        self._held.maybe_placed[approval.verdict_id] = _Unresolved(
            since, approval.order.side, approval.order.symbol
        )

    # --- fills and positions (E7, E8) --------------------------------------

    def _positions_pass(self, report: TickReport) -> None:
        """Fills, then reconciliation, in one transaction: another process (the gate)
        never sees a fill applied but not yet reconciled (research E16). Each order
        and each symbol is its own savepoint, so one bad row can't block the rest."""
        with self.conn.transaction():
            self._sync_orders(report)
            self._reconcile_positions(report)

    def _sync_orders(self, report: TickReport) -> None:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.broker_order_id, o.fill_qty, o.fill_price,
                       v.approved_order->>'symbol' AS symbol,
                       v.approved_order->>'side' AS side
                FROM orders o JOIN risk_verdicts v ON v.id = o.risk_verdict_id
                WHERE o.status = ANY(%s) AND o.broker_order_id IS NOT NULL
                ORDER BY o.submitted_at, o.id
                """,
                (list(_OPEN_STATUSES),),
            )
            rows = cur.fetchall()
        for row in rows:
            self._isolated(f"order {row['id']}", report, lambda r=row: self._sync_one(r, report))

    def _sync_one(self, row: dict, report: TickReport) -> None:
        placed = self.broker.get_order(row["broker_order_id"])
        try:
            status = fills.map_status(placed.status, placed.filled_qty)
        except fills.UnexpectedStatus:
            report.stuck_orders += 1
            log.error(
                "execution: order %s (%s %s) has unexpected broker status %r and stays open; "
                "it counts against %s, so %s of %s may be blocked until the owner resolves it",
                row["id"],
                row["side"],
                row["symbol"],
                placed.status,
                "shares available to sell" if row["side"] == "sell" else "cash and the position",
                "exits" if row["side"] == "sell" else "buys",
                row["symbol"],
            )
            return
        delta = fills.fill_delta(
            row["fill_qty"] or Decimal(0),
            row["fill_price"],
            placed.filled_qty,
            placed.filled_avg_price,
        )
        # The order and the position it moves change together, or neither does.
        with self.conn.transaction(), self._cursor() as cur:
            if delta is not None:
                self._apply_fill(cur, row["symbol"], row["side"], *delta)
                report.fills_applied += 1
            reason = None
            if status == "rejected":
                reason = placed.reason or NO_BROKER_REASON
                log.warning(
                    "execution: broker rejected %s after accepting it: %s", row["id"], reason
                )
            cur.execute(
                "UPDATE orders SET status = %s, fill_qty = %s, fill_price = %s, "
                "broker_reason = coalesce(%s, broker_reason), updated_at = now() WHERE id = %s",
                (status, placed.filled_qty, placed.filled_avg_price, reason, row["id"]),
            )

    def _apply_fill(self, cur, symbol: str, side: str, qty: Decimal, price: Decimal) -> None:
        cur.execute(
            "SELECT qty, avg_entry_price FROM positions WHERE symbol = %s FOR UPDATE", (symbol,)
        )
        current = cur.fetchone()
        holding = fills.Holding(current["qty"], current["avg_entry_price"]) if current else None
        try:
            after = fills.apply_fill(holding, side, qty, price)
        except ValueError as exc:
            # The table was already out of step; reconciliation corrects it next.
            log.warning("execution: fill for %s not applied to positions: %s", symbol, exc)
            return
        _write_position(cur, symbol, after)

    def _reconcile_positions(self, report: TickReport) -> None:
        """The broker is the record of what is held (FR-011). Each symbol is its own
        unit, so one row the table refuses can't block the others (research E16)."""
        broker_positions = {p.symbol: p for p in self.broker.get_positions()}
        with self._cursor() as cur:
            cur.execute("SELECT symbol, qty, avg_entry_price FROM positions")
            ours = {row["symbol"]: row for row in cur.fetchall()}
        for symbol in sorted(set(broker_positions) | set(ours)):
            self._isolated(
                f"position {symbol}",
                report,
                lambda s=symbol: self._reconcile_one(
                    s, broker_positions.get(s), ours.get(s), report
                ),
            )

    def _reconcile_one(self, symbol: str, theirs, mine, report: TickReport) -> None:
        if theirs is not None and theirs.qty <= 0:
            log.error(
                "execution: broker reports %s %s of %s; long-only, left for the owner",
                "a short" if theirs.qty < 0 else "zero",
                theirs.qty,
                symbol,
            )
            return
        want = (
            None if theirs is None else fills.Holding(theirs.qty, _stored(theirs.avg_entry_price))
        )
        have = None if mine is None else fills.Holding(mine["qty"], mine["avg_entry_price"])
        if want == have:
            return
        log.warning(
            "execution: position %s disagrees with the broker (ours %s, broker's %s); "
            "adopting the broker's",
            symbol,
            have,
            want,
        )
        with self.conn.transaction(), self._cursor() as cur:
            _write_position(cur, symbol, want)
        report.reconciled += 1

    # --- the stop-loss monitor (FR-014, E13) -------------------------------

    def _run_monitor(self, session: Session, report: TickReport) -> None:
        window = monitor_window(session.now)
        if window is None:
            return
        config = self._load_config()
        if config is None:
            log.error(
                "execution: STOP-LOSS MONITOR IS OFF: the risk config failed to load, so "
                "no position is being checked against its stop-loss line"
            )
            return
        start = window[0]
        if start in self._held.windows_done:
            return
        with self._cursor() as cur:
            cur.execute("SELECT symbol, qty, avg_entry_price FROM positions")
            holdings = {
                row["symbol"]: fills.Holding(row["qty"], row["avg_entry_price"])
                for row in cur.fetchall()
            }
            skip = exits_on_their_way(cur, session.today)
        trades, quotes = {}, {}
        for symbol in holdings:
            if symbol in skip:
                continue
            try:
                trades[symbol] = self.broker.get_latest_trade(symbol)
            except BrokerUnavailable as exc:
                log.warning("execution: no last trade for %s: %s", symbol, exc)
                trades[symbol] = None
            # The second reading (ADR 0014); only consulted if the trade breaches.
            try:
                quotes[symbol] = self.broker.get_latest_quote(symbol)
            except BrokerUnavailable as exc:
                log.warning("execution: no quote for %s: %s", symbol, exc)
                quotes[symbol] = None
        result = monitor.scan(holdings, trades, quotes, config.stop_loss_pct, session.now, skip)
        for breach in result.breaches:
            # Committed on its own, so the gate's process can see it (ADR 0013).
            with self.conn.transaction(), self._cursor() as cur:
                cur.execute(
                    "INSERT INTO stop_loss_triggers (symbol, observed_price, observed_at) "
                    "VALUES (%s, %s, %s)",
                    (breach.symbol, breach.price, session.now),
                )
            report.triggers += 1
            log.warning(
                "execution: stop-loss trigger for %s at %s (line %s)",
                breach.symbol,
                breach.price,
                breach.line,
            )
        for symbol in result.stale:
            seen = self._held.stale_windows.setdefault(symbol, set())
            seen.add(start)
            if len(seen) >= 2:
                log.error(
                    "execution: %s has had no fresh trade for %d windows; it is going "
                    "unprotected by the stop-loss monitor",
                    symbol,
                    len(seen),
                )
        for symbol in set(self._held.stale_windows) - set(result.stale):
            if symbol not in result.failed:
                del self._held.stale_windows[symbol]
        if result.complete:
            self._held.windows_done.add(start)

    def _pre_open(self, session: Session, report: TickReport) -> None:
        """The gate's daily-loss baseline (FR-015). Checked in the table, so it
        survives restarts; a broker failure just means the next tick tries again."""
        with self._cursor() as cur:
            exists = baseline_equity(cur, session.today) is not None
        if not pre_open_due(session.now, exists):
            return
        try:
            account = self.broker.get_account()
        except BrokerUnavailable as exc:
            log.warning("execution: pre-open snapshot not taken yet: %s", exc)
            return
        with self.conn.transaction(), self._cursor() as cur:
            cur.execute(
                "INSERT INTO account_snapshots (taken_at, equity, cash, buying_power) "
                "VALUES (%s, %s, %s, %s)",
                (session.now, account.equity, account.cash, account.buying_power),
            )
        report.snapshot_taken = True
        log.info("execution: pre-open snapshot, equity %s", account.equity)

    def _window_snapshot(self, session: Session, report: TickReport) -> None:
        """One account snapshot per 30-minute window during market hours, so a crossing
        of the daily-loss line is recorded within 30 minutes (ADR 0014, FR-015)."""
        window = monitor_window(session.now)
        if window is None or window[0] in self._held.snapshot_windows:
            return
        try:
            account = self.broker.get_account()
        except BrokerUnavailable as exc:
            log.warning("execution: window snapshot not taken yet: %s", exc)
            return
        with self.conn.transaction(), self._cursor() as cur:
            cur.execute(
                "INSERT INTO account_snapshots (taken_at, equity, cash, buying_power) "
                "VALUES (%s, %s, %s, %s)",
                (session.now, account.equity, account.cash, account.buying_power),
            )
        self._held.snapshot_windows.add(window[0])
        report.snapshot_taken = True

    def _check_unevaluated(self, session: Session, report: TickReport) -> None:
        """A trigger the gate hasn't evaluated within minutes means its runner isn't
        running, and stop-loss exits are waiting (ADR 0013)."""
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT t.id, t.symbol, t.observed_at FROM stop_loss_triggers t
                WHERE (t.observed_at AT TIME ZONE 'America/New_York')::date = %s
                  AND t.observed_at <= %s
                  AND NOT EXISTS (SELECT 1 FROM risk_verdicts v
                                  WHERE v.stop_loss_trigger_id = t.id)
                """,
                (session.today, session.now - UNEVALUATED_AFTER),
            )
            stuck = cur.fetchall()
        for row in stuck:
            log.error(
                "execution: stop-loss trigger %s for %s (at %s) unevaluated; is the gate's "
                "trigger runner running?",
                row["id"],
                row["symbol"],
                row["observed_at"].isoformat(),
            )
        report.unevaluated_triggers = len(stuck)

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
                (session.today, session.today, lapse_today(session)),
            )
            rows = cur.fetchall()
        for row in rows:
            self._isolated(
                f"lapsed verdict {row['id']}",
                report,
                lambda r=row: self._expire(_approval(r), session, report),
            )

    def _expire(self, approval: Approval, session: Session, report: TickReport) -> None:
        try:
            with self.conn.transaction(), self._cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
                if self._has_outcome(cur, approval.verdict_id):
                    self._held.maybe_placed.pop(approval.verdict_id, None)
                    return
                # No wait for an unresolved placement here: submissions stop 30 s
                # before the close and today's approvals lapse only 2 minutes after
                # it (lapse_today), so any wait has already run out.
                if self._settle_from_broker(cur, approval, session, report):
                    return
                self._held.maybe_placed.pop(approval.verdict_id, None)
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

_FOUR_PLACES = Decimal("0.0001")


def _stored(value: Decimal) -> Decimal:
    """What a numeric(14,4) column would hold."""
    return value.quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP)


def _write_position(cur, symbol: str, holding) -> None:
    if holding is None:
        cur.execute("DELETE FROM positions WHERE symbol = %s", (symbol,))
        return
    cur.execute(
        """
        INSERT INTO positions (symbol, qty, avg_entry_price, updated_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (symbol) DO UPDATE
        SET qty = EXCLUDED.qty, avg_entry_price = EXCLUDED.avg_entry_price, updated_at = now()
        """,
        (symbol, holding.qty, _stored(holding.avg_entry_price)),
    )


def _approval(row: dict) -> Approval:
    return Approval(row["id"], ApprovedOrder.from_json(row["approved_order"]))


def submittable(at: datetime, trading_day) -> bool:
    """Open, on the approval's own trading day, and not in the final seconds before
    the close (research E16)."""
    if calendar.trading_day(at) != trading_day or not calendar.market_open(at):
        return False
    return at < calendar.close_time(trading_day) - NO_SUBMIT_BEFORE_CLOSE


def lapse_today(session: Session) -> bool:
    """Today's approvals lapse once the close is past by the maybe-placed wait, so a
    submission that timed out just before the close is looked up first (E16)."""
    if not session.after_close:
        return False
    if not calendar.is_session(session.today):
        return True
    return session.now >= calendar.close_time(session.today) + MAYBE_PLACED_WAIT


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


def lowest_snapshot_since_open(cur, session: Session) -> dict | None:
    """The snapshot with the lowest equity since today's open (research E6 row 7),
    as {id, equity}, or None."""
    if not calendar.is_session(session.today):
        return None
    cur.execute(
        "SELECT id, equity FROM account_snapshots "
        "WHERE (taken_at AT TIME ZONE 'America/New_York')::date = %s "
        "AND taken_at >= %s AND taken_at <= %s "
        "ORDER BY equity, taken_at LIMIT 1",
        (session.today, calendar.open_time(session.today), session.now),
    )
    return cur.fetchone()


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


def exits_on_their_way(cur, today) -> frozenset[str]:
    """Symbols with an open sell, a trigger from today the gate hasn't evaluated,
    or an approved exit from today with no outcome yet (research E13)."""
    cur.execute(
        """
        SELECT v.approved_order->>'symbol' AS symbol
        FROM orders o JOIN risk_verdicts v ON v.id = o.risk_verdict_id
        WHERE o.status = ANY(%(open)s) AND v.approved_order->>'side' = 'sell'
        UNION
        SELECT t.symbol FROM stop_loss_triggers t
        WHERE (t.observed_at AT TIME ZONE 'America/New_York')::date = %(today)s
          AND NOT EXISTS (SELECT 1 FROM risk_verdicts v WHERE v.stop_loss_trigger_id = t.id)
        UNION
        SELECT v.approved_order->>'symbol' FROM risk_verdicts v
        WHERE v.verdict = 'approved' AND v.trading_day = %(today)s
          AND v.approved_order->>'side' = 'sell'
          AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.risk_verdict_id = v.id)
          AND NOT EXISTS (SELECT 1 FROM execution_refusals r WHERE r.risk_verdict_id = v.id)
        """,
        {"open": list(_OPEN_STATUSES), "today": today},
    )
    return frozenset(row["symbol"] for row in cur.fetchall())


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
