# Risk Gate

## Purpose

The single deterministic checkpoint every PM decision must pass before it can
become an order. See
[ADR 0005](../adr/0005-risk-gate-and-execution-are-deterministic.md) for why
this is a pure function and not a model call.

Not responsible for: deciding direction or size (the PM's job), placing
orders (Execution's job), or holding any credential — it makes no network
call and reads no environment beyond what its caller passes in.

## Inputs

- One `decisions` row.
- `config/risk.yaml` (see below for contents).
- Or, instead of a decision, one stop-loss trigger recorded by Execution's
  monitor ([ADR 0010](../adr/0010-stop-loss-monitor-and-universe-reference-data.md)).
- Whether the market is currently open — a boolean the caller computes from an
  exchange calendar, with no credential. The gate never fetches it itself.
- Current `positions`, the latest `account_snapshots` row, and `system_state`
  (for cash reserve, daily order count, the daily-loss halt, and the baseline).
  These are read directly and re-derived rather than trusted from the
  `decisions` row, matching Execution's own re-derivation discipline.
- The daily universe reference data, for the universe re-check.

## `config/risk.yaml` contents

Starting defaults, carried over from `trading-bot`'s proven table and
extended with this project's daily-loss breaker and universe filters
([ADR 0005](../adr/0005-risk-gate-and-execution-are-deterministic.md)):

```yaml
max_position_pct: 8          # per-symbol ceiling as a share of equity
cash_reserve_pct: 20         # cash floor; buys trimmed or rejected below it
stop_loss_pct: 20             # a position this far below avg entry exits in full
                               # (raised from 8, ADR 0010); checked every 30 min
max_orders_per_day: 5         # portfolio-wide cap on exposure-increasing orders;
                               # sells and stop-loss exits are exempt
daily_loss_halt_pct: 20       # new orders blocked for the rest of the day past this

universe:
  listing: us_common_equity   # no OTC, no leveraged/inverse ETFs, no options
  min_market_cap_usd: 500000000
  min_avg_daily_dollar_volume_usd: 10000000
  min_share_price_usd: 5       # excludes low-priced stocks that behave like
                               # penny stocks even when market cap/volume clear
```

Changes to this file follow the review process in
`docs/policy/agent-management.md` — editability is a deliberate trade-off
([ADR 0005](../adr/0005-risk-gate-and-execution-are-deterministic.md)), not a
license to loosen limits without review.

## Outputs

One `risk_verdicts` row: `approved` with a fully-specified order (symbol,
side, quantity, limit price, time-in-force), or `rejected` with the specific
rule that fired.

## Edge cases

- **Market closed at evaluation time**: reject outright, regardless of any
  other rule — no order should ever be approved outside market hours.
- **Daily-loss halt already active**: reject every new-exposure decision
  outright; exits (sells, stop-loss triggers) are exempt, same as the
  per-day order cap.
- **`max_orders_per_day` already reached**: reject further exposure-increasing
  decisions for the rest of that day; exits remain exempt.
- **Decision would breach `max_position_pct` only partially** (e.g. a buy
  that would push a position from 6% to 10%): trim the order to the ceiling
  rather than rejecting outright, mirroring `trading-bot`'s existing
  behavior — reject only when trimming to zero is the only option (e.g. the
  position is already at the ceiling).
- **Decision's symbol fails the universe filter** (e.g. below the market-cap
  floor): reject unconditionally. This should be rare in practice, since both
  analysts scan within the eligible universe, but the gate re-checks it
  independently rather than trusting that upstream filtering held. A symbol
  with no reference data from the current trading day fails too.
- **Stop-loss trigger**: approve a full exit of the shares held only if the
  trigger's observed price really is at or below the stop-loss line under the
  position's average entry price. Otherwise reject, so a faulty monitor can't
  force a sale. An approved stop-loss exit is exempt from the daily order cap
  and the daily-loss halt.
- **No pre-open account snapshot today**: no daily-loss baseline can be
  recorded, so reject every exposure-increasing decision until one exists.

## Interfaces

- Reads `decisions`, `positions`, `account_snapshots` (cash and equity),
  `system_state`, `config/risk.yaml`.
- Writes only `risk_verdicts`.
- Also responsible for recording `system_state.halt_triggered_on` (today's
  date) when its own evaluation detects the daily-loss line has been crossed,
  and for recording that day's `daily_starting_equity` baseline at its first
  evaluation of a new trading day. It never needs to *clear* the halt — "halt
  active" is computed from the date and clears itself the next trading day
  (see `docs/specs/data-model.md`).

## Non-goals

- Does not evaluate whether a decision is a *good* trade — only whether it's
  an *allowed* one. Quality of judgment is entirely upstream, in the PM.
- Does not force-liquidate positions — see
  [ADR 0006](../adr/0006-autonomous-operation-with-daily-loss-breaker.md).
