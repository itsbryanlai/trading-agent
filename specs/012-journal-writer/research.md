# Research: Journal writer

Decisions for `specs/012-journal-writer`. Each has the decision, why, and what was rejected. The owner's choices are in the spec's Clarifications; this file settles what they leave to the plan.

## J1. Railway can run it on a cron schedule

**Decision**: the `journal` service in `.railway/railway.ts` carries `deploy: { cronSchedule: "30 22 * * 1-5", restartPolicyType: "NEVER" }`. 22:30 UTC is 18:30 ET in summer and 17:30 ET in winter, after every close, early closes included, and before midnight New York time in both.

**Verified 2026-10-09**: the pinned SDK, `railway@3.12.0`, declares `cronSchedule?: string | null` and `restartPolicyType?: "ON_FAILURE" | "ALWAYS" | "NEVER" | null` on its `DeployConfig` (`dist/index-UavU4i3L.d.ts`, read from unpkg). Railway's IaC reference page doesn't mention cron, so the type declaration is the evidence. The owner's `railway config plan` shows the schedule before anything is applied (quickstart step 5).

**Why `NEVER`**: a failed run fails for a reason a restart won't fix within minutes (no snapshot, a rejected key). A restart loop would also repeat the market-data calls. The owner re-runs by hand the same evening (FR-003). Transient fetch errors are retried inside the run (J8).

**Rejected**: an always-on loop (ADR 0022); `ON_FAILURE` (above).

## J2. `/quote` after the close is the session's close, or the symbol goes unpriced

**Decision**: a quote counts as today's close only when its price is usable and its own trade time `t` falls inside today's session: on or after `calendar.open_time(D)` and at or before `calendar.close_time(D)` plus `close_grace_minutes` (default 5, for the closing auction's prints). Anything else is unpriced for the day (FR-012 of the spec).

**Why**: Finnhub doesn't document whether `/quote` reflects after-hours trading. If it does, `t` lands after the close and the price is refused rather than silently used. If every symbol is refused that way, the run fails (FR-019), which the owner sees the first evening. An illiquid symbol whose last trade was mid-afternoon still prices: its last regular-session trade is its close.

**Not yet verified**: what `t` reads after the close. The owner's `--check` after a close prints `t` against the close for each symbol (quickstart step 3) before the service is released, as ADR 0016's status records for the PM's quote.

**Reuse**: `reference.finnhub.FinnhubProvider.get_quote`, as the PM does. `reference` is a layer below, so no copy is needed. The key travels in a header, redirects are refused, and errors map to `KeyRejected`, `NotPermitted`, `RateLimited` and `ProviderUnavailable`.

## J3. Which session a run writes

**Decision**: `D = calendar.trading_day(now)`. The run writes only if `calendar.is_session(D)` and `now ≥ calendar.close_time(D)`. Otherwise it logs `not_a_session` or `before_close` and exits 0. The previous row is the latest row with `trading_day < D`. A row with `trading_day > D` refuses the run (`future_row`, exit 1): the clock or the data is wrong, and continuing would fork the books.

## J4. The report window

**Decision**: the reports applied, and counted for usage, are those with `previous_close < generated_at ≤ close_time(D)`, where `previous_close = close_time(previous row's day)`. With no previous row: every report whose New York date is `D` and `generated_at ≤ close_time(D)` (spec, clarify Q2).

**Why**: the same window on a re-run gives the same reports (SC-004). A report written after today's close but before the run lands in tomorrow's window instead of being lost. Reports written on a weekend or during a missed session fall in the next window.

**Late reports**: a report whose `trading_day(generated_at)` is a session before `D` is *late*. It still enters at today's close, and is counted in `late_reports`.

## J5. The book arithmetic

All arithmetic is `Decimal`. Weights are percentages of the book, as in `reports.suggested_size_pct`. For each agent, in this order:

1. **Value.** For each holding, `r_i = price_D / ref_price - 1`, with `price_D = ref_price` (so `r_i = 0`) if unpriced. The book's return `R = Σ w_i · r_i / 100`. `index_D = index_prev × (1 + R)`.
2. **Drift.** `w_i ← w_i × (1 + r_i) / (1 + R)`. `ref_price ← price_D` (or unchanged if unpriced).
3. **Apply reports**, in `generated_at` order, latest per symbol winning:
   - buy or hold: `w ← suggested_size_pct`; `ref_price ← price_D`; `support ← support session` (J6). Unpriced and not already held: skipped and listed in `skipped_targets`.
   - sell: `w ← min(w, suggested_size_pct)`; a sell never changes `support`. A sell on a symbol not held does nothing.
   - `no_action`: nothing.
4. **Exit stale holdings.** Any holding with `sessions_since(support, D) ≥ holding_sessions` leaves.
5. **Drop** holdings whose weight is 0.
6. **Scale.** If `Σ w > 100`, every `w ← w × 100 / Σ w`, and `scaled_by` records the factor.
7. **Round** every stored weight to 6 decimal places, the index to 8, prices as received. Rounding happens once, at storage. The next run reads the rounded values, so a run is a function of the previous row and today's inputs.

`1 + R` can't be 0 or negative: weights are non-negative and sum to at most 100, and a price can't go below 0, so `R ≥ -1`. `R = -1` (every holding at a price of 0) can't occur either, because a zero price is unusable and unpriced. A test pins this.

**A new agent** (one with a report in the window and no book) starts with `index = 100` and no holdings, so its first day's return is 0.

**Rejected**: storing returns and recomputing the index from history (unbounded reads, and drift needs the weights anyway).

## J6. Counting sessions for the holding limit

**Decision**: `sessions_since(a, b)` counts the sessions `s` with `a < s ≤ b`, by stepping back from `b` with `calendar.previous_session` at most `holding_sessions` times. A report's *support session* is `trading_day(generated_at)` if that day is a session, otherwise `D`. So a missed run never extends a holding, and a weekend report counts from the session it took effect.

**Why not a new calendar function**: `risk.calendar` is Risk Gate code. The loop needs nothing new from it, so the gate's module stays untouched.

## J7. Equity

**Decision**: from `account_snapshots` rows whose New York date is `D`. Open: the latest with `taken_at ≤ open_time(D)`, else the earliest that day. Close: the latest that day. No row that day: the run fails (`no_account_snapshot`, exit 1).

**Account return** (spec, clarify Q3): `equity_close / previous row's equity_close - 1`; on the first-ever run, `equity_close / equity_open - 1`. Null if the base is 0.

**Known limit**: Execution records equity at each stop-loss window during market hours (ADR 0014), so the last snapshot is normally from the last window, up to about 30 minutes before the close. The journal has no broker key and can't take its own. The summary labels it as "last recorded", and `account.close_taken_at` records the time.

## J8. Fetching prices

**Decision**: one `/quote` per symbol in any book or newly targeted, in symbol order. Paced at `finnhub_calls_per_minute` (default 20, as the OI, because the account may be shared, ADR 0016 §5). A `RateLimited` or `ProviderUnavailable` symbol is retried at most twice, after a pause of one pacing interval. Fetching stops at `fetch_deadline_seconds` (default 480), and the rest are unpriced. `NotPermitted` is unpriced at once. `KeyRejected` fails the run (`market_data_key_rejected`).

**Budget**: 480 s at 20 a minute is about 160 calls. The books of two agents with five-session holdings hold far fewer symbols. The deadline plus database work keeps the run under 10 minutes (SC-005). The loader refuses a deadline over 540 s.

**Failure**: if at least one symbol needed a price and none got one, the run fails (`no_prices`, exit 1). Otherwise unpriced symbols are listed per agent.

## J9. Usage counts

**Decision**: for each agent, over the J4 window's reports:
- `written`, `argued` (direction not `no_action`), `no_action`;
- `cited`: reports with any `decision_reports` row;
- `cited_decisions`: distinct decisions citing any of them;
- `approved`: of those decisions, ones whose verdict is `approved`;
- `filled`: of those, ones with an order whose `fill_qty > 0`.

A decision that cites both agents counts once for each. Counts are read at the run, so a decision or fill that lands after the run is not seen. That's after the close, so in practice nothing.

## J10. The summary

**Decision**: one fixed Markdown template (contracts/summary-template.md), `SUMMARY_VERSION = "0.1"`, logged with every run and added to `docs/policy/versioning.md`. It's built from closed sets and numbers:
- decision directions;
- rejection rules: `risk_verdicts.rejection_rule` values matching `^[a-z_]{1,40}$`, otherwise counted as `other`;
- order statuses and refusal reasons (both closed by database checks);
- tickers matching `^[A-Z][A-Z0-9.\-]{0,9}$`, otherwise counted as malformed.

It never reads `reasoning_md`, `rationale_md`, `sources`, `broker_reason`, refusal `details` or attribution (spec FR-016, FR-017). The day's facts:
- decisions: `trading_day(generated_at) = D`;
- verdicts: `trading_day = D`;
- orders: those for those verdicts;
- refusals and stop-loss triggers: by their own timestamp's New York date.

The breaker line reads "triggered" when any verdict that day is `daily_loss_halt` or any refusal is `daily_loss_line_crossed`.

**Length**: the fixed lines come first and are at most about 700 characters. The symbol list and missed-session list follow, cut with "and N more" so the whole summary stays within 2,000 characters (FR-018). A test generates a day with 500 decisions and checks the bound.

## J11. The row's state, and re-runs

**Decision**: `per_agent_attribution` holds `schema_version: 1` and everything the next run needs (contracts/attribution.md). A run reads only the previous row's attribution object. A schema version it doesn't know refuses the run (`unknown_schema`, exit 1), rather than guessing.

The write is one `INSERT … ON CONFLICT (trading_day) DO UPDATE`, setting every column and `written_at = now()`. Reads run first, in one `REPEATABLE READ, READ ONLY` transaction. Prices are fetched outside any transaction. The write is its own transaction.

**Two runs at once** (the cron and a manual one): both read the same previous row and write the same day. The later write wins, and both computed from the same inputs, apart from a price that changed between fetches, which after the close it doesn't. No lock is needed.

## J12. Exit codes and modes

| Exit | Meaning |
|---|---|
| 0 | wrote today's row, or nothing to do (`not_a_session`, `before_close`) |
| 1 | a named failure; nothing written (`no_account_snapshot`, `no_prices`, `market_data_key_rejected`, `future_row`, `unknown_schema`) |
| 2 | refused to start: bad arguments, config or a missing variable |
| 3 | the database is unreachable, or a read or write failed |
| 4 | crashed (never Python's default 1) |

`--dry-run` does everything except the write, and prints the row. It needs the database login, because it reads. `--check SYMBOL …` needs only the market-data key. It fetches each symbol's quote and prints `c`, `t`, today's open and close, and whether J2 would accept it. The owner names the symbols; none are built in.

## J13. Package and layering

**Decision**: a new top-layer package, `trading_agent.journal`, added to the first layer in `pyproject.toml`'s import contract. It imports `risk.calendar`, `reference.finnhub` and `reference.provider`, and `storage.db`. It imports no sibling, no `llm`, nothing from `risk.gate` or `risk.rules`, and no `execution`. An import test pins this.

**Pure core**: `books.py` (J5, J6), `usage.py` (J9), `summary.py` (J10) and `state.py` (J11) take plain values, so the arithmetic and the "no model text" property are tested without a database or network.

## J14. Credentials and deployment

- **Login**: `ta_journal_login`, the tenth row in `storage/logins.py` and its contract. No grant changes: `ta_journal` already reads every table in J7–J10 and writes `journal` (contracts/role-grants.md). An integration test asserts the login can't read `system_state` and can't write anything but `journal`.
- **Variables**: `JOURNAL_DATABASE_URL`, `JOURNAL_FINNHUB_API_KEY`. Both are in `.env.example`, the service-layout contract and the guard test's contract. The guard test also learns that `journal` is the one service with a cron schedule and `restartPolicyType: "NEVER"`, and that no other service has one.
- **Config**: `config/journal.yaml`:
  - `holding_sessions: 5` (1–60);
  - `finnhub_calls_per_minute: 20` (1–300);
  - `fetch_deadline_seconds: 480` (30–540);
  - `close_grace_minutes: 5` (0–30).

  Every key is required, unknown keys are refused, and it's changed only through code review.
- **No migration.**
