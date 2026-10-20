# Contract: the pause/resume toggle

The dashboard's one write (FR-018 to FR-025, ADR 0026 §4, ADR 0021 §9). Research: D13–D15.

## Decision table

`toggle.decide(expected, target, run_while_paused, skipped_for_pause) -> Write(target) | Refused(reason)` is pure. `run_while_paused` is `True`, `False` or `None` (unreadable); `skipped_for_pause` is whether the Portfolio Manager's newest `orchestrator_runs` row is `skipped` with detail `trading paused` (ADR 0028, research D23). `stored` is not an input: the conditional `UPDATE` decides `stale`. `warn_market(market_open)` is separate and only adds the warning.

| Stored | Request | `run_while_paused` | Outcome | Written |
|---|---|---|---|---|
| running | pause (`expected=running`) | any | `paused` | yes |
| paused | resume (`expected=paused`) | `False`, and a PM run skipped for the pause | `resumed` | yes |
| paused | resume | `False`, but no PM run skipped for the pause | `refused: no_skipped_run` | no |
| paused | resume | `True` | `refused: run_while_paused` | no |
| paused | resume | `None` | `refused: unreadable` | no |
| any | `expected` ≠ stored | any | `stale` | no |
| any | `target` ≠ not `expected`, or either missing or unknown | any | `refused: malformed`: `403`, no redirect | no |

`stored` is what the conditional `UPDATE … WHERE trading_paused = expected` finds: zero rows is `stale`. `market_open` never changes an outcome; it only adds the warning on the confirmation page.

## Confirmation page (`GET /toggle`)

**Running → pause**

> **Pause trading?**
> Trading is running. Pausing stops new buys at the gate. Sells and stop-loss exits continue. The PM keeps running while `run_while_paused` is true.
> [Confirm pause] [Cancel]

**Paused → resume, allowed**

> **Resume trading?**
> Trading has been paused since 6 Oct 09:12 SGT (5 Oct 21:12 ET). Resuming lets the next approved buy be submitted.
> *(when the market is open, or that can't be told)* **The market is open.** An approved buy could be submitted within minutes. The deployment runbook resumes after the close.
> *(when the halt is active)* The daily-loss halt stays active until the next trading day; resuming doesn't clear it.
> [Confirm resume] [Cancel]

**Paused → resume, refused before any form is shown**

> **Resume is not available yet.**
> The deployed schedule still has `run_while_paused: true`. Switching on has two steps: first release `run_while_paused: false`, then resume after a close. See the deployment runbook, section 5 "Switching trading on".
> *(unreadable)* The deployed schedule's `run_while_paused` could not be read, so resuming is refused until it can.
> *(no skipped run)* The deployed schedule says `run_while_paused: false`, but the orchestrator hasn't yet skipped a Portfolio Manager run because of the pause, so it may still be running the old setting. Resume becomes available after the next morning session is skipped. See the deployment runbook, section 5 "Switching trading on".

The form carries `csrf`, `expected` and `target` as hidden fields. "Paused since" shows "start time not recorded" while `trading_paused_changed_at` is null.

## Outcome page (`GET /toggle/done`)

| `result` | Heading | Then |
|---|---|---|
| `paused` | Trading paused. | the current state, read fresh |
| `resumed` | Trading resumed. | the current state |
| `stale` | Nothing was changed. | "The state changed since the confirmation page was shown. It is now …" |
| `refused` | Nothing was changed. | the refusal's reason, as above |
| `failed` | Nothing was changed. | "The change could not be saved. Trading is still …" (state read fresh if the read login works) |

## Guarantees

- Only `POST /toggle`, through `control.py`, opens the control login: one connection, one `UPDATE`, commit, close.
- The `UPDATE` sets `trading_paused` and `updated_at` only; `trading_paused_changed_at` is set by the database trigger (ADR 0027).
- Resume re-reads the deployed setting and the orchestrator's evidence at `POST` time, not only when the confirmation page was drawn.
- The write runs with `lock_timeout` 5 s and `statement_timeout` 10 s; a timeout is `failed`, nothing changed.
- The halt columns are never read or written by the toggle.
- Each `POST` writes one log line (contracts/dashboard-interface.md, Logs).
