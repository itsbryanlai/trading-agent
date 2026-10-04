# Contract: Database Roles and Grants

The interface this feature exposes to every other component: which database role each component
connects as, and exactly what that role may do. Later features rely on this contract; changing a
cell is a change to this contract and requires updating the component's `docs/specs/*.md` first
(Constitution Principle III, V).

The integration test suite is generated from the matrix below (research.md R14): every cell is
asserted — permitted operations must succeed, **every unlisted operation must be rejected**. Adding
a grant in a migration without adding it here fails the suite, and vice versa.

## Group roles

All are `NOLOGIN`, created by migration. Each component connects through its own `LOGIN` role that
an operator creates out-of-band as a member of the matching group role (research.md R3).

| Group role | Component | Source of its read/write set |
|---|---|---|
| `ta_research` | Research agent | `docs/specs/research-agent.md` |
| `ta_opportunistic_identifier` | Opportunistic Identifier agent | `docs/specs/opportunistic-identifier-agent.md` |
| `ta_portfolio_manager` | Portfolio Manager agent | `docs/specs/portfolio-manager-agent.md` |
| `ta_risk_gate` | Risk Gate | `docs/specs/risk-gate.md` |
| `ta_execution` | Execution | `docs/specs/execution.md` |
| `ta_journal` | Daily journal writer | `docs/specs/data-model.md` (`journal`) |
| `ta_orchestrator` | Orchestrator | `docs/specs/orchestrator.md` |
| `ta_assistant` | Assistant agent | `docs/specs/assistant-agent.md` |
| `ta_dashboard` | Dashboard, general request path | `docs/specs/ui-dashboard.md` |
| `ta_dashboard_control` | Dashboard, pause/resume toggle only | `docs/specs/ui-dashboard.md` |
| `ta_reference_data` | Daily universe reference-data job | ADR 0010; added by `specs/002-risk-gate` |

The dashboard uses two roles, following `trading-bot`'s `web_reader` / control split: the ordinary
request path holds a connection that cannot write at all; only the toggle endpoint holds the one
that can.

## Matrix

`S` = SELECT, `I` = INSERT, `U` = UPDATE, `D` = DELETE, `—` = no access.
`I*` = INSERT restricted by row-level security to the role's own `agent` value.
`U(cols)` = UPDATE on the named columns only. `S(cols)` = SELECT on the named columns only.

| Object | research | opp_identifier | portfolio_mgr | risk_gate | execution | journal | orchestrator | assistant | dashboard | dashboard_control | reference_data |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `reports` | S, I* | S, I* | S | — | — | S | — | S | S | — | — |
| `reports_with_status` (view) | — | — | S | — | — | S | — | S | S | — | — |
| `decisions` | — | — | S, I | S | — | S | — | S | S | — | — |
| `decision_reports` | — | — | S, I | — | — | S | — | S | S | — | — |
| `risk_verdicts` | — | — | — | S, I | S | S | — | S | S | — | — |
| `orders` ² | — | — | — | — | S, I, U(broker_order_id, status, fill_qty, fill_price, broker_reason, updated_at) | S | — | S | S | — | — |
| `positions` | — | — | S | S | S, I, U, D | S | — | S | S | — | — |
| `account_snapshots` | — | — | S | S | S, I | S | — | S | S | — | — |
| `journal` | — | — | S | **—** | **—** | S, I, U | — | S | S | — | — |
| `system_state` ⁴ | — | — | — | S, U(halt_triggered_on, baseline_trading_day, daily_starting_equity, updated_at) | S(trading_paused) ² | — | S(trading_paused) | S | S | S, U(trading_paused, updated_at) | — |
| `system_state_effective` (view) ⁴ | — | — | — | S | — | — | — | S | S | S | — |
| `stop_loss_triggers` ¹ | — | — | — | S | S, I | S | — | S | S | — | — |
| `instrument_reference` ¹ ³ | — | — | — | S | — | — | — | S | S | — | S, I |
| `execution_refusals` ² | — | — | — | — | S, I | S | — | S | S | — | — |
| `reference_candidate_symbols` (view) ³ | — | — | — | — | — | — | — | S | S | — | S |
| `in_flight_orders` (view) ⁵ | — | — | — | S | — | — | — | S | S | — | — |
| `orchestrator_runs` ⁴ | — | — | — | — | — | — | S, I, U(pgid, finished_at, outcome, detail) | S | S | — | — |
| `latest_report_time` (view) ⁴ | — | — | — | — | — | — | S | S | S | — | — |
| `schema_migrations` | — | — | — | — | — | — | — | — | — | — | — |

¹ Added by `specs/002-risk-gate` (migration `0006`). Execution writes stop-loss triggers and the
Risk Gate evaluates them. The reference-data job writes universe data and the Risk Gate reads it.
The journal can read triggers so it can trace a stop-loss exit's order back to its cause.

² Amended by `specs/003-execution` (migration `0007`). Execution's `UPDATE` on `orders` is narrowed
to the columns that change after submission; it may read the manual pause flag and no other
`system_state` column (FR-018); `execution_refusals` records approvals it declined to submit, readable
wherever orders are.

³ Amended by `specs/004-reference-data` (migration `0009`). The reference-data job loses `UPDATE` on
`instrument_reference`: a day's row is never changed once written, and it inserts with
`ON CONFLICT DO NOTHING`. Its only read outside that table is `reference_candidate_symbols`, a view
of held and recently named symbols (symbols and times only). The view runs with its owner's rights,
so the job has no access to `positions`, `reports` or `decisions`, and the `reports` row-level
security policies are unchanged (research D10).

⁴ Amended by `specs/005-orchestrator` (migration `0010`). The orchestrator's read of `system_state` is
narrowed to the `trading_paused` column, and its read of `system_state_effective` removed: since `0005`
it could read the starting equity and the halt, account data it must never see (ADR 0003). It writes
only its own `orchestrator_runs` (inserting a row before each agent starts, then updating its process
group and outcome), and learns about reports only through `latest_report_time`, a single value,
through a view with owner rights, so the `reports` row-level security policies are unchanged
(research O5-O7, ADR 0015).

Bold `—` marks the two denials the spec calls out by name: the Risk Gate and Execution can never
read the journal (FR-012), so attribution cannot become a trading input.

## Row-level security on `reports`

RLS is enabled on `reports`. Policies:

- `INSERT` for `ta_research` — `WITH CHECK (agent = 'research')`
- `INSERT` for `ta_opportunistic_identifier` — `WITH CHECK (agent = 'opportunistic_identifier')`
- `SELECT` for every role with `S` above — `USING (true)`

No role has an `UPDATE` or `DELETE` policy or grant on `reports` (reports are insert-only; status is
computed — spec Clarifications).

## Rejection behavior other features can rely on

| Attempt | Database response |
|---|---|
| Any operation not in the matrix | `InsufficientPrivilege` (SQLSTATE `42501`) |
| Analyst inserts a row with another agent's `agent` value | RLS violation (SQLSTATE `42501`, "new row violates row-level security policy") |
| `ta_risk_gate` updates `trading_paused`, or `ta_dashboard_control` updates a halt column | `InsufficientPrivilege` (column-level grant) |
| Order referencing a rejected or nonexistent verdict | Foreign key violation (SQLSTATE `23503`) |
| Second verdict for the same decision; second order or second refusal for the same verdict | Unique violation (SQLSTATE `23505`) |
| An order whose id isn't `{day}-{symbol}-{side}-{first 8 hex of its verdict id}` ([ADR 0012](../../../docs/adr/0012-order-identifier-per-verdict.md)); a buy without a limit price or a sell with one | Check violation (SQLSTATE `23514`) |
| An order and a refusal for the same verdict | Integrity violation (SQLSTATE `23000`), from the `execution_outcome_exclusive` trigger |

## Sequences

None. All generated keys are `gen_random_uuid()` defaults or caller-supplied (`orders.id`), so no
`USAGE ON SEQUENCE` grants are needed — unlike `trading-bot`, where every `BIGSERIAL` table
required one.

⁵ Amended by `specs/009-pending-orders` (migration `0013`, ADR 0020). `in_flight_orders` shows the
Risk Gate which of its approvals are still working: trading day, symbol, side, unsettled quantity
and (for buys) price ceiling. It runs with its owner's rights, so the gate has no access to
`orders` or `execution_refusals`. No role gained a write.
