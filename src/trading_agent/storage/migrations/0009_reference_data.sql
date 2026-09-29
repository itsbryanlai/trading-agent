-- Feature 004 (reference-data job): the job reads candidate symbols through one
-- view and can only read and insert its own table. Decisions:
-- specs/004-reference-data/research.md D10, spec Clarifications.

-- A day's row is never changed once written (FR-011, FR-022); the job inserts with
-- ON CONFLICT DO NOTHING, which needs no UPDATE.
REVOKE UPDATE ON instrument_reference FROM ta_reference_data;

-- Symbols the system touches: held, or named in a report or decision. Symbols and
-- times only: no text, reasoning, sizes or quantities.
--
-- Deliberately NOT security_invoker (unlike the views in 0003 and 0005): it reads
-- the base tables with its owner's rights, so ta_reference_data needs no grant on
-- positions, reports or decisions. reports' row-level security is enabled, not
-- forced, so the owner sees every row and the policies stay as 0002 made them.
--
-- No time filter: the job applies the exact window (FR-001) with the exchange
-- calendar. A filter on now() would drop long-lived active reports and make
-- fixed-date tests expire (/speckit-analyze F2-F3). Grouped, so it grows with
-- distinct symbols, not rows.
CREATE VIEW reference_candidate_symbols AS
    SELECT symbol, 'position'::text AS source,
           NULL::timestamptz AS named_at, NULL::timestamptz AS active_until
    FROM positions
    UNION ALL
    SELECT symbol, 'report', max(generated_at), max(expires_at)
    FROM reports
    WHERE symbol IS NOT NULL
    GROUP BY symbol
    UNION ALL
    SELECT symbol, 'decision', max(generated_at), NULL
    FROM decisions
    GROUP BY symbol;

-- Grants: exactly specs/001-data-model/contracts/role-grants.md (amended by 004).
-- The Assistant and dashboard read everything (Constitution VII).
GRANT SELECT ON reference_candidate_symbols TO ta_reference_data, ta_assistant, ta_dashboard;
