# Data model: Journal writer

No migration. The `journal` table, the `ta_journal` role and its grants exist since `specs/001-data-model` (migration `0004`). This feature adds one login and fills the table.

## Written: `journal`

| Column | Value |
|---|---|
| `trading_day` | `D`, today's session (research J3); `UNIQUE`, written by upsert (J11) |
| `equity_open` | research J7 |
| `equity_close` | research J7, "last recorded" |
| `summary_md` | contracts/summary-template.md |
| `per_agent_attribution` | contracts/attribution.md |
| `written_at` | `now()` on every write, re-runs included |

## Read (all already granted to `ta_journal`)

| Table | Columns | For |
|---|---|---|
| `journal` | the latest row with `trading_day < D`, plus any row `> D` (refusal) | the books' state, the account's base (J5, J7) |
| `reports` | `id, agent, generated_at, symbol, direction, suggested_size_pct` in the J4 window | books, usage. Never `rationale_md` or `sources`. |
| `decision_reports` | rows for those reports | usage (J9) |
| `decisions` | `id, generated_at, symbol, direction` | usage; summary counts and tickers. Never `reasoning_md`. |
| `risk_verdicts` | `decision_id, stop_loss_trigger_id, trading_day, verdict, rejection_rule` | usage, summary, breaker |
| `orders` | `risk_verdict_id, status, fill_qty` | usage, summary. Never `broker_reason`. |
| `execution_refusals` | `reason, refused_at` | summary, breaker. Never `details`. |
| `stop_loss_triggers` | `id, observed_at` | summary |
| `account_snapshots` | `taken_at, equity` | equity (J7) |

The journal reads no `system_state`, and has no grant to. All reads run in one `REPEATABLE READ, READ ONLY` transaction.

## In memory

- **Holding**: symbol, `weight_pct`, `ref_price`, `support_session`.
- **Book**: agent, `started_on`, `index`, holdings.
- **ReportRow**: id, agent, `generated_at`, symbol, direction, `suggested_size_pct`.
- **Price**: symbol, `Decimal` close or none, with the unpriced reason.
- **DayFacts**: the counts and closed-set values the summary needs.
- **RunOutcome**: `wrote`, `nothing_to_do` with a reason, or `failed` with a reason; plus the would-be row for `--dry-run`.

## New login

`ta_journal_login`, a member of `ta_journal` only, created by `python -m trading_agent.storage.logins` like the other nine (specs/010-observe-only-deployment/contracts/logins-command.md gains a row).
