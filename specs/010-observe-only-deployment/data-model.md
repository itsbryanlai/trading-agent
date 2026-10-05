# Data Model: Observe-Only Deployment

**No schema change.** No migration, table, view or grant is added or changed. Every permission comes from the existing group roles in [specs/001-data-model/contracts/role-grants.md](../001-data-model/contracts/role-grants.md).

## What this feature adds to the database

Login roles only, created by the login command ([contracts/logins-command.md](contracts/logins-command.md)). Each is a member of exactly one existing group role. They are cluster objects created at deploy time, not migration content, and their passwords never exist in the repository.

It also adds one row change made by the owner during setup: `system_state.trading_paused = true`, set as `ta_owner_control_login` before any service starts.

## Read access for the owner (FR-004b)

`ta_owner_read_login`, in `ta_dashboard`, reads everything observation and the post-deploy check need:
- `reports`, through its `reports_select` policy;
- `reports_with_status`, `decisions`, `decision_reports` and `risk_verdicts`;
- `account_snapshots`, `positions`, `orders` and `execution_refusals`;
- `instrument_reference`, `orchestrator_runs`, `system_state`, `system_state_effective` and `in_flight_orders`.

It has no write grant.

## Pause control for the owner

`ta_owner_control_login`, in `ta_dashboard_control`, can read `system_state.trading_paused` and update only `trading_paused` and `updated_at`. It is the one switch for observe-only, switch-on and the paused form of switch-off.

## State over the feature's life

| Phase | `trading_paused` | `run_while_paused` | Paper account | Buy verdicts | `orders` |
|---|---|---|---|---|---|
| Setup | set `true` before any service | `true` | owner flattens it | — | empty |
| Observing | `true` | `true` | flat (confirmed by the pre-open check), nothing to sell | rejected by the gate, `trading_paused` | empty |
| Switch-on | cleared by the owner after the close | set `false` first, by a reviewed release while still paused | flat | none pending | empty |
| Trading | `false` | `false` | Execution's | submitted the same session, or lapsed | Execution's |
| Paused switch-off | `true` | `false` (the PM stops) | positions keep their stop-loss exits | rejected by the gate | sells and exits only |
