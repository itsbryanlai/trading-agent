# 0028. The dashboard's resume also needs the orchestrator to have skipped a Portfolio Manager run for the pause

Status: accepted

Accepted by the owner on 2026-10-18, after `/speckit-analyze` for `specs/014-dashboard` (finding S4). Adds a condition to [0026](0026-dashboard-public-service-with-owner-sign-in.md) §4's guard on resuming; the rest of 0026 stands. Also records the owner's confirmation (finding S5) that 0026's market-hours warning is a fallback, not a change to [0021](0021-railway-deployment-as-code-observe-only-first.md) §9.

## Context

0026 §4 refuses a resume while the dashboard's deployed `config/schedule.yaml` has `portfolio_manager.run_while_paused: true`. That reads the dashboard's own copy of the file. The orchestrator is a separate service: if its redeploy of 0021 §9's step 1 fails or lags, the dashboard can read `false` while the orchestrator still runs the Portfolio Manager while paused. Resuming then is the window 0021 §9 exists to close. The runbook's own step 1 check is a person reading the orchestrator's log.

The orchestrator already leaves a durable trace. When a pause blocks the Portfolio Manager's morning session, it writes an `orchestrator_runs` row with outcome `skipped` and detail `trading paused` (`specs/005-orchestrator`). With `run_while_paused: true` that slot runs instead. The dashboard can read the table (`ta_dashboard`).

## Decision

1. **Resume needs both:** the dashboard's deployed `run_while_paused` is `false` (0026 §4), **and** the newest `orchestrator_runs` row for `portfolio_manager` (by start time, or slot time for a skipped row) has outcome `skipped` and detail exactly `trading paused`. Either missing, and resume is refused before any write, with the reason.
2. **The check is read-only,** through the dashboard's read login, re-run at the moment of the write as well as on the confirmation page.
3. **Pausing is unchanged:** always allowed.
4. **Market hours (finding S5):** 0026's warning stays a warning. 0021 §9's "after the close and before the next open" stays the runbook's instruction; the dashboard allows a resume during the session only as a fallback, with the warning shown.

## Alternatives considered

- **The file check alone, plus a line telling the owner to check the orchestrator's log.** Not chosen: the dashboard can't tell whether it was done.
- **The orchestrator writing its effective setting to the database.** Not chosen: a new column, a new grant to the orchestrator's role and a change to its code, for a fact its runs already show.
- **Refusing a resume while the market is open.** Not chosen by the owner: the warning is enough, and an emergency resume during the session stays possible.

## Consequences

- **Switch-on can slip a day.** If step 1 is released after a close, no morning slot has been skipped yet, so resume waits until the next morning session has been skipped for the pause, then the next close. That only delays trading.
- **It depends on the orchestrator's detail text** `trading paused` (`orchestrator/planner.py` `PAUSED`). A test pins the text on both sides, so a rename fails the suite instead of silently blocking or allowing resume.
- **A disabled or stopped orchestrator blocks resume,** since no row is skipped. That fails closed.
- **No migration, grant, service or credential.** The runbook's manual `UPDATE` (section 5) still bypasses the guard, as before.
