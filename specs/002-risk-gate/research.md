# Research: Risk Gate

Phase 0 decisions for `specs/002-risk-gate`. Numbered G1–G15 so they don't collide with feature
001's R-numbers.

## G1. Pure core, thin caller: the gate returns intents, never performs I/O

- **Decision**: `risk/gate.py` exposes pure functions that take plain frozen dataclasses and return a
  `GateResult`: the verdict, plus *intents* to record the halt or today's baseline. A separate
  `risk/service.py` loads the inputs as `ta_risk_gate`, calls the pure core, and persists the
  verdict and intents in one transaction.
- **Rationale**: FR-002 and Constitution I require the judgement to be a deterministic function.
  Returning "record the halt" as data rather than doing it keeps the core testable with no
  database. Only the service touches Postgres, and it makes no decisions of its own.
- **Alternatives considered**: a gate that reads the database itself. It's simpler to call, but no
  longer a pure function, and every rule test would need Postgres.

## G2. Exact decimal arithmetic, rounding toward the smaller trade

- **Decision**: all money, quantity, and percentage arithmetic uses `decimal.Decimal`. No floats
  anywhere in the gate. Share quantities are whole numbers, rounded toward the smaller trade
  (FR-003): buys and the shares sold toward a non-zero target round *down*. A 0% target sells every
  share held.
- **Rationale**: a limit check done in binary floating point can pass at 8.0000000001%. Decimals
  make SC-001 ("no approved order ever breaches") provable rather than approximately true.

## G3. Size buys at the price ceiling, not the quote

- **Decision**: a buy's price ceiling is `quote × (1 + max_buy_price_tolerance_pct/100)`. The
  position-ceiling and cash-reserve arithmetic values the new shares *and the existing holding* at
  that ceiling price (FR-001a).
- **Rationale**: Execution may fill anywhere up to the ceiling. Sizing at the quote would let a
  fill at the top of the tolerance breach the 8% ceiling or the 20% reserve. Valuing the existing
  holding at the same price is the conservative choice when prices have risen.

## G4. Target-weight arithmetic

With equity `E`, the decision's quote `q`, the ceiling price `p`, the target weight `t` (fraction),
and shares held `h`:

- **Buy.** Shares wanted `= floor((t·E − h·q) / p)`. If `t·E < h·q − q`, the target is more than one
  share *below* the holding, so reject `direction_contradicts_target`. If shares wanted `< 1`,
  reject `target_already_met`.
- **Sell.** Shares to keep `= ceil(t·E / q)` (0 when `t = 0`); shares to sell `= h − keep`. If
  `h = 0`, reject `no_position`. If `t·E > h·q + q`, reject `direction_contradicts_target`. If
  shares to sell `< 1`, reject `target_already_met`.
- **Limits on a buy.** Ceiling room `= floor(max_position_pct·E / p) − h`. Cash room
  `= floor((cash − cash_reserve_pct·E) / p)`. Approved quantity `= min(wanted, ceiling room, cash
  room)`. Each limit that lowered it is recorded as a trim. If the result is `< 1`, reject naming
  the binding limit.

"Within one share" is the tolerance for "target already met", so a PM re-run on the same target
can never produce a sub-share order or thrash in both directions.

## G5. Fixed rule precedence (FR-016)

When several rules would reject, the verdict names the first that applies, in this order:

1. `market_closed`: every request.
2. **Exits** (sell decisions and stop-loss triggers): `no_position` → `stop_loss_not_breached`
   (triggers only) → `direction_contradicts_target` → `target_already_met` → approve.
3. **Buys**: `trading_paused` → `no_account_snapshot_today` → `no_daily_baseline` →
   `daily_loss_halt` → `daily_order_cap` → `universe_no_reference_data` → `universe_listing` →
   `universe_market_cap` → `universe_dollar_volume` → `universe_share_price` →
   `direction_contradicts_target` → `target_already_met` → `max_position_pct` →
   `cash_reserve_pct` → approve (trimmed if a limit lowered the quantity).

**Rationale**: account-wide stops come before symbol-specific checks, and symbol checks before
sizing, so the named rule is the most fundamental reason. Exits skip every stop except
`market_closed` (FR-008, FR-010, FR-012, FR-020). The names are a contract
(contracts/rejection-rules.md).

## G6. Risk config: strict YAML schema, content-hash version

- **Decision**: `config/risk.yaml` is loaded with `yaml.safe_load` into a frozen `RiskConfig`.
  Loading fails on a missing key, an unknown key, a wrong type, or an out-of-range value
  (percentages in `(0, 100]`, `max_orders_per_day ≥ 0`, positive floors). A failure raises
  `RiskConfigError` naming the setting, and the service then evaluates nothing (FR-014). The
  **config version** is the first 12 hex characters of the SHA-256 of the file's raw bytes, and it
  is stored on every verdict (FR-015).
- **Rationale**: unknown keys are rejected because a misspelled limit (e.g. `max_postion_pct`)
  silently falling back to nothing is exactly the loosening Principle V worries about. A content
  hash changes with any edit, needs no manual bumping, and matches `git log` history.
- **Alternatives considered**: a hand-maintained `version:` key. Rejected: it can be forgotten.

## G7. Market open and trading day from `exchange_calendars` (XNYS)

- **Decision**: the service computes `market_open = calendar.is_open_on_minute(now)` and the
  trading day from the XNYS calendar in `exchange-calendars` 4.x, which covers regular hours,
  holidays, and early closes and needs no credential (FR-013). Today's open time comes from the
  same calendar and defines "pre-open" for the baseline.
- **Rationale**: deterministic and offline, maintained, and already models NYSE early closes.
- **Alternatives considered**: `pandas_market_calendars` (equivalent, heavier API). A hand-rolled
  holiday list, rejected because it would silently go wrong the first time NYSE adds a holiday.

## G8. Baseline selection is a pure helper, recorded by the service

- **Decision**: `choose_baseline(stored_baseline_for_today, snapshots_before_open)` returns the
  equity to use and whether to record it. If today's baseline is already stored (visible through
  `system_state_effective`), use it. Otherwise use the latest snapshot with
  `taken_at < today's open` and record it as today's baseline. With neither, `None`, and every buy
  is rejected with `no_daily_baseline`.
- **Rationale**: this implements the Clarifications answer (the pre-open snapshot) and keeps the
  "which snapshot" logic unit-testable.

## G9. Current equity: the latest snapshot from today, no freshness window

- **Decision**: current equity and cash come from the latest `account_snapshots` row whose
  `taken_at` falls on today's trading day in New York. If there is none, every buy is rejected with
  `no_account_snapshot_today` (FR-018). No other staleness limit applies (Clarifications:
  Execution's live check at purchase is the last-moment guard).

## G10. Serialize every evaluation with a transaction-scoped advisory lock

- **Decision**: the service takes `pg_advisory_xact_lock(<fixed key>)` at the start of each
  evaluation transaction. Evaluations never run concurrently.
- **Rationale**: the daily order cap is a count of approvals so far today. Two concurrent
  evaluations could each see 4 and both approve, giving 6. `SELECT ... FOR UPDATE` on the decision
  would need an UPDATE grant `ta_risk_gate` doesn't have and shouldn't get. Volume is a handful of
  evaluations an hour, so a global lock costs nothing.
- **Alternatives considered**: `SERIALIZABLE` isolation with a retry loop. More moving parts for
  the same effect at this volume.

## G11. Idempotency: look up first, and let the unique constraint settle any race

- **Decision**: under the lock, the service first looks for an existing verdict for the decision or
  trigger and returns it unchanged if found (FR-017). The `UNIQUE` constraints on `decision_id` and
  `stop_loss_trigger_id` are the backstop.

## G12. Verdicts come from exactly one source: a decision XOR a stop-loss trigger

- **Decision**: migration `0006` makes `risk_verdicts.decision_id` nullable, adds
  `stop_loss_trigger_id uuid UNIQUE REFERENCES stop_loss_triggers`, and adds
  `CHECK (num_nonnulls(decision_id, stop_loss_trigger_id) = 1)`. It also adds
  `trading_day date NOT NULL` (FR-019) and `config_version text NOT NULL` (FR-015), backfilled for
  any existing rows before `NOT NULL` is set.
- **Rationale**: it keeps one verdicts table, so `orders` keeps its single composite foreign key to
  an approved verdict (feature 001 R12), and Constitution I holds for stop-loss exits too.
- **Consequence for SC-001 (feature 001)**: an order now traces to a verdict, then to *either* a
  decision (and its reports) *or* a stop-loss trigger.

## G13. `stop_loss_triggers`: Execution records the observation, the gate re-derives the rest

- **Decision**: columns `id`, `symbol`, `observed_price` (> 0), `observed_at`. There is no entry
  price and no line: the gate takes the entry price from `positions` and the line from its own
  config, and trusts only the observed price, which it cannot fetch itself.
- **Rationale**: a buggy monitor can't force a sale by writing a wrong entry price or line.

## G14. `instrument_reference`: one row per symbol per trading day, normalized by the job

- **Decision**: columns `symbol`, `trading_day`, `security_type` (normalized: `common_stock`,
  `etf`, `adr`, `other`), `exchange_mic`, `market_cap_usd`, `avg_daily_dollar_volume_usd`,
  `share_price_usd`, `fetched_at`; primary key `(symbol, trading_day)`. `listing: us_common_equity`
  means `security_type = 'common_stock'` and `exchange_mic ∈ {XNYS, XNAS, XASE}`. That excludes
  OTC (not a listed MIC), ETFs including leveraged and inverse ones, ADRs, and options. The
  definition lives in the gate's code and is tested, and the config names it.
- **Writer**: a new group role `ta_reference_data` (ADR 0010), `SELECT, INSERT, UPDATE` so a
  re-run upserts. Readers: `ta_risk_gate`, plus `ta_assistant` and `ta_dashboard` (Principle VII:
  they read everything). This feature creates the table and role; the job that fills it is a
  later feature.

## G15. Tests: example-based, property-based, and against the real database

- **Decision**: offline unit tests for every rule and the precedence; **Hypothesis** property tests
  over ≥ 10,000 generated states for the invariants in SC-001 to SC-003 (never breach the ceiling or
  reserve, identical inputs give an identical result, exits always pass hard stops). Integration
  tests reuse feature 001's harness: migration `0006`, the extended grants matrix, the service end
  to end, idempotency, and a two-connection race on the daily order cap. One more offline test scans
  imports so that only `trading_agent.risk` may import the risk-config loader (spec Assumptions:
  the PM must never read it).
- **Rationale**: SC-001 is a universal claim, and example tests only sample it. Hypothesis searches
  for the counterexample and shrinks it when it finds one.

## Stack additions

`PyYAML` 6.0, `exchange-calendars` 4.13 (brings pandas and numpy), and `hypothesis` 6.x (dev
only). Python 3.12 and Postgres 16 are unchanged from feature 001.
