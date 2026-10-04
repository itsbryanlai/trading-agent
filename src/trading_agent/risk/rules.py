"""Rule names written to risk_verdicts.rejection_rule and approved_order.trims.

These strings are a contract (specs/002-risk-gate/contracts/rejection-rules.md);
the dashboard, Assistant and journal display and count them. A test keeps this
module and that document in lockstep.
"""

MARKET_CLOSED = "market_closed"
DECISION_STALE = "decision_stale"

STOP_LOSS_TRIGGER_STALE = "stop_loss_trigger_stale"
NO_POSITION = "no_position"
STOP_LOSS_NOT_BREACHED = "stop_loss_not_breached"
DIRECTION_CONTRADICTS_TARGET = "direction_contradicts_target"
TARGET_ALREADY_MET = "target_already_met"

TRADING_PAUSED = "trading_paused"
NO_ACCOUNT_SNAPSHOT_TODAY = "no_account_snapshot_today"
NO_DAILY_BASELINE = "no_daily_baseline"
DAILY_LOSS_HALT = "daily_loss_halt"
DAILY_ORDER_CAP = "daily_order_cap"
UNIVERSE_NO_REFERENCE_DATA = "universe_no_reference_data"
UNIVERSE_LISTING = "universe_listing"
UNIVERSE_MARKET_CAP = "universe_market_cap"
UNIVERSE_DOLLAR_VOLUME = "universe_dollar_volume"
UNIVERSE_SHARE_PRICE = "universe_share_price"
MAX_POSITION_PCT = "max_position_pct"
CASH_RESERVE_PCT = "cash_reserve_pct"

# `listing: us_common_equity` means common stock on one of these exchanges.
# Excludes OTC (not a listed MIC), ETFs including leveraged/inverse, ADRs, options.
US_LISTED_MICS = frozenset({"XNYS", "XNAS", "XASE"})
