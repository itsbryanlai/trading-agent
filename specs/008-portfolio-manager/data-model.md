# Data model: Portfolio Manager agent

The PM adds no table and no grant. It writes rows to the existing `decisions` and `decision_reports` tables ([`docs/specs/data-model.md`](../../docs/specs/data-model.md), migration 0003) as `ta_portfolio_manager`. Migration 0012 adds one column.

## Migration 0012: `decisions.quote_time`

```text
ALTER TABLE decisions ADD COLUMN quote_time timestamptz NOT NULL
```

- **What**: the trade time of the quote the PM recorded in `quote_at_decision` (Finnhub's `t`), as [ADR 0016](../../docs/adr/0016-market-data-for-the-llm-agents.md) §4 requires.
- **Who reads it**: the Risk Gate, for `decision_stale` (research P12); the dashboard and the Assistant, which already read `decisions`.
- **Grants**: unchanged. The PM's table-level `INSERT` on `decisions` covers the new column; everyone else's `SELECT` does too.
- **Existing rows**: none outside tests. `NOT NULL` with no default, on purpose: a default would invent a quote time. Every test helper that inserts a decision gains the column.

## `decisions` rows written by the PM

| Column | Value |
|---|---|
| `generated_at` | database default `now()`, at the write |
| `symbol` | a symbol given to the model in this run |
| `direction` | `buy`, `sell` or `hold` |
| `size_pct` | buy: the model's target, rounded down to 3 places, above 0; sell: the model's target, rounded down, from 0; hold: the current weight computed by code (research P8) |
| `reasoning_md` | the model's reasoning, trimmed and cut to `reasoning_max_chars`. Model-written: readers treat it as data |
| `quote_at_decision` | the PM's own Finnhub quote (`c`), above 0 |
| `quote_time` | that quote's trade time (`t`), fresh by research P4 |

## `decision_reports` rows

One per `(decision, cited report)`: every report the decision's `report_ids` named, de-duplicated. At least one per decision. Written in the same transaction as the decision.

## Reads

All in one `REPEATABLE READ, READ ONLY` transaction (research P6).

| What | From | Grant (existing) |
|---|---|---|
| Unexpired, actionable reports, each `open` or `consumed` | `reports_with_status` where `expires_at > run_start` and `direction <> 'no_action'` | `SELECT` on `reports_with_status`, `reports`, `decision_reports` |
| Positions | `positions` | `SELECT` |
| Today's latest account snapshot | `account_snapshots`, New York date = today, `taken_at ≤ run_start` | `SELECT` |
| Recent journal entries | `journal`, latest `journal_entries` by `trading_day` | `SELECT` |
| Its own decisions today on the symbols under consideration | `decisions`: `symbol`, `direction`, `size_pct`, `generated_at` | `SELECT` |

Not read: `risk_verdicts`, `orders`, `execution_refusals`, `stop_loss_triggers`, `instrument_reference`, `system_state`, `config/risk.yaml`. The integration tests assert the role still can't write anything but `decisions` and `decision_reports`.

## In-memory entities (never stored)

- **ReportView**: a report as the PM sees it: database id, run identifier (`R1`…), agent, symbol, direction, conviction, suggested size and its meaning (`full exit` or `target weight`), `already_decided_on`, primary and secondary source counts, source titles with publisher, time and relevance, and the rationale cut to length.
- **Quote**: the reference adapter's `Quote(symbol, current, previous_close, timestamp)`; **FreshQuote** when it passes research P4.
- **Candidate**: one symbol under consideration with a fresh quote: its reports, its current weight and its earlier decisions today.
- **ModelInput**: `system`, `user` (the JSON document of research P7), the identifier map `R1 → (report id, symbol, direction)`, and `input_chars`.
- **Proposal**: one item of the model's answer as received. It becomes a **CheckedDecision** (`symbol`, `direction`, `size_pct`, `reasoning`, `quote`, `quote_time`, `report_ids`) only if every check in research P8 passes.
- **Drop**: `(index, symbol or None, reason)`, the reason from research P8's closed set.
- **RunOutcome**: the checked decisions, the drops, the skipped symbols with reasons, the failure category or none, and the token counts. `__main__` maps it to an exit code (research P9).

## Risk Gate changes (feature 002's data)

- **`risk_verdicts.rejection_rule`** may now hold `decision_stale` (research P12). The column is free text; no migration. `specs/002-risk-gate/contracts/rejection-rules.md` and `risk/rules.py` gain it together, kept in lockstep by the existing test.
- **No other table changes.**
