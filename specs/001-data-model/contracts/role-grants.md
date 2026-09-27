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
| `system_state` | — | — | — | S, U(halt_triggered_on, baseline_trading_day, daily_starting_equity, updated_at) | S(trading_paused) ² | — | S | S | S | S, U(trading_paused, updated_at) | — |
| `system_state_effective` (view) | — | — | — | S | — | — | S | S | S | S | — |
| `stop_loss_triggers` ¹ | — | — | — | S | S, I | S | — | S | S | — | — |
| `instrument_reference` ¹ | — | — | — | S | — | — | — | S | S | — | S, I, U |
| `execution_refusals` ² | — | — | — | — | S, I | S | — | S | S | — | — |
| `schema_migrations` | — | — | — | — | — | — | — | — | — | — | — |

¹ Added by `specs/002-risk-gate` (migration `0006`). Execution writes stop-loss triggers and the
Risk Gate evaluates them. The reference-data job writes universe data and the Risk Gate reads it.
The journal can read triggers so it can trace a stop-loss exit's order back to its cause.

² Amended by `specs/003-execution` (migration `0007`). Execution's `UPDATE` on `orders` is narrowed
to the columns that change after submission; it may read the manual pause flag and no other
`system_state` column (FR-018); `execution_refusals` records approvals it declined to submit, readable
wherever orders are.

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
