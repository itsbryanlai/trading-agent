-- Feature 003 (Execution): order identifiers per verdict (ADR 0012), the order
-- states and limit price Execution records, refusals of approvals it declined to
-- submit, and its read of the manual pause flag. Decisions:
-- specs/003-execution/research.md E3, E4, E6, E7, E10; data-model.md.
-- orders has no rows in any environment yet (nothing could write it before this
-- feature), so every change is a plain ALTER with no backfill.

-- ADR 0012: `{trading_day}-{symbol}-{side}-{first 8 hex of the verdict id}`, and
-- the suffix must name this row's own verdict.
ALTER TABLE orders
    ADD CONSTRAINT orders_id_format
    CHECK (id ~ '^\d{4}-\d{2}-\d{2}-[A-Z][A-Z0-9.]*-(buy|sell)-[0-9a-f]{8}$');
ALTER TABLE orders
    ADD CONSTRAINT orders_id_names_verdict
    CHECK (right(id, 8) = left(risk_verdict_id::text, 8));
COMMENT ON COLUMN orders.id IS
    'ADR 0012: {trading_day}-{symbol}-{side}-{first 8 hex of risk_verdict_id}; '
    'also the broker''s client order id';

-- `expired`: the broker closed a day order out at the end of the session,
-- filled or not (its `expired` and `done_for_day`, research E7).
ALTER TABLE orders DROP CONSTRAINT orders_status_check;
ALTER TABLE orders
    ADD CONSTRAINT orders_status_check
    CHECK (status IN ('submitted', 'partially_filled', 'filled', 'rejected', 'canceled',
                      'expired'));

-- The live ask a buy was actually submitted at (<= the verdict's ceiling). Every
-- buy has one and no sell does, so the open-buy cost sum can't skip a row (E6).
ALTER TABLE orders ADD COLUMN limit_price numeric(14,4) CHECK (limit_price > 0);
ALTER TABLE orders
    ADD CONSTRAINT orders_buy_has_limit
    CHECK ((id ~ '-buy-[0-9a-f]{8}$') = (limit_price IS NOT NULL));
ALTER TABLE orders ADD COLUMN broker_reason text;

-- An approved verdict Execution declined to submit (FR-007). Insert-only. The
-- same composite key as orders, so a refusal can only name an approved verdict.
CREATE TABLE execution_refusals (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    risk_verdict_id  uuid NOT NULL UNIQUE,
    verdict          text NOT NULL DEFAULT 'approved' CHECK (verdict = 'approved'),
    -- Exactly specs/003-execution/contracts/refusal-reasons.md.
    reason           text NOT NULL CHECK (reason IN (
                         'approval_expired', 'identifier_clash', 'trading_paused',
                         'no_daily_baseline', 'daily_loss_line_crossed',
                         'quote_above_ceiling', 'max_position_pct', 'cash_reserve_pct',
                         'shares_held_differ')),
    details          jsonb NOT NULL CHECK (jsonb_typeof(details) = 'object'),
    refused_at       timestamptz NOT NULL,

    FOREIGN KEY (risk_verdict_id, verdict) REFERENCES risk_verdicts (id, verdict)
);

-- One outcome per approval: an order or a refusal, never both (E4). Each table's
-- UNIQUE (risk_verdict_id) stops two of one kind; this stops one of each.
-- Execution also serializes its own work, so this is a second guard.
CREATE FUNCTION execution_outcome_exclusive() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'orders' THEN
        IF EXISTS (SELECT 1 FROM execution_refusals WHERE risk_verdict_id = NEW.risk_verdict_id) THEN
            RAISE EXCEPTION 'verdict % already has a refusal', NEW.risk_verdict_id
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    ELSIF EXISTS (SELECT 1 FROM orders WHERE risk_verdict_id = NEW.risk_verdict_id) THEN
        RAISE EXCEPTION 'verdict % already has an order', NEW.risk_verdict_id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER orders_outcome_exclusive BEFORE INSERT ON orders
    FOR EACH ROW EXECUTE FUNCTION execution_outcome_exclusive();
CREATE TRIGGER execution_refusals_outcome_exclusive BEFORE INSERT ON execution_refusals
    FOR EACH ROW EXECUTE FUNCTION execution_outcome_exclusive();

-- Grants: exactly specs/001-data-model/contracts/role-grants.md (amended by 003).
REVOKE UPDATE ON orders FROM ta_execution;
GRANT UPDATE (broker_order_id, status, fill_qty, fill_price, broker_reason, updated_at)
    ON orders TO ta_execution;

GRANT SELECT, INSERT ON execution_refusals TO ta_execution;
GRANT SELECT ON execution_refusals TO ta_journal, ta_assistant, ta_dashboard;

-- The manual pause flag only (FR-018); never the halt or baseline columns.
GRANT SELECT (trading_paused) ON system_state TO ta_execution;
