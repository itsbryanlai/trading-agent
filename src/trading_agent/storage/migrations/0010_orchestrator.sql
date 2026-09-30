-- Feature 005 (orchestrator): its run records, a one-value view of the latest
-- report time, and its read of system_state narrowed to the pause flag.
-- Decisions: specs/005-orchestrator/research.md O5-O7, ADR 0011, ADR 0015.

-- One row per agent run or skipped slot. Inserted as 'running' BEFORE the agent's
-- process starts, so the row claims the slot (research O5); updated once to its
-- outcome. Never deleted.
CREATE TABLE orchestrator_runs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent        text NOT NULL
                 CHECK (agent IN ('research', 'opportunistic_identifier', 'portfolio_manager')),
    trading_day  date NOT NULL,
    reason       text NOT NULL
                 CHECK (reason IN ('scheduled', 'morning_session', 'event_driven', 'catch_up')),
    -- research_daily, research@HH:MM, morning_session or oi@HH:MM; null only for
    -- event-driven runs, which aren't tied to a slot.
    slot_key     text,
    slot_at      timestamptz,
    started_at   timestamptz,
    finished_at  timestamptz,
    -- The agent's process group, so a later startup can stop an agent orphaned
    -- by a crash (research O12).
    pgid         integer,
    outcome      text NOT NULL
                 CHECK (outcome IN ('running', 'succeeded', 'failed', 'timed_out',
                                    'interrupted', 'skipped')),
    -- Exit status, launch error type or skip reason. Never an environment value.
    detail       text,

    CONSTRAINT orchestrator_runs_slot_key_iff_not_event_driven
        CHECK ((slot_key IS NULL) = (reason = 'event_driven')),
    CONSTRAINT orchestrator_runs_skipped_iff_never_started
        CHECK ((outcome = 'skipped') = (started_at IS NULL)),
    CONSTRAINT orchestrator_runs_running_iff_unfinished
        CHECK ((outcome = 'running') = (started_at IS NOT NULL AND finished_at IS NULL))
);

-- One claim per slot per day, whatever reason the row gives: an on-time and a
-- catch-up morning session can't both exist (/speckit-analyze D1). Nulls are
-- distinct, so event-driven rows aren't constrained.
CREATE UNIQUE INDEX orchestrator_runs_one_per_slot
    ON orchestrator_runs (agent, trading_day, slot_key);
CREATE INDEX orchestrator_runs_agent_started ON orchestrator_runs (agent, started_at DESC);

-- The only thing the orchestrator may learn about reports (ADR 0011). Owner's
-- rights, like 0009's view: reports' row-level security is enabled, not forced,
-- so its policies stay as 0002 made them.
CREATE VIEW latest_report_time AS
    SELECT max(generated_at) AS generated_at FROM reports;

-- 0005 let the orchestrator read the whole system_state row, including the
-- starting equity and the halt: account data it must never read (FR-002,
-- ADR 0003). A table-level REVOKE also clears any column grants.
REVOKE SELECT ON system_state, system_state_effective FROM ta_orchestrator;
GRANT SELECT (trading_paused) ON system_state TO ta_orchestrator;

-- Grants: exactly specs/001-data-model/contracts/role-grants.md (amended by 005).
GRANT SELECT, INSERT ON orchestrator_runs TO ta_orchestrator;
GRANT UPDATE (pgid, finished_at, outcome, detail) ON orchestrator_runs TO ta_orchestrator;
GRANT SELECT ON latest_report_time TO ta_orchestrator;
-- The Assistant and dashboard read everything (Constitution VII).
GRANT SELECT ON orchestrator_runs, latest_report_time TO ta_assistant, ta_dashboard;
