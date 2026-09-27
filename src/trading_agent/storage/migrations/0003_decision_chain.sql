-- US2: the trading path's audit trail.
--   reports --< decision_reports >-- decisions --1:1-- risk_verdicts --1:0..1-- orders
-- The links are the feature (SC-001): every order must trace back to the
-- decision and report(s) behind it. Foreign keys default to NO ACTION, so a
-- referenced row can never be deleted out from under its dependents.

CREATE TABLE decisions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    generated_at        timestamptz NOT NULL DEFAULT now(),
    symbol              text NOT NULL,
    direction           text NOT NULL CHECK (direction IN ('buy', 'sell', 'hold')),
    size_pct            numeric(6,3) NOT NULL CHECK (size_pct >= 0 AND size_pct <= 100),
    reasoning_md        text NOT NULL,
    -- The quote the Portfolio Manager fetched itself, never one copied from a report.
    quote_at_decision   numeric(14,4) NOT NULL CHECK (quote_at_decision > 0)
);

CREATE INDEX decisions_generated_at ON decisions (generated_at DESC);
CREATE INDEX decisions_symbol_generated_at ON decisions (symbol, generated_at DESC);

-- Which report(s) a decision drew on. A table rather than a uuid[] column
-- because Postgres cannot foreign-key array elements (research.md R7).
CREATE TABLE decision_reports (
    decision_id  uuid NOT NULL REFERENCES decisions (id),
    report_id    uuid NOT NULL REFERENCES reports (id),
    PRIMARY KEY (decision_id, report_id)
);

CREATE INDEX decision_reports_report_id ON decision_reports (report_id);

CREATE TABLE risk_verdicts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- UNIQUE: exactly one verdict per decision, even if two evaluations race.
    decision_id     uuid NOT NULL UNIQUE REFERENCES decisions (id),
    evaluated_at    timestamptz NOT NULL DEFAULT now(),
    verdict         text NOT NULL CHECK (verdict IN ('approved', 'rejected')),
    rejection_rule  text,
    approved_order  jsonb,

    CONSTRAINT risk_verdicts_rule_iff_rejected
        CHECK ((verdict = 'rejected') = (rejection_rule IS NOT NULL)),
    CONSTRAINT risk_verdicts_order_iff_approved
        CHECK ((verdict = 'approved') = (approved_order IS NOT NULL)),
    CONSTRAINT risk_verdicts_order_is_object
        CHECK (approved_order IS NULL OR jsonb_typeof(approved_order) = 'object'),
    -- Target of orders' composite foreign key (research.md R12).
    UNIQUE (id, verdict)
);

CREATE TABLE orders (
    -- `{trading_day}-{symbol}-{side}`, also sent to the broker as client_order_id,
    -- so a crash-restart resubmission is rejected twice over (research.md R11).
    id               text PRIMARY KEY,
    risk_verdict_id  uuid NOT NULL UNIQUE,
    -- Always 'approved'. Together with the composite key below, this makes an
    -- order for a rejected verdict impossible rather than merely discouraged.
    verdict          text NOT NULL DEFAULT 'approved' CHECK (verdict = 'approved'),
    submitted_at     timestamptz NOT NULL DEFAULT now(),
    broker_order_id  text UNIQUE,
    status           text NOT NULL
                     CHECK (status IN ('submitted', 'filled', 'partially_filled',
                                       'rejected', 'canceled')),
    fill_price       numeric(14,4),
    fill_qty         numeric(14,4) CHECK (fill_qty >= 0),
    updated_at       timestamptz NOT NULL DEFAULT now(),

    FOREIGN KEY (risk_verdict_id, verdict) REFERENCES risk_verdicts (id, verdict)
);

-- Report status, computed at read time; never stored (spec Clarifications).
-- security_invoker: the view obeys the caller's own table grants, so granting
-- it never widens what a role can see (research.md R6).
CREATE VIEW reports_with_status WITH (security_invoker = true) AS
SELECT
    r.*,
    CASE
        WHEN r.expires_at <= now() THEN 'expired'
        WHEN EXISTS (SELECT 1 FROM decision_reports dr WHERE dr.report_id = r.id)
            THEN 'consumed'
        ELSE 'open'
    END AS status
FROM reports r;

-- Grants: exactly contracts/role-grants.md.
GRANT SELECT, INSERT ON decisions, decision_reports TO ta_portfolio_manager;
GRANT SELECT ON decisions TO ta_risk_gate, ta_journal, ta_assistant, ta_dashboard;
GRANT SELECT ON decision_reports TO ta_journal, ta_assistant, ta_dashboard;

GRANT SELECT, INSERT ON risk_verdicts TO ta_risk_gate;
GRANT SELECT ON risk_verdicts TO ta_execution, ta_journal, ta_assistant, ta_dashboard;

GRANT SELECT, INSERT, UPDATE ON orders TO ta_execution;
GRANT SELECT ON orders TO ta_journal, ta_assistant, ta_dashboard;

GRANT SELECT ON reports_with_status
    TO ta_portfolio_manager, ta_journal, ta_assistant, ta_dashboard;
