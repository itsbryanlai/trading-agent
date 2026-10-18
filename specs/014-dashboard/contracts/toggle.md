# Contract: the pause/resume toggle

The dashboard's one write (FR-018 to FR-025, ADR 0026 §4, ADR 0021 §9). Research: D13–D15.

## Decision table

`toggle.decide(stored, expected, target, run_while_paused, market_open)` is pure and returns one outcome. `run_while_paused` is `True`, `False` or `None` (unreadable); `market_open` is `True`, `False` or `None` (couldn't tell).

| Stored | Request | `run_while_paused` | Outcome | Written |
|---|---|---|---|---|
| running | pause (`expected=running`) | any | `paused` | yes |
| paused | resume (`expected=paused`) | `False` | `resumed` | yes |
| paused | resume | `True` | `refused: run_while_paused` | no |
| paused | resume | `None` | `refused: unreadable` | no |
| any | `expected` ≠ stored | any | `stale` | no |
| any | `target` ≠ not `expected` | any | `refused: malformed` (`403`) | no |

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
- Resume re-reads the deployed setting at `POST` time, not only when the confirmation page was drawn.
- The halt columns are never read or written by the toggle.
- Each `POST` writes one log line (contracts/dashboard-interface.md, Logs).
