-- US4: the single control row, and the view every reader uses.

-- At most one row: the primary key is a boolean that can only be true.
CREATE TABLE system_state (
    id                     boolean PRIMARY KEY DEFAULT true CHECK (id),
    -- The dashboard's manual pause toggle. The orchestrator checks it before
    -- invoking the Portfolio Manager.
    trading_paused         boolean NOT NULL DEFAULT false,
    -- The trading day the Risk Gate saw the daily-loss line crossed. Storing the
    -- date rather than a boolean means the halt clears itself at the next
    -- trading day with no write from anyone (research.md R9).
    halt_triggered_on      date,
    baseline_trading_day   date,
    daily_starting_equity  numeric(16,2) CHECK (daily_starting_equity > 0),
    updated_at             timestamptz NOT NULL DEFAULT now()
);

INSERT INTO system_state DEFAULT VALUES;

-- The current trading date is the date in New York. A halt is only ever
-- recorded on a trading day and only equality with today is checked, so
-- weekends and holidays need no calendar here.
CREATE VIEW system_state_effective WITH (security_invoker = true) AS
WITH today AS (
    SELECT (now() AT TIME ZONE 'America/New_York')::date AS d
)
SELECT
    s.trading_paused,
    COALESCE(s.halt_triggered_on = t.d, false) AS daily_loss_halt_active,
    CASE WHEN s.baseline_trading_day = t.d THEN s.daily_starting_equity END
        AS daily_starting_equity,
    t.d AS current_trading_date,
    s.updated_at
FROM system_state s
CROSS JOIN today t;

-- Grants: exactly contracts/role-grants.md. Column-level UPDATE keeps each
-- writer to its own columns; nobody may INSERT or DELETE.
GRANT SELECT ON system_state
    TO ta_risk_gate, ta_dashboard_control, ta_orchestrator, ta_assistant, ta_dashboard;
GRANT UPDATE (halt_triggered_on, baseline_trading_day, daily_starting_equity, updated_at)
    ON system_state TO ta_risk_gate;
GRANT UPDATE (trading_paused, updated_at) ON system_state TO ta_dashboard_control;

GRANT SELECT ON system_state_effective
    TO ta_risk_gate, ta_orchestrator, ta_assistant, ta_dashboard, ta_dashboard_control;
