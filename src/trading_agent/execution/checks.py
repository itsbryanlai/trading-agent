"""Execution's buy and exit checks, in the fixed order of research E6.

Pure: plain values in, one of Submit / Refuse / Retry out. A Refuse is final
(the approval is spent); a Retry is tried again next tick while the approval is
still valid. Decimal arithmetic only.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from trading_agent.execution import reasons
from trading_agent.execution.broker import OrderRequest
from trading_agent.execution.ids import is_valid, order_id
from trading_agent.execution.model import (
    Approval,
    BuyLive,
    ExitLive,
    Outcome,
    Refuse,
    Retry,
    Session,
    Submit,
)

# An ask older than this isn't a live price (research E9). Not a risk limit.
MAX_QUOTE_AGE = timedelta(seconds=60)
_HUNDRED = Decimal(100)
_CENT = Decimal("0.01")


def expiry_check(approval: Approval, session: Session) -> Outcome | None:
    """Row 1-2: an approval from a day that is over is spent; before the open, wait."""
    day = approval.order.trading_day
    if day != session.today or session.after_close:
        return Refuse(
            reasons.APPROVAL_EXPIRED,
            {"trading_day": day.isoformat(), "now": session.now.isoformat()},
        )
    if not session.market_open:
        return Retry("market not open yet")
    return None


def symbol_refusal(approval: Approval) -> Outcome | None:
    """Row 1a: an identifier the database would refuse means an order that could be
    placed but never recorded (research E16)."""
    oid = order_id(approval.verdict_id, approval.order)
    if is_valid(oid):
        return None
    return Refuse(reasons.INVALID_SYMBOL, {"symbol": approval.order.symbol, "order_id": oid})


def clash_refusal(approval: Approval, clash_with) -> Outcome | None:
    if clash_with is None:
        return None
    return Refuse(
        reasons.IDENTIFIER_CLASH,
        {
            "order_id": order_id(approval.verdict_id, approval.order),
            "other_verdict_id": str(clash_with),
        },
    )


def daily_loss_line(baseline: Decimal, halt_pct: Decimal) -> Decimal:
    return baseline * (1 - halt_pct / _HUNDRED)


def loss_line_refusal(live: BuyLive) -> Refuse | None:
    """Row 7: the live equity or any snapshot since the open at or below the line
    halts buys for the rest of the day (Constitution IV). Needs `equity`, `baseline`
    and `config`; the service calls it right after the pre-buy snapshot, before any
    other broker call, so a crossing is never lost to a later failure (E16)."""
    line = daily_loss_line(live.baseline, live.config.daily_loss_halt_pct)
    lowest = live.equity
    if live.min_equity_since_open is not None:
        lowest = min(lowest, live.min_equity_since_open)
    if lowest > line:
        return None
    return Refuse(
        reasons.DAILY_LOSS_LINE_CROSSED,
        {
            "equity": str(live.equity),
            "snapshot_id": live.snapshot_id,
            "min_equity_since_open": _str(live.min_equity_since_open),
            "min_snapshot_id": live.min_snapshot_id,
            "baseline": str(live.baseline),
            "line": str(line),
        },
    )


def precheck_buy(
    approval: Approval, live: BuyLive, session: Session, clash_with=None
) -> Outcome | None:
    """Rows 1-6: everything decidable before fetching the account or a quote."""
    for outcome in (
        expiry_check(approval, session),
        symbol_refusal(approval),
        clash_refusal(approval, clash_with),
    ):
        if outcome is not None:
            return outcome
    if live.config is None:
        return Retry("risk config failed to load")
    if live.paused:
        return Refuse(reasons.TRADING_PAUSED)
    if live.baseline is None:
        return Refuse(reasons.NO_DAILY_BASELINE, {"trading_day": session.today.isoformat()})
    return None


def check_buy(approval: Approval, live: BuyLive, session: Session, clash_with=None) -> Outcome:
    early = precheck_buy(approval, live, session, clash_with)
    if early is not None:
        return early
    order = approval.order
    config = live.config
    if live.equity is None or live.cash is None:
        return Retry("account not fetched")

    crossed = loss_line_refusal(live)
    if crossed is not None:
        return crossed

    # Row 8: a usable live ask.
    quote = live.ask
    if quote is None or quote.ask <= 0:
        return Retry("no live ask")
    if session.now - quote.timestamp > MAX_QUOTE_AGE:
        return Retry("live ask is stale")
    ask = quote.ask

    # Row 9: at or under the verdict's ceiling.
    if ask > order.limit_price:
        return Refuse(
            reasons.QUOTE_ABOVE_CEILING,
            {
                "ask": str(ask),
                "ceiling": str(order.limit_price),
                "quote_time": quote.timestamp.isoformat(),
            },
        )

    qty = Decimal(order.qty)
    # Row 10: position ceiling, counting our own unfilled buys of this symbol.
    position_value = (live.held_qty + live.open_buy_qty_symbol + qty) * ask
    position_limit = config.max_position_pct / _HUNDRED * live.equity
    if position_value > position_limit:
        return Refuse(
            reasons.MAX_POSITION_PCT,
            {
                "held": str(live.held_qty),
                "open_buy_qty": str(live.open_buy_qty_symbol),
                "qty": order.qty,
                "ask": str(ask),
                "equity": str(live.equity),
                "limit_pct": str(config.max_position_pct),
            },
        )

    # Row 11: cash reserve, counting our own unfilled buys of every symbol.
    cash_after = live.cash - live.open_buy_cost_all - qty * ask
    reserve = config.cash_reserve_pct / _HUNDRED * live.equity
    if cash_after < reserve:
        return Refuse(
            reasons.CASH_RESERVE_PCT,
            {
                "cash": str(live.cash),
                "open_buy_cost": str(live.open_buy_cost_all),
                "qty": order.qty,
                "ask": str(ask),
                "equity": str(live.equity),
                "reserve_pct": str(config.cash_reserve_pct),
            },
        )

    # Rounding down never exceeds the ceiling.
    limit_price = ask.quantize(_CENT, rounding=ROUND_DOWN)
    return Submit(
        OrderRequest(
            client_order_id=order_id(approval.verdict_id, order),
            symbol=order.symbol,
            side="buy",
            qty=order.qty,
            order_type="limit",
            limit_price=limit_price,
        )
    )


def check_exit(approval: Approval, live: ExitLive, session: Session, clash_with=None) -> Outcome:
    """Exits take no equity, baseline, pause or config input: nothing but their
    own conditions can refuse them (FR-006, FR-018, FR-020)."""
    for outcome in (
        expiry_check(approval, session),
        symbol_refusal(approval),
        clash_refusal(approval, clash_with),
    ):
        if outcome is not None:
            return outcome
    order = approval.order
    available = live.held_qty - live.open_sell_qty_symbol
    if available < order.qty:
        return Refuse(
            reasons.SHARES_HELD_DIFFER,
            {
                "held": str(live.held_qty),
                "open_sell_qty": str(live.open_sell_qty_symbol),
                "qty": order.qty,
            },
        )
    return Submit(
        OrderRequest(
            client_order_id=order_id(approval.verdict_id, order),
            symbol=order.symbol,
            side="sell",
            qty=order.qty,
            order_type="market",
        )
    )


def _str(value) -> str | None:
    return None if value is None else str(value)
