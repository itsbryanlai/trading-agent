-- US3: current holdings, broker account state, and the daily journal.

-- Current holdings, maintained by Execution from confirmed fills. Long-only:
-- a closed position's row is deleted rather than left at zero.
CREATE TABLE positions (
    symbol           text PRIMARY KEY,
    qty              numeric(14,4) NOT NULL CHECK (qty > 0),
    avg_entry_price  numeric(14,4) NOT NULL CHECK (avg_entry_price > 0),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Broker account state. Execution is the only component holding the broker
-- credential, so it records this for everyone who needs cash or equity without
-- broker access: the Portfolio Manager, the Risk Gate, the journal
-- (research.md R8).
CREATE TABLE account_snapshots (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    taken_at      timestamptz NOT NULL DEFAULT now(),
    equity        numeric(16,2) NOT NULL CHECK (equity >= 0),
    cash          numeric(16,2) NOT NULL,
    buying_power  numeric(16,2) NOT NULL CHECK (buying_power >= 0)
);

CREATE INDEX account_snapshots_taken_at ON account_snapshots (taken_at DESC);

-- One entry per trading day. UNIQUE (trading_day) lets a failed run be re-run
-- as an upsert rather than duplicated.
CREATE TABLE journal (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trading_day            date NOT NULL UNIQUE,
    equity_open            numeric(16,2) NOT NULL,
    equity_close           numeric(16,2) NOT NULL,
    summary_md             text NOT NULL,
    per_agent_attribution  jsonb NOT NULL
                           CHECK (jsonb_typeof(per_agent_attribution) = 'object'),
    written_at             timestamptz NOT NULL DEFAULT now()
);

-- Grants: exactly contracts/role-grants.md.
GRANT SELECT, INSERT, UPDATE, DELETE ON positions TO ta_execution;
GRANT SELECT ON positions
    TO ta_portfolio_manager, ta_risk_gate, ta_journal, ta_assistant, ta_dashboard;

GRANT SELECT, INSERT ON account_snapshots TO ta_execution;
GRANT SELECT ON account_snapshots
    TO ta_portfolio_manager, ta_risk_gate, ta_journal, ta_assistant, ta_dashboard;

-- Deliberately no grant of any kind to ta_risk_gate or ta_execution: per-agent
-- attribution is measurement, never a trading input (FR-012).
GRANT SELECT, INSERT, UPDATE ON journal TO ta_journal;
GRANT SELECT ON journal TO ta_portfolio_manager, ta_assistant, ta_dashboard;
