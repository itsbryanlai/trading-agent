# Data Model: Observe-Only Deployment

**No schema change.** No migration, table, view or grant is added or changed. Every permission comes from the existing group roles in [specs/001-data-model/contracts/role-grants.md](../001-data-model/contracts/role-grants.md).

## What this feature adds to the database

Login roles only, created by the login command ([contracts/logins-command.md](contracts/logins-command.md)). Each is a member of exactly one existing group role. They are cluster objects created at deploy time, not migration content, and their passwords never exist in the repository.

## Read access for the owner (FR-004b)

The login `ta_owner_read_login`, a member of `ta_dashboard`, already reads everything observation needs (role-grants matrix):
- `reports`, through its `reports_select` policy, which names `ta_dashboard`;
- `reports_with_status`, `decisions`, `decision_reports` and `risk_verdicts`;
- `instrument_reference`, `orchestrator_runs`, `system_state` and `system_state_effective`;
- `orders` and `execution_refusals`, which stay empty while observing.

It has no write grant on any object.

## Pause control for the owner (research R9)

The login `ta_owner_control_login`, a member of `ta_dashboard_control`, can update only `system_state.trading_paused` and `updated_at`. It's used only for the post-switch-on pause.

## State over the feature's life

| Phase | Execution service | Approved verdicts | `orders` |
|---|---|---|---|
| Observing | absent | accumulate, never submitted | empty |
| Switch-on (after the close) | added | every earlier approval lapses as `approval_expired` | still empty |
| Trading | running | submitted the same session, or lapsed | filled by Execution |
