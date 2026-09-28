"""Refusal reasons written to execution_refusals.reason.

These strings are a contract (specs/003-execution/contracts/refusal-reasons.md);
the dashboard, Assistant and journal display and count them. A test keeps this
module and that document in lockstep. A refusal is final: the approval is spent.
"""

APPROVAL_EXPIRED = "approval_expired"
INVALID_SYMBOL = "invalid_symbol"
IDENTIFIER_CLASH = "identifier_clash"
TRADING_PAUSED = "trading_paused"
NO_DAILY_BASELINE = "no_daily_baseline"
DAILY_LOSS_LINE_CROSSED = "daily_loss_line_crossed"
QUOTE_ABOVE_CEILING = "quote_above_ceiling"
MAX_POSITION_PCT = "max_position_pct"
CASH_RESERVE_PCT = "cash_reserve_pct"
SHARES_HELD_DIFFER = "shares_held_differ"

ALL = frozenset(
    {
        APPROVAL_EXPIRED,
        INVALID_SYMBOL,
        IDENTIFIER_CLASH,
        TRADING_PAUSED,
        NO_DAILY_BASELINE,
        DAILY_LOSS_LINE_CROSSED,
        QUOTE_ABOVE_CEILING,
        MAX_POSITION_PCT,
        CASH_RESERVE_PCT,
        SHARES_HELD_DIFFER,
    }
)
