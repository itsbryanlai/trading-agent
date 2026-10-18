# 0027. The time the pause last changed is recorded by the database

Status: accepted

Accepted by the owner on 2026-10-18, during `/speckit-clarify` for `specs/014-dashboard`. Amends [0026](0026-dashboard-public-service-with-owner-sign-in.md)'s consequence "No migration and no grant change": this adds a migration, and no grant. The rest of 0026 stands. The owner chose this on 2026-10-18 (clarify Q1).

## Context

0026 has the dashboard say how long trading has been paused: on every page's observe-only banner (§9) and on the toggle's confirmation page (§4). `system_state` has no column for that. Its `updated_at` is not one: the Risk Gate sets it every trading day when it records the daily-loss baseline, and again when the halt fires (`risk/service.py`). Read as "paused since", it would show this morning's open after a two-week pause.

The pause changes by two paths: the dashboard's toggle, and the runbook's manual `UPDATE` as `ta_owner_control_login` (deployment runbook, section 5).

## Decision

1. **A new column, `system_state.trading_paused_changed_at`** (`timestamptz`, nullable), added by migration `0015`.
2. **A database trigger keeps it.** Before any update of `system_state`, if `trading_paused` changes value, the trigger sets the new column to the transaction's time. An update that leaves `trading_paused` as it was leaves the column alone. The trigger works whichever role or path made the change, so no role is granted a write on the column, and the dashboard's control login keeps exactly `U(trading_paused, updated_at)`.
3. **Empty until the first change.** The migration leaves the column null; nothing is backfilled from `updated_at`. The dashboard shows a null as "start time not recorded".
4. **Readers.** Roles with table-wide `SELECT` on `system_state` (the Risk Gate, the Assistant, the dashboard, the dashboard's control role) can read it. The orchestrator and Execution keep their read of `trading_paused` alone. Nothing in the trade path reads it.

## Alternatives considered

- **No "since when".** Not chosen by the owner: how long the system has been observing is the first thing the banner should say.
- **Show `updated_at`, labelled as the state's last change.** Rejected: accurate, but nearly always the Risk Gate's daily baseline, so it reads as a pause time when it isn't.
- **A column the dashboard writes itself.** Rejected: the runbook's manual `UPDATE` would not set it, and it needs a new column grant.
- **Infer it from the dashboard's log line.** Rejected: logs are not durable state, and the manual path writes none.

## Consequences

- **One migration**, run by the owner with the release that ships the dashboard (runbook, section 3, step 1), before that code is merged to `release/prod`.
- **The grants contract** (`specs/001-data-model/contracts/role-grants.md`) gains a note for the column, readers as above; its matrix cells don't change.
- **`docs/specs/data-model.md`** lists the column under `system_state`.
- **A trigger is new in this schema's `system_state`.** It is tested with fixed inputs like the rest of the data model: a change sets the column, a no-op update and a baseline or halt update leave it.
- **No trade-path behavior changes.** The Risk Gate, Execution and the orchestrator ignore the column.
