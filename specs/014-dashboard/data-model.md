# Data Model: The Dashboard

The dashboard reads the existing schema (`specs/001-data-model/data-model.md`) and writes one row's two columns. This feature adds one column and one trigger (ADR 0027), and two logins. No new table, view or grant.

## Schema change: migration `0015`

| Object | Change |
|---|---|
| `system_state.trading_paused_changed_at` | new, `timestamptz`, nullable, no default, no backfill |
| `system_state_trading_paused_changed()` | new `plpgsql` trigger function: `IF NEW.trading_paused IS DISTINCT FROM OLD.trading_paused THEN NEW.trading_paused_changed_at := now(); END IF; RETURN NEW;` |
| trigger `system_state_trading_paused_changed` | `BEFORE UPDATE ON system_state FOR EACH ROW` |

Grants: unchanged. Roles with table-wide `SELECT` on `system_state` (`ta_risk_gate`, `ta_assistant`, `ta_dashboard`, `ta_dashboard_control`) read the column; `ta_orchestrator` and `ta_execution` keep `S(trading_paused)` only. No role may `UPDATE` it directly; an `UPDATE … SET trading_paused_changed_at = …` by any role is `42501`.

**Behaviour** (integration tests, fixed inputs):

| Update | `trading_paused_changed_at` |
|---|---|
| `trading_paused` false → true, by `ta_dashboard_control` | set to the transaction's `now()` |
| true → true (no change) | unchanged |
| baseline or halt columns, by `ta_risk_gate` | unchanged, and the update succeeds |
| manual `UPDATE` as the owner's control login | set, as by the dashboard |

## Logins (no grant change)

| Login | Member of | Used by |
|---|---|---|
| `ta_dashboard_login` | `ta_dashboard` | every page, the health check, `--check` |
| `ta_dashboard_control_login` | `ta_dashboard_control` | the toggle's one write, `--check` |

`storage/logins.py` grows from 11 to 13 rows. The owner's `ta_owner_read_login` and `ta_owner_control_login` are unchanged.

## What each view reads

All through `ta_dashboard_login`, in one read-only transaction per request (research D12). Exact SQL: [contracts/queries.md](contracts/queries.md).

| View | Tables and views |
|---|---|
| Overview, banner | `system_state` (`trading_paused`, `trading_paused_changed_at`), `system_state_effective` (`daily_loss_halt_active`, `current_trading_date`), `positions`, latest `account_snapshots` |
| Equity curve | `account_snapshots` (`taken_at`, `equity`) |
| Order history | `risk_verdicts` ← `decisions` or `stop_loss_triggers`; → `orders` or `execution_refusals` |
| Agent activity | `reports`, `decisions`, `decision_reports`, `orchestrator_runs` |
| Journal | `journal` |

## In-memory state (never stored)

| Entity | Fields | Lifetime |
|---|---|---|
| Session | id (random, 32 bytes), created_at, expires_at (created + 12 h), csrf (random, 32 bytes) | until sign-out, expiry or restart; at most 20, oldest dropped |
| Sign-in gate, site-wide | failures, not_before | until a success or restart |
| Sign-in gate, per address | address → failures, not_before | until that address succeeds, it is evicted (1,000 tracked) or restart |

## Toggle state machine

States are the stored `trading_paused` value. The dashboard shows only the action leading out of the current state.

```text
running ──[Pause → confirm]──────────────────────────────▶ paused
paused  ──[Resume → confirm]──▶ (run_while_paused is false, and the PM's newest run
                                  was skipped for the pause? ADR 0028) ──yes──▶ running
                                       │ no / unreadable
                                       ▼
                               refused, nothing written
```

A confirmation whose `expected` no longer matches the stored value writes nothing (contracts/toggle.md). The daily-loss halt is independent: no toggle reads or writes it.

## Derived values

| Value | From |
|---|---|
| "Paused since" | `trading_paused_changed_at`; null shows "start time not recorded". Never `updated_at` |
| Halt active | `system_state_effective.daily_loss_halt_active` |
| Orchestrator honours the setting | newest `orchestrator_runs` row for `portfolio_manager` is `skipped` with detail `trading paused` (ADR 0028) |
| Position cost | `qty × avg_entry_price` |
| Snapshot age | read time − `taken_at` |
| Equity point per day (1m, all) | last `account_snapshots` row per NYSE trading day |
| Account index (journal chart) | product of `(1 + account.return)` over the range's rows, × 100 at the first |
| Analyst index (journal chart) | `agents.<name>.index`, rebased so its first in-range point is 100 |
| Usage totals | per agent, sum of each in-range row's `usage` fields |
