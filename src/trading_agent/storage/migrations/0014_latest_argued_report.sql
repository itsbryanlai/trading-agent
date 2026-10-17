-- Feature 011 (the Opportunistic Identifier): a `no_action` report doesn't wake the
-- Portfolio Manager. Decisions: specs/011-opportunistic-identifier spec FR-023 and
-- research.md O13; ADR 0011 ("at least one *new* report").
--
-- The orchestrator's event-driven PM trigger compares this view with its own last start.
-- A `no_action` row argues nothing for the PM to consider, so it no longer counts: six
-- quiet hourly runs cost no PM runs. This narrows a read. It adds no grant, role or flow.
--
-- Replaced in place: the same name, the same single column (generated_at) and the same
-- grants (0010: SELECT for ta_orchestrator, ta_assistant and ta_dashboard), which
-- CREATE OR REPLACE VIEW keeps. Still not security_invoker, so it runs with its owner's
-- rights as 0010 intended: the orchestrator learns only this one timestamp, never a
-- report row. Still excludes a report dated in the future (0010's adversarial review H1).
CREATE OR REPLACE VIEW latest_report_time AS
    SELECT max(generated_at) AS generated_at
    FROM reports
    WHERE generated_at <= now() AND direction <> 'no_action';

COMMENT ON VIEW latest_report_time IS 'Newest report that argues something (excludes no_action), dated up to now: the orchestrator''s event-driven PM trigger (feature 011 FR-023).';
