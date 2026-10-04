-- Feature 008 (specs/008-portfolio-manager, data-model.md "Migration 0012";
-- ADR 0016 section 4; ADR 0019).
--
-- A decision records the trade time of the quote it was made on (Finnhub's `t`),
-- beside `quote_at_decision`. The Risk Gate reads it for `decision_stale`
-- (ADR 0019); the dashboard and the Assistant read it with the rest of the row.
--
-- NOT NULL with no default, on purpose: a default would invent a quote time.
-- No grant changes: the PM's table-level INSERT on `decisions` and every reader's
-- SELECT already cover the new column.

ALTER TABLE decisions ADD COLUMN quote_time timestamptz NOT NULL;
