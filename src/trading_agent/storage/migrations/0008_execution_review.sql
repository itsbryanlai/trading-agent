-- Feature 003, after the adversarial review (specs/003-execution research E16):
-- a refusal for an approval whose order identifier the database would refuse,
-- decided before any broker call so no order is placed that couldn't be recorded.

ALTER TABLE execution_refusals DROP CONSTRAINT execution_refusals_reason_check;
ALTER TABLE execution_refusals
    ADD CONSTRAINT execution_refusals_reason_check
    CHECK (reason IN (
        'approval_expired', 'invalid_symbol', 'identifier_clash', 'trading_paused',
        'no_daily_baseline', 'daily_loss_line_crossed', 'quote_above_ceiling',
        'max_position_pct', 'cash_reserve_pct', 'shares_held_differ'));
