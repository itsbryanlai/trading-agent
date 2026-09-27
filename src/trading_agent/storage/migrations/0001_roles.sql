-- One NOLOGIN group role per component. Each component connects through its own
-- LOGIN role, created out-of-band by an operator as a member of its group role,
-- so no password ever lives in this repository
-- (specs/001-data-model/research.md R3).
--
-- Roles are cluster-wide, so creation is conditional: a database created on a
-- server that already has these roles must still migrate cleanly.

DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'ta_research',
        'ta_opportunistic_identifier',
        'ta_portfolio_manager',
        'ta_risk_gate',
        'ta_execution',
        'ta_journal',
        'ta_orchestrator',
        'ta_assistant',
        'ta_dashboard',
        'ta_dashboard_control'
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', role_name);
        END IF;
        EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', role_name);
    END LOOP;
END
$$;

-- No component may create objects: a role that can create a table can create
-- one it owns, outside every grant in the contract.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

REVOKE ALL ON schema_migrations FROM PUBLIC;
