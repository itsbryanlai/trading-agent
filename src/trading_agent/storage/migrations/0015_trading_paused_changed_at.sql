-- Feature 014 (the dashboard): when the pause last changed, recorded by the database.
-- Decisions: ADR 0027, specs/014-dashboard research.md D22 and data-model.md.
--
-- The dashboard's banner and confirmation page say how long trading has been paused.
-- system_state.updated_at can't tell them: the Risk Gate sets it every trading day when it
-- records the daily-loss baseline. A trigger stamps the new column whenever trading_paused
-- changes value, whichever role or path (the dashboard's toggle, or the runbook's manual
-- UPDATE as the owner's control login) made the change. So no role is granted a write on
-- the column, and ta_dashboard_control keeps exactly UPDATE (trading_paused, updated_at):
-- column privileges are checked against an UPDATE's own SET list, not against values a
-- trigger assigns.
--
-- Null until the first change after this migration: nothing is backfilled from updated_at.
-- The dashboard shows a null as "start time not recorded". No grant is added or changed;
-- the roles with table-wide SELECT on system_state read the column, and the orchestrator
-- and Execution keep SELECT (trading_paused) alone. Nothing in the trade path reads it.
ALTER TABLE system_state ADD COLUMN trading_paused_changed_at timestamptz;

CREATE FUNCTION system_state_trading_paused_changed() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.trading_paused IS DISTINCT FROM OLD.trading_paused THEN
        NEW.trading_paused_changed_at := now();
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER system_state_trading_paused_changed
    BEFORE UPDATE ON system_state
    FOR EACH ROW EXECUTE FUNCTION system_state_trading_paused_changed();

COMMENT ON COLUMN system_state.trading_paused_changed_at IS 'When trading_paused last changed value, stamped by a trigger (ADR 0027); null until the first change after migration 0015.';
