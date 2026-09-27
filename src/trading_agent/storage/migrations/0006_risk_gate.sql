-- Feature 002 (Risk Gate): stop-loss triggers, daily universe reference data and
-- its writer role, and verdicts that can come from a trigger as well as a
-- decision. Decisions: specs/002-risk-gate/research.md G12-G14, ADR 0010.

-- The daily universe reference-data job's role (ADR 0010). Cluster-wide, so
-- created only if absent, like the roles in 0001.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ta_reference_data') THEN
        CREATE ROLE ta_reference_data NOLOGIN;
    END IF;
END
$$;
GRANT USAGE ON SCHEMA public TO ta_reference_data;

-- One observation by Execution's 30-minute monitor that a held position is at or
-- below its stop-loss line. Deliberately no entry price and no line: the gate
-- re-derives both, trusting only the price it cannot fetch itself (G13).
CREATE TABLE stop_loss_triggers (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          text NOT NULL,
    observed_price  numeric(14,4) NOT NULL CHECK (observed_price > 0),
    observed_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX stop_loss_triggers_observed_at ON stop_loss_triggers (observed_at DESC);

-- Per-symbol universe data for one trading day, normalized by the reference job.
-- A symbol without today's row fails the gate's universe check (G14).
CREATE TABLE instrument_reference (
    symbol                       text NOT NULL,
    trading_day                  date NOT NULL,
    security_type                text NOT NULL
                                 CHECK (security_type IN ('common_stock', 'etf', 'adr', 'other')),
    exchange_mic                 text NOT NULL,
    market_cap_usd               numeric(20,2) NOT NULL CHECK (market_cap_usd >= 0),
    avg_daily_dollar_volume_usd  numeric(20,2) NOT NULL CHECK (avg_daily_dollar_volume_usd >= 0),
    share_price_usd              numeric(14,4) NOT NULL CHECK (share_price_usd > 0),
    fetched_at                   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, trading_day)
);

-- A verdict now comes from exactly one of a decision or a stop-loss trigger, so
-- stop-loss exits pass through the gate like everything else (Constitution I).
-- orders' composite FK to (id, verdict) is untouched.
ALTER TABLE risk_verdicts ALTER COLUMN decision_id DROP NOT NULL;
ALTER TABLE risk_verdicts
    ADD COLUMN stop_loss_trigger_id uuid UNIQUE REFERENCES stop_loss_triggers (id);
ALTER TABLE risk_verdicts
    ADD CONSTRAINT risk_verdicts_exactly_one_source
    CHECK (num_nonnulls(decision_id, stop_loss_trigger_id) = 1);

-- The only trading day an approval is valid (FR-019), and the config it was
-- judged against (FR-015). Backfilled before NOT NULL so the migration holds
-- on a database that already has verdicts.
ALTER TABLE risk_verdicts ADD COLUMN trading_day date;
UPDATE risk_verdicts
    SET trading_day = (evaluated_at AT TIME ZONE 'America/New_York')::date;
ALTER TABLE risk_verdicts ALTER COLUMN trading_day SET NOT NULL;

ALTER TABLE risk_verdicts ADD COLUMN config_version text;
UPDATE risk_verdicts SET config_version = 'pre-002';
ALTER TABLE risk_verdicts ALTER COLUMN config_version SET NOT NULL;

-- Drives the daily order-cap count.
CREATE INDEX risk_verdicts_trading_day_verdict ON risk_verdicts (trading_day, verdict);

-- Grants: exactly specs/001-data-model/contracts/role-grants.md (amended by 002).
GRANT SELECT, INSERT ON stop_loss_triggers TO ta_execution;
GRANT SELECT ON stop_loss_triggers TO ta_risk_gate, ta_journal, ta_assistant, ta_dashboard;

GRANT SELECT, INSERT, UPDATE ON instrument_reference TO ta_reference_data;
GRANT SELECT ON instrument_reference TO ta_risk_gate, ta_assistant, ta_dashboard;
