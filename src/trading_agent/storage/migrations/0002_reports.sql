-- US1: analyst reports. One row per Research / Opportunistic Identifier run,
-- including runs that found nothing. Insert-only: status is never stored, it is
-- computed at read time (spec Clarifications; reports_with_status in 0003).

CREATE TABLE reports (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent               text NOT NULL CHECK (agent IN ('research', 'opportunistic_identifier')),
    generated_at        timestamptz NOT NULL DEFAULT now(),
    symbol              text,
    direction           text NOT NULL CHECK (direction IN ('buy', 'sell', 'hold', 'no_action')),
    conviction          smallint,
    suggested_size_pct  numeric(6,3),
    sources             jsonb NOT NULL DEFAULT '[]',
    rationale_md        text NOT NULL,
    -- Set by the writing agent from the exchange calendar; the database only
    -- bounds it (research.md R10).
    expires_at          timestamptz NOT NULL,

    CONSTRAINT reports_symbol_iff_actionable
        CHECK ((direction = 'no_action') = (symbol IS NULL)),
    -- `IS NOT NULL` is load-bearing: a CHECK passes when it evaluates to NULL,
    -- and `NULL BETWEEN 1 AND 5` is NULL.
    CONSTRAINT reports_conviction_range
        CHECK (CASE WHEN direction = 'no_action' THEN conviction IS NULL
                    ELSE conviction IS NOT NULL AND conviction BETWEEN 1 AND 5 END),
    CONSTRAINT reports_suggested_size_range
        CHECK (CASE WHEN direction = 'no_action' THEN suggested_size_pct IS NULL
                    ELSE suggested_size_pct > 0 AND suggested_size_pct <= 100 END),
    CONSTRAINT reports_sources_is_array
        CHECK (jsonb_typeof(sources) = 'array'),
    -- A thesis with no citable source is not a valid report.
    CONSTRAINT reports_sources_required_when_actionable
        CHECK (direction = 'no_action' OR jsonb_array_length(sources) > 0),
    CONSTRAINT reports_expires_after_generated
        CHECK (expires_at > generated_at)
);

CREATE INDEX reports_agent_generated_at ON reports (agent, generated_at DESC);
CREATE INDEX reports_symbol_expires_at ON reports (symbol, expires_at);

-- Row-level security: an analyst may only insert rows attributed to itself.
-- The table owner (the migration admin) is exempt; components never connect as it.
ALTER TABLE reports ENABLE ROW LEVEL SECURITY;

CREATE POLICY reports_insert_research ON reports
    FOR INSERT TO ta_research
    WITH CHECK (agent = 'research');

CREATE POLICY reports_insert_opportunistic_identifier ON reports
    FOR INSERT TO ta_opportunistic_identifier
    WITH CHECK (agent = 'opportunistic_identifier');

CREATE POLICY reports_select ON reports
    FOR SELECT TO ta_research, ta_opportunistic_identifier, ta_portfolio_manager,
                  ta_journal, ta_assistant, ta_dashboard
    USING (true);

-- Grants: exactly contracts/role-grants.md. No UPDATE or DELETE for anyone.
GRANT SELECT, INSERT ON reports TO ta_research, ta_opportunistic_identifier;
GRANT SELECT ON reports TO ta_portfolio_manager, ta_journal, ta_assistant, ta_dashboard;
