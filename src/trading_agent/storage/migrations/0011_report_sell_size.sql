-- Feature 007 (specs/007-research-agent, spec Clarifications; research R13).
--
-- A report's suggested size is a target weight, so a sell may suggest 0: a full
-- exit, the same way the PM writes one. A buy or hold must still be above 0.
--
-- Also closes a gap in 0002's check, approved by the owner: `NULL > 0` is NULL,
-- and a CHECK passes on NULL, so an actionable report could have no size at
-- all. `IS NOT NULL` is load-bearing here, as in reports_conviction_range.
--
-- Applies to both analysts' rows. No grant changes.

ALTER TABLE reports DROP CONSTRAINT reports_suggested_size_range;

ALTER TABLE reports ADD CONSTRAINT reports_suggested_size_range
    CHECK (CASE WHEN direction = 'no_action' THEN suggested_size_pct IS NULL
                WHEN direction = 'sell' THEN suggested_size_pct IS NOT NULL
                     AND suggested_size_pct >= 0 AND suggested_size_pct <= 100
                ELSE suggested_size_pct IS NOT NULL
                     AND suggested_size_pct > 0 AND suggested_size_pct <= 100 END);
