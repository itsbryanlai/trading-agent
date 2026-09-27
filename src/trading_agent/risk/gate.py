"""The Risk Gate's pure core: (request, context, config) -> GateResult.

No I/O, no clock, no environment (FR-002). All arithmetic is Decimal (G2).
Rule precedence is specs/002-risk-gate/contracts/rejection-rules.md; sizing is
research.md G3-G4.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal

from trading_agent.risk import rules
from trading_agent.risk.config import RiskConfig
from trading_agent.risk.model import (
    ApprovedOrder,
    Context,
    DecisionRequest,
    GateResult,
    Request,
    StopLossRequest,
    Verdict,
)

_HUNDRED = Decimal(100)
_CENT = Decimal("0.01")


def _floor(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def _ceil(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def price_ceiling(quote: Decimal, tolerance_pct: Decimal) -> Decimal:
    """The most a buy may pay: quote plus tolerance, rounded down to the cent."""
    return (quote * (1 + tolerance_pct / _HUNDRED)).quantize(_CENT, rounding=ROUND_DOWN)


def evaluate(request: Request, context: Context, config: RiskConfig) -> GateResult:
    if not context.market_open:
        return GateResult(Verdict.reject(rules.MARKET_CLOSED), config.version, context.trading_day)

    # Runs on every evaluation, whatever the request, so the first evaluation after
    # a bad drop records the halt even if it's an exit (FR-009, SC-004). It only
    # ever rejects buys; an exit's verdict is unaffected.
    crossed = _loss_line_crossed(context, config)

    if isinstance(request, StopLossRequest):
        verdict = _stop_loss(request, context, config)
    elif request.direction == "sell":
        verdict = _sell(request, context)
    else:
        verdict = _buy(request, context, config, crossed)
    return GateResult(verdict, config.version, context.trading_day, record_halt=crossed)


def _loss_line_crossed(ctx: Context, config: RiskConfig) -> bool:
    """True when today's equity is at or below the loss line and the halt isn't yet recorded."""
    if ctx.halt_active or ctx.equity is None or ctx.baseline_equity is None:
        return False
    line = ctx.baseline_equity * (1 - config.daily_loss_halt_pct / _HUNDRED)
    return ctx.equity <= line


def _stop_loss(request: StopLossRequest, ctx: Context, config: RiskConfig) -> Verdict:
    """Confirm a monitor's observation against the position's own entry (G13, FR-012).

    Only the observed price comes from the trigger; the entry price and the line
    are the gate's own, so a faulty monitor can't force a sale. No pause, halt,
    cap, missing account data or universe rule can block a genuine breach.
    """
    if ctx.shares_held <= 0 or ctx.avg_entry_price is None:
        return Verdict.reject(rules.NO_POSITION)
    line = ctx.avg_entry_price * (1 - config.stop_loss_pct / _HUNDRED)
    if request.observed_price > line:
        return Verdict.reject(rules.STOP_LOSS_NOT_BREACHED)
    return Verdict.approve(_market_sell(request.symbol, ctx.shares_held, ctx, source="stop_loss"))


def _sell(request: DecisionRequest, ctx: Context) -> Verdict:
    held = ctx.shares_held
    if held <= 0:
        return Verdict.reject(rules.NO_POSITION)
    target = request.target_weight_pct / _HUNDRED
    if target == 0:
        return Verdict.approve(_market_sell(request.symbol, held, ctx, source="decision"))
    if ctx.equity is None:
        return Verdict.reject(rules.NO_ACCOUNT_SNAPSHOT_TODAY)

    quote, equity = request.quote, ctx.equity
    if target * equity > held * quote + quote:
        return Verdict.reject(rules.DIRECTION_CONTRADICTS_TARGET)
    keep = _ceil(target * equity / quote)
    qty = held - keep
    if qty < 1:
        return Verdict.reject(rules.TARGET_ALREADY_MET)
    return Verdict.approve(_market_sell(request.symbol, qty, ctx, source="decision"))


def _buy(request: DecisionRequest, ctx: Context, config: RiskConfig, crossed: bool) -> Verdict:
    stop = _account_stop(ctx, config, crossed) or _universe_stop(ctx.reference, config)
    if stop:
        return Verdict.reject(stop)

    equity, cash, quote, held = ctx.equity, ctx.cash, request.quote, ctx.shares_held
    target = request.target_weight_pct / _HUNDRED
    ceiling = price_ceiling(quote, config.max_buy_price_tolerance_pct)

    if target * equity < held * quote - quote:
        return Verdict.reject(rules.DIRECTION_CONTRADICTS_TARGET)
    wanted = _floor((target * equity - held * quote) / ceiling)
    if wanted < 1:
        return Verdict.reject(rules.TARGET_ALREADY_MET)

    # Existing shares and new ones are both valued at the ceiling: a fill at the
    # top of the tolerance must still respect both limits (G3).
    position_room = _floor(config.max_position_pct / _HUNDRED * equity / ceiling) - held
    cash_room = _floor((cash - config.cash_reserve_pct / _HUNDRED * equity) / ceiling)

    if position_room < 1:
        return Verdict.reject(rules.MAX_POSITION_PCT)
    if cash_room < 1:
        return Verdict.reject(rules.CASH_RESERVE_PCT)

    trims = tuple(
        name
        for name, room in (
            (rules.MAX_POSITION_PCT, position_room),
            (rules.CASH_RESERVE_PCT, cash_room),
        )
        if room < wanted
    )
    return Verdict.approve(
        ApprovedOrder(
            symbol=request.symbol,
            side="buy",
            qty=min(wanted, position_room, cash_room),
            order_type="limit",
            limit_price=ceiling,
            trading_day=ctx.trading_day,
            exposure="increase",
            source="decision",
            trims=trims,
        )
    )


def _account_stop(ctx: Context, config: RiskConfig, crossed: bool) -> str | None:
    """Account-wide stops that block every new exposure, in contract order."""
    if ctx.trading_paused:
        return rules.TRADING_PAUSED
    if ctx.equity is None or ctx.cash is None:
        return rules.NO_ACCOUNT_SNAPSHOT_TODAY
    if ctx.baseline_equity is None:
        return rules.NO_DAILY_BASELINE
    if ctx.halt_active or crossed:
        return rules.DAILY_LOSS_HALT
    if ctx.increase_orders_approved_today >= config.max_orders_per_day:
        return rules.DAILY_ORDER_CAP
    return None


def _universe_stop(reference, config: RiskConfig) -> str | None:
    """Independent universe re-check against today's reference data (FR-011)."""
    if reference is None:
        return rules.UNIVERSE_NO_REFERENCE_DATA
    if (
        reference.security_type != "common_stock"
        or reference.exchange_mic not in rules.US_LISTED_MICS
    ):
        return rules.UNIVERSE_LISTING
    floors = config.universe
    if reference.market_cap_usd < floors.min_market_cap_usd:
        return rules.UNIVERSE_MARKET_CAP
    if reference.avg_daily_dollar_volume_usd < floors.min_avg_daily_dollar_volume_usd:
        return rules.UNIVERSE_DOLLAR_VOLUME
    if reference.share_price_usd < floors.min_share_price_usd:
        return rules.UNIVERSE_SHARE_PRICE
    return None


def _market_sell(symbol: str, qty: int, ctx: Context, *, source: str) -> ApprovedOrder:
    return ApprovedOrder(
        symbol=symbol,
        side="sell",
        qty=qty,
        order_type="market",
        limit_price=None,
        trading_day=ctx.trading_day,
        exposure="decrease",
        source=source,
    )


def choose_baseline(
    stored_for_today: Decimal | None, latest_snapshot_before_open: Decimal | None
) -> tuple[Decimal | None, bool]:
    """(baseline equity, whether it must be recorded). Stored wins; else pre-open (G8)."""
    if stored_for_today is not None:
        return stored_for_today, False
    if latest_snapshot_before_open is not None:
        return latest_snapshot_before_open, True
    return None, False
