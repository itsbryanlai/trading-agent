# Contract: the query menu

The only reads the Assistant makes (FR-005, FR-005a; research A9, A10, A16).
- **Choosing**: the plan call chooses entries by name.
- **Validating**: code validates the parameters (A10).
- **Running**: code runs each entry as a constant SQL string, inside one `REPEATABLE READ READ ONLY` transaction per question, as `ta_assistant_login`.

Adding, removing or widening an entry is a change to this contract and is reviewed.

## Parameters

Every choice carries all six keys. A key the entry doesn't take must be `null`; otherwise the choice is dropped as `unexpected_parameter`.

| Key | Type | Validation |
|---|---|---|
| `name` | string | on this menu |
| `symbol` | string or null | ticker pattern, and in the known-symbol set (A10) |
| `agent` | string or null | in the entry's own agent list |
| `from_date`, `to_date` | ISO date or null | owner-local dates. `from_date` ≤ `to_date`, span ≤ `max_span_days`. A missing date takes the entry's default |
| `section` | string or null | `settings` only: `risk`, `schedule` or `models` |

"Today" in a default means the owner's local date (clarify Q3). Dates become UTC instants over the owner's local days (A10).

## Entries

Every row-returning entry is capped at `max_rows_per_query`, newest first, with `cut: true` when more rows exist. The entry list is fixed in `menu.py`. Fields marked † are untrusted text (A11), cut to `text_field_chars`.

| Name | Takes | Default dates | Returns |
|---|---|---|---|
| `portfolio_now` | — | — | each position (symbol, qty, average entry price, `updated_at`); the latest account snapshot (equity, cash, buying power, `taken_at`); `system_state_effective` (paused, halt day, baseline day, starting equity, `updated_at`) |
| `account_history` | dates | last 7 days | account snapshots in the span (equity, cash, `taken_at`) |
| `decisions` | symbol?, dates | today | decisions (time, symbol, direction, size, quote at decision, reasoning†), each with its verdict (approved or rejected, rejection rule, evaluated at), its order's status and fill if any, and its cited reports (id, agent, direction) |
| `reports` | agent? (`research`, `opportunistic_identifier`), symbol?, dates | today | reports (time, agent, symbol, direction, conviction, suggested size, expires at, status from `reports_with_status`, rationale†, sources: title†, publisher†, url, relevance, published at) |
| `orders` | symbol?, dates | today | orders (submitted at, symbol, side and quantity from the verdict's approved order, status, fill price and quantity, broker reason†, updated at); execution refusals (refused at, reason, details†); `in_flight_orders` |
| `stop_losses` | symbol?, dates | last 7 days | stop-loss triggers (symbol, observed price, observed at), each with its verdict and order status |
| `journal` | dates | last 10 days | journal rows (trading day, equity open and close, summary†, `per_agent_attribution` as stored, written at) |
| `agent_runs` | agent? (`research`, `opportunistic_identifier`, `portfolio_manager`), dates | today | orchestrator runs (agent, trading day, reason, slot, started, finished, outcome, detail†) |
| `universe` | symbol | latest | the latest `instrument_reference` row for the symbol (trading day, security type, exchange, market cap, average daily dollar volume, share price, fetched at) |
| `settings` | section? | — | the allowlisted settings below, as deployed |

The known-symbol query (A10) isn't on the menu. Code runs it at the start of every question.

## `settings` allowlist (ADR 0024 §3)

These are read from the files in the deployed checkout. Nothing outside this list is read into a result. In particular, `env`, `module` and every other key are never read.

| Section | File | Keys |
|---|---|---|
| `risk` | `config/risk.yaml` | `max_position_pct`, `cash_reserve_pct`, `stop_loss_pct`, `max_orders_per_day`, `daily_loss_halt_pct`, `max_buy_price_tolerance_pct`, and every key under `universe` |
| `schedule` | `config/schedule.yaml` | per agent (`research`, `opportunistic_identifier`, `portfolio_manager`): `enabled`, `timeout_minutes`, `daily_at`, `interval_minutes`, `window_start`, `window_end`, `morning_session`, `min_spacing_minutes`, `report_wait_minutes`, `last_start`, `before_close_minutes`, `run_while_paused`, where present |
| `models` | `config/research.yaml`, `config/opportunistic_identifier.yaml`, `config/portfolio_manager.yaml`, `config/assistant.yaml` | `model.provider`, `model.name` |

A missing file or key shows as `"unavailable"`. Times in `schedule` are ET wall-clock, as the file says. The answer converts them to the owner's zone with ET alongside.
