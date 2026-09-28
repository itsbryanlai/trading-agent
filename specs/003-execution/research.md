# Research: Execution

Phase 0 decisions for `specs/003-execution`. Numbered E1–E15 so they don't collide with feature
001's R-numbers or feature 002's G-numbers. Broker facts were checked against Alpaca's API
reference and the `alpaca-py` SDK docs on 2026-09-27; each is cited where it's used.

## E1. Pure core, a broker port, and a thin service

- **Decision**: split Execution the same way as the gate (002 G1).
  - A **pure core** (`execution/checks.py`, `ids.py`, `fills.py`, `monitor.py`, `schedule.py`,
    `model.py`) takes plain frozen values and returns plain outcomes: submit (with the exact order),
    refuse (with a reason and the numbers), or retry later. No database, no broker, no clock.
  - A **broker port** (`execution/broker.py`): a `Protocol` with the handful of calls Execution
    makes (account, positions, latest ask, latest trade, submit, look up by client id, get order).
    It returns the project's own frozen dataclasses with `Decimal`s, never SDK objects.
  - One **real adapter** (`execution/alpaca.py`), the only module that imports the broker SDK, and
    one **fake** (`tests/fakes/broker.py`) used by every test.
  - A **service** (`execution/service.py`) that loads rows as `ta_execution`, calls the broker
    port, runs the core, and writes the outcome.
- **Rationale**: FR-016 (deterministic, testable with fixed inputs) and FR-019 (fake broker only).
  With the broker behind a port, every behaviour in the spec can be driven by a scripted fake, and
  the core can be tested with no database at all.
- **Alternatives considered**: calling the SDK directly from the service and mocking it. Rejected:
  mocks of a third-party SDK drift from its real behaviour silently, and the SDK's objects would
  leak `str` prices into the arithmetic.

## E2. Paper-only guard: an address fixed in code, then an authenticated read there

- **Decision**:
  1. `PAPER_TRADING_URL = "https://paper-api.alpaca.markets"` is a constant in `alpaca.py`. The
     trading client is always built with `paper=True` **and** `url_override=PAPER_TRADING_URL`,
     so the SDK's own default can't decide it (`TradingClient(..., paper=True, url_override=None)`
     picks `url_override` first, then paper, then live — alpaca-py `trading/client.py`).
  2. If the environment variable `ALPACA_BASE_URL` is set to anything other than that constant,
     startup stops before any client is built. It is optional; unset is fine.
  3. At startup, `GET /v2/account` against the paper address must succeed. Any failure (bad keys,
     network, non-2xx) stops startup (FR-013).
- **What the docs don't give us**: there is **no documented field** on the Account object that
  identifies a paper account. `account_number` is described only as "Account number"; example
  responses show both `PALPACA_123` and `010203ABCD`, and the `PAPER_ONLY` status value is
  undocumented while an example paper account shows `ACTIVE`
  (docs.alpaca.markets/reference/getaccount-1). So check (b) of FR-013 is met by what *is*
  documented: paper keys are distinct from live keys (docs.alpaca.markets/docs/paper-trading), and
  the fixed address only serves paper accounts. A successful authenticated read there proves the
  credentials belong to a paper account. The account number is logged at startup for the owner to
  eyeball, but an undocumented prefix is never a pass/fail rule: a false refusal would be harmless,
  but a false pass on a guessed rule would give false confidence.
- **Market data**: one host (`data.alpaca.markets`) for paper and live, and data calls can't place
  orders, so the data client needs no guard beyond sharing the same keys.
- **Import scan**: only `trading_agent.execution.alpaca` may import `alpaca.trading`, and nothing
  outside `trading_agent.execution` may import `trading_agent.execution.alpaca` (Constitution I,
  III: one holder of the broker credential).

## E3. Order identifier (ADR 0012)

- **Decision**: `order_id(verdict) = f"{trading_day}-{symbol}-{side}-{str(verdict_id)[:8]}"`, a
  pure function. The database checks the format and that the suffix matches `risk_verdict_id`
  ([data-model.md](data-model.md)). Before submitting, the service checks whether an order with
  that identifier already exists for a *different* verdict; if so it refuses with
  `identifier_clash` (FR-008).
- **Length**: at most about 40 characters, well under the broker's 128-character limit for a
  client order id (docs.alpaca.markets/reference/postorder).
- **Duplicate handling at the broker is undocumented.** The reference lists only 403 and 422 for
  order submission and says nothing about duplicate client ids. So Execution never *relies* on the
  broker rejecting a duplicate: it looks the identifier up first (E5). If the broker also rejects
  duplicates, that's a bonus, not a guarantee the design leans on.

## E4. Every approval ends with exactly one outcome

- **Decision**: a new insert-only table `execution_refusals`, keyed one-to-one to an approved
  verdict, alongside `orders`. A trigger on each table rejects a row if the other already has one
  for that verdict. Refusal reasons are a fixed set of names
  ([contracts/refusal-reasons.md](contracts/refusal-reasons.md)).
- **Lapsed approvals are refused too.** Each Execution tick records `approval_expired` for any
  approved verdict with no outcome whose trading day is over (an earlier day, or today after the
  close). So every approval ends with exactly one row, either an order or a refusal, and "why
  didn't this trade?" always has an answer in the database.
- **But only after asking the broker.** A crash between submission and commit just before the
  close would otherwise leave a real order at the broker whose verdict the sweep then marks
  `approval_expired`, and the exclusivity trigger would make that order unrecordable. So the sweep
  goes through the same locked path as a submission (E5 steps 1–3): if the broker has an order
  under the identifier, it is recorded as the outcome instead. If the broker can't be reached, the
  sweep leaves the approval for the next tick; an unsubmittable approval waiting a little longer
  for its outcome row is harmless.
- **Transient conditions are not refusals**: broker unreachable, no valid live quote, a bad risk
  config (E12). They are logged and retried on the next tick while the approval is valid (FR-007).
- **Alternatives considered**: a `not_submitted` status on `orders`. Rejected: `orders.id` is a
  broker identifier, and a row that was never sent to the broker would carry one anyway, blurring
  "sent" and "not sent" in every query.

## E5. Crash safety: look up before submitting, write after

- **Decision**: each approval is processed inside one database transaction holding Execution's
  advisory lock (`pg_advisory_xact_lock`, a key distinct from the gate's):
  1. Skip if the verdict already has an order or a refusal.
  2. If an `orders` row with this identifier already exists for a *different* verdict, refuse
     `identifier_clash` and stop. This must come before step 3, or the lookup would return the
     other verdict's broker order and recording it would fail on the primary key every tick.
  3. Look the identifier up at the broker (`get_order_by_client_id`). If found, record it as the
     order (including its limit price, E6) and stop. This is the crash-recovery path, and it runs on
     every submission, so recovery isn't a separate code path that only runs after a crash (FR-009).
  4. Run the checks (E6). Refuse, or submit.
  5. Insert the `orders` row with what the broker returned, and commit.
- **Crash windows**: a crash before step 4's submission leaves nothing anywhere, so the next tick
  starts over. A crash after submission but before commit leaves an order at the broker and none
  in the database; the next tick finds it in step 3.
- **Remaining risk, bounded**: a *timeout* on submission followed by a broker lookup that lags
  behind the order it placed. If the broker then also accepted a repeated client id (undocumented,
  E3), a resubmission would be a second order. So a verdict whose submission raised
  `BrokerUnavailable` goes into an in-memory "maybe placed" set with the time, and is not
  resubmitted until a `find_order` at least 2 minutes after the timeout still finds nothing. A
  restart forgets the set, but a restart takes longer than the broker's lookup lag in practice, and
  the lookup still runs first.
- **A timeout on submission** is treated as "maybe placed": the broker's own guidance is to check
  rather than resubmit or assume it failed (docs.alpaca.markets/us/docs/working-with-orders). The
  transaction rolls back and the next tick's step 3 settles it.
- **A rejection is double-checked.** If the broker's lookup lags a "maybe placed" order, the next
  tick could resubmit, and the broker might reject the duplicate client id. Recording that as a
  final `rejected` order with no broker id would hide a live order from polling and from the
  open-order cash check. So on `OrderRejected`, Execution calls `find_order` once more: if an order
  exists under the identifier, that is recorded instead; only if none exists is the rejection
  recorded.
- **Real transactions, not savepoints.** Execution's connection runs with `autocommit=True`, so
  each `conn.transaction()` block is a real transaction that commits at its end and releases the
  advisory lock. The runner opens it that way, and both `startup` and `tick` assert `conn.autocommit`, refusing to run otherwise. `tick` takes `_allow_savepoints=False`; only the rolled-back test harness passes `True`. Any other host of `tick` must supply an autocommit connection (a caller obligation in the interface contract). The
  shared `storage.db.connect` helper commits only when the connection closes, which would leave
  every write of a long-running Execution uncommitted and invisible to other components. The
  single-connection test harness can't show this, so one integration test uses two real committed
  connections (Execution's and the gate's) against a database it cleans up itself.
- **A lost database connection ends the process.** A `psycopg.OperationalError`, or a closed
  connection, is not a per-unit failure: every unit would fail forever and no exit would go out. The
  runner exits non-zero so the platform restarts it (ADR 0013). The gate's trigger runner does the
  same.
- **Failures are isolated per unit of work.** Each approval, each order sync, the reconciliation,
  the monitor and the snapshot run in their own `try` block: an unexpected error in one is logged
  with the verdict or order id and the tick carries on. **Exits are processed before buys**, so a
  poisoned buy can never delay a stop-loss exit. A tick never aborts wholesale on one bad row.
- **Serialization** also makes the open-order accounting in E6 exact: no second submission can
  happen between reading open orders and placing a new one.
- **Alternatives considered**: write a "submitting" row first, then submit. Rejected: it needs a
  state that isn't a real broker state, and a crash still needs the same broker lookup to resolve
  it, so it adds a status without removing any work.

## E6. The buy and exit checks, in a fixed order

All arithmetic is `Decimal`, as in the gate (002 G2).

**Buy** (first that applies wins; a *refusal* is final, a *retry* is not):

| # | Check | Outcome |
|---|---|---|
| 1 | verdict's trading day ≠ today, or today and the market has closed | refuse `approval_expired` |
| 2 | market not open yet (today, before the open) | retry |
| 3 | identifier used by another verdict | refuse `identifier_clash` |
| 4 | risk config missing or invalid | retry (logged) |
| 5 | manual pause is on | refuse `trading_paused` |
| 6 | no baseline for today | refuse `no_daily_baseline` |
| 7 | fetch account; **record a snapshot**; that equity, *or any snapshot's equity since today's open*, ≤ baseline × (1 − `daily_loss_halt_pct`/100) | refuse `daily_loss_line_crossed` |
| 8 | fetch latest ask; missing, zero, or older than 60 s | retry |
| 9 | ask > the verdict's `limit_price` (the ceiling) | refuse `quote_above_ceiling` |
| 10 | (held + open buy qty + qty) × ask > `max_position_pct`% × equity | refuse `max_position_pct` |
| 11 | cash − open buy cost − qty × ask < `cash_reserve_pct`% × equity | refuse `cash_reserve_pct` |
| 12 | — | submit a day limit buy of `qty` at the ask |

- **Row 7 looks back over the day.** Once any snapshot since the open has been at or below the
  line, no buy goes out for the rest of the day, even if equity has since recovered. That's
  Principle IV ("new order submission halts for the remainder of the day"), and it matches the
  gate, which records the halt from the same snapshots. Execution can't read the halt column, and
  doesn't need to.
- *held* is the broker's live position quantity. *Open buy qty* and *open buy cost* are the
  unfilled remainder of Execution's own non-final buy orders for that symbol (qty) and for all
  symbols (cost, at their limit prices). `orders` has no symbol, side or quantity columns, so these
  come from joining each order to its verdict's `approved_order` (`symbol`, `side`, `qty`);
  remaining = `qty − coalesce(fill_qty, 0)`, cost = remaining × `orders.limit_price`. Every buy row
  carries a limit price, enforced by a `CHECK` (data-model.md), including rows recorded by the
  crash-recovery lookup, so the sum can never silently skip one. The broker's `cash` doesn't fall until a fill, so without
  this two unfilled buys could together breach a reserve each passes alone (spec Edge Cases).
- The existing holding is valued at the live ask, the same conservative choice the gate makes at
  its ceiling (002 G3).
- The ask is submitted as-is if it already has at most two decimals, else rounded **down** to the
  cent. Rounding down never exceeds the ceiling. The universe floor ($5) keeps every symbol above
  the sub-penny range.

**Exit** (sell decision or stop-loss exit):

| # | Check | Outcome |
|---|---|---|
| 1 | verdict's trading day over | refuse `approval_expired` |
| 2 | market not open yet | retry |
| 3 | identifier used by another verdict | refuse `identifier_clash` |
| 4 | broker's held qty − open sell qty < approved qty | refuse `shares_held_differ` |
| 5 | — | submit a day market sell of `qty` |

No pause, halt, baseline, or config check on exits (FR-006, FR-018, FR-020).

## E7. Order status: polling, mapped onto six states

- **Decision**: every tick, poll each non-final order with `get_order_by_id` and map the broker's
  status:

| Broker status | Recorded |
|---|---|
| `new`, `accepted`, `pending_new`, `accepted_for_bidding`, `held`, `calculated`, `stopped`, `suspended`, `pending_cancel`, `pending_replace` | `submitted`, or `partially_filled` if filled qty > 0 |
| `partially_filled` | `partially_filled` |
| `filled` | `filled` |
| `done_for_day`, `expired` | `expired` |
| `canceled` | `canceled` |
| `rejected` | `rejected` |
| `replaced` | not expected (Execution never replaces); logged as an error, status left unchanged |

- Source: order statuses and which are terminal (docs.alpaca.markets/docs/orders-at-alpaca); an
  unfilled day order is "automatically canceled" after the closing auction
  (docs.alpaca.markets/reference/postorder, TimeInForce). A partly filled day order reportedly ends
  the day as `done_for_day`, which gets no further updates until the next day. Both end up final
  here.
- **Why polling, not the `trade_updates` stream**: the stream is the broker's recommended way to
  track orders, but it's a long-lived connection to keep alive and reconnect, and it's hard to
  fake deterministically. At a handful of orders a day, a poll every tick costs a few requests a
  minute. The trading API's rate limit isn't documented; this load is far below any plausible one.
  Revisit if order volume grows.
- **After the close**: the last tick of the day and the first of the next both sync, so an order
  closed out at the end of the session is recorded as final by the next morning at the latest.

## E8. Positions from fills, reconciled to the broker

- **Decision**: when a poll shows an order's cumulative filled qty rise from `f₀` (avg `p₀`) to
  `f₁` (avg `p₁`), the new fill is `Δq = f₁ − f₀` at `Δp = (f₁·p₁ − f₀·p₀) / Δq`. A pure function
  applies it: a buy sets `qty' = qty + Δq` and `avg' = (qty·avg + Δq·Δp) / qty'`; a sell sets
  `qty' = qty − Δq` with `avg` unchanged, deleting the row at zero. The order update and the
  position update are one transaction.
- **Reconciliation**: after applying fills, each tick compares `positions` with the broker's
  `get_all_positions()` (qty exactly, `avg_entry_price` rounded to the column's 4 decimals). Any
  difference is logged at warning level with both values, and the broker's figures are written
  (FR-011). This also covers anything Execution missed (e.g. a fill while it was down).
- **Rationale**: the gate sizes from `positions`, and the stop-loss line is measured from
  `avg_entry_price`, so these must match what the broker actually holds. Deriving from fills keeps
  the table explainable; reconciling keeps it right.
- **Stop-loss ordering**: the monitor runs only after the tick's sync and reconciliation, so the
  gate, which re-derives the stop line from `positions`, sees the broker's current entry price.

## E9. Live prices: the IEX feed, with a freshness bound

- **Facts**: paper and free accounts get real-time **IEX** data only; requesting SIP on the latest
  endpoints without a subscription is refused. IEX is one exchange, a small slice of the market's
  volume. An ask of `0` means "no active ask". Paper fills are simulated against the full national
  best bid and offer (docs.alpaca.markets/docs/paper-trading, about-market-data-api,
  market-data-faq).
- **Buy price**: `get_stock_latest_quote(..., feed=iex)`, ask price. Treated as missing (retry, E6
  #8) when zero or when its timestamp is more than 60 seconds old.
- **Stop-loss price**: `get_stock_latest_trade(..., feed=iex)`, trade price (Clarifications). A
  trade older than 30 minutes (one monitor window) is skipped for that cycle with a warning rather
  than compared against the line, since an old print could miss a real breach or invent one.
- **Consequence, accepted**: an IEX ask can sit below the national ask, so a limit buy at it may
  not fill immediately, and a day order unfilled at the close lapses. That errs toward not buying.
  The universe floors keep symbols liquid enough that IEX quotes are usually current.
- The 60-second and 30-minute bounds are constants in `execution/checks.py` and
  `execution/monitor.py`, not risk limits: they decide whether a price is usable, not how much risk
  to take.

## E10. Grants

- `ta_execution` gains `SELECT, INSERT` on `execution_refusals` and a **column** grant
  `SELECT (trading_paused) ON system_state`. It needs only the pause flag (FR-018); the halt and
  baseline columns stay out of reach, and Execution computes its own baseline (E11).
- `ta_execution`'s `UPDATE` on `orders` is narrowed to the columns that change after submission.
- Readers of `orders` (journal, Assistant, dashboard) gain `SELECT` on `execution_refusals`
  (Constitution VII: they read everything).
- The grants contract ([role-grants.md](../001-data-model/contracts/role-grants.md)) is amended and
  `grants_matrix.py` still has to equal the database's own records in both directions.
- Not done here: removing `ta_risk_gate`'s unused `SELECT` on `system_state_effective` (a separate
  loose end from feature 002).

## E11. The baseline, defined once and read twice

- **Decision**: Execution derives today's baseline itself from `account_snapshots`: the equity of
  the last snapshot on today's New York date taken before today's open. That's the gate's exact
  rule (002 G8) and uses the same calendar (`trading_agent.risk.calendar`). An integration test
  runs both over the same snapshots and asserts they agree.
- **Rationale**: reading the gate's stored copy would need a grant on the halt and baseline columns
  and would trust a value Execution can re-derive from the source. The spec asks for a distrustful
  re-read (Constitution I).

## E12. Risk config: shared file, fail closed on buys only

- **Decision**: Execution loads `config/risk.yaml` with the gate's own loader
  (`trading_agent.risk.config.load_config`), so both judge against the same validated values. The
  import guard from 002 (only `trading_agent.risk` may import the loader) is widened to allow
  `trading_agent.execution`. The Portfolio Manager, and everything else, is still forbidden.
- A config that fails to load blocks **buys** (logged, retried each tick, never a refusal, since a
  fix lands by deploy) and **the stop-loss monitor** (without `stop_loss_pct` there's no line to
  compare against, and the gate would refuse to evaluate a trigger anyway). Approved **exits** are
  still submitted (FR-020).
- **This state is loud.** A broken config switches off stop-loss protection, which Principle IV
  relies on. Every tick in that state logs at **error** level, naming the failing setting and
  saying the stop-loss monitor is off, not just a warning. Alerting the owner (e.g. Telegram) is
  the Assistant's or the dashboard's job later; the log line is the contract they read.

## E13. Scheduling: Execution runs its own tick

- **Decision**: Execution is driven by one entry point, `tick(now)`, meant to be called about once
  a minute. Each tick, in order: sync open orders and reconcile positions (E7, E8) → record
  `approval_expired` for lapsed approvals (E4) → process today's approvals that have no outcome,
  **exits first, then buys** (E5, E6) → run the stop-loss monitor if a 30-minute window has no
  successful check yet → record the pre-open snapshot if it's due. Each step is isolated (E5). A pure `schedule.py` answers "what's due at `now`" from the
  exchange calendar.
- **Approvals are picked up, not handed over.** Execution finds new approvals itself each tick
  (spec Assumptions), so it doesn't depend on the orchestrator to call it after each gate verdict.
  An approval is submitted within about a minute.
- **Stop-loss windows**: `[open + 30k min, open + 30(k+1) min)` clipped to the close, following the
  calendar's early closes. That needs the session close, so `trading_agent.risk.calendar` gains
  `close_time(day)` (early closes included), used also for "the market has closed" in E6 row 1 and
  the lapsed-approval sweep. A window is marked done only when the price check for **every** held
  position succeeded (a broker failure or a stale trade leaves it due, so the next tick retries
  within the same window, SC-005). A position whose last trade has been stale for two consecutive
  windows is logged at error level, since it is going unprotected. The record of which windows
  succeeded is in memory: a restart re-runs the current window's check, which is harmless.
- **Duplicate triggers are avoided**: no new trigger for a symbol with an open sell order, a trigger
  from today not yet evaluated, or an approved exit from today with no outcome yet. A trigger the
  gate *rejected* has a verdict, so it doesn't block a later one.
- **A missing gate runner is visible.** Every tick, Execution counts triggers from today with no
  verdict older than 5 minutes. Each is logged at error level ("stop-loss trigger unevaluated;
  is the gate's trigger runner running?") and counted in `TickReport`. Execution can read
  `risk_verdicts` and `stop_loss_triggers` already.
- **Pre-open snapshot**: due from 60 minutes before the open until the open, on trading days, until
  one exists for today (checked in `account_snapshots`, so it survives restarts). A broker failure
  just means the next tick tries again (FR-015).
- **Hosting**: `python -m trading_agent.execution` runs the startup guard and then calls `tick`
  every 60 seconds. Whether it runs as its own process or is hosted by the orchestrator's scheduler
  in the worker service is the orchestrator feature's call; `tick` works the same either way.
- **The gate evaluates triggers in its own process** (owner decision after `/speckit-analyze`,
  spec Clarifications 2026-09-28). Execution records and commits a trigger as `ta_execution`, and
  that row *is* the hand-off. A small gate-side runner, `python -m trading_agent.risk`, holding only
  `RISK_GATE_DATABASE_URL`, calls `evaluate_stop_loss_trigger` about once a minute for every
  trigger observed on the current trading day that has no verdict yet. Execution then finds the
  approved exit on its next tick like any other approval (exits first). Worst-case added delay is
  about two minutes, small next to the 30-minute monitor interval.
  - **Why**: the Execution process never holds the gate's credential, so it can't write a verdict,
    even by a bug. Principle III wants that enforced by credentials, not by code discipline, and it
    also keeps the gate from ever sharing a process with the broker client.
  - **Alternatives considered**: an injected callable in Execution's process wrapping a gate
    connection (rejected: Execution's process would hold `ta_risk_gate`); leaving evaluation to the
    orchestrator (rejected: stop-losses wouldn't work until that feature exists).
  - Recorded in [ADR 0013](../../docs/adr/0013-deterministic-services-run-their-own-loops.md),
    which qualifies ADR 0003's rejected "each process manages its own schedule" for these two
    deterministic services.
  - Each trigger is evaluated in its own `try` block: one that raises is logged with its id and the
    pass continues, so a poisoned trigger can't block the others.
  - The runner is hosting only: it adds no logic to the gate and needs no new grant
    (`ta_risk_gate` already reads `stop_loss_triggers` and writes `risk_verdicts`). Triggers from an
    earlier trading day are never evaluated; they are logged and left alone.

## E14. Tests never touch the broker

- **Decision**:
  - `tests/fakes/broker.py`: an in-memory broker implementing the port. It holds scripted account
    values, positions, quotes, and trades; records every submission; fills orders when the test
    says so; and can raise on any call to simulate an outage or a timeout.
  - Crash injection (SC-002): the service's steps are separate functions, and the tests raise
    inside each step in turn, then re-run the tick against the same fake broker and database.
  - A test-suite-wide guard: an autouse fixture patches socket connection so any attempt to reach a
    host other than the local Postgres fails the test (SC-008). The real adapter is covered only by
    tests that construct it with fake keys and check the guard refuses before any call.
  - Hypothesis (≥10,000 examples) over live account states for SC-004: no submitted buy leaves the
    position above the ceiling or cash below the reserve at the submitted price, counting open
    orders.
- **Rationale**: FR-019 and CLAUDE.md: no order, not even on the paper account, without the owner
  asking for that specific action.

## E15. Dependencies and credentials

- **New dependency**: `alpaca-py` 0.44 (Alpaca's official SDK; Python ≥ 3.10), pinned `>=0.44,<0.45`
  since it's pre-1.0. It brings `pydantic`, `requests`, `msgpack` and `websockets`. Imported only
  by `execution/alpaca.py`.
- **Environment variables** (added to `.env.example`, names only):
  - `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`: the paper account's keys. Execution only.
  - `ALPACA_BASE_URL`: optional; if set, must equal the paper address or startup stops (E2).
  - `EXECUTION_DATABASE_URL`: a login in `ta_execution`.
  - `RISK_GATE_DATABASE_URL`: a login in `ta_risk_gate`, used by the **gate's** trigger runner
    (E13), never given to Execution's process.
- **Open, and out of scope here**: the Portfolio Manager and Opportunistic Identifier need a price
  source that can't trade. They must not get these keys (Constitution I). Market data with the
  same Alpaca keys would put a trading-capable credential in an LLM agent's process.

## E16. Fixes after the adversarial review ([ADR 0014](../../docs/adr/0014-fresh-confirmed-stop-loss-triggers-and-intraday-equity.md))

The review of the implementation found no way to place two orders for one verdict, but found
these gaps, fixed as follows (owner decisions, 2026-09-28):

- **The clock at submission.** A tick decides "market open" at its start; slow broker calls can
  push a submission past the close, and Alpaca holds a day order submitted after hours for the
  next session, where it would go out without that day's checks. `_submit` re-reads the clock
  under the lock and submits only while the market is open and more than 30 seconds before the
  close; otherwise it retries, and the approval lapses at the close.
- **Unresolved placements.** A timed-out submission may be live but has no `orders` row, so the
  open-order sums miss it. While any placement is unresolved, buys retry, and exits retry for that
  symbol if the unresolved order is a sell. The lapsed-approval sweep leaves unresolved verdicts
  alone and doesn't expire today's approvals until two minutes after the close. A rejection for a
  verdict that ever timed out is treated as a duplicate of the live order: retried, never recorded.
  A restart forgets the set; the first lookup still runs before any submission, so the remaining
  risk is Alpaca accepting a duplicate client id while its lookup lags, unverified until a paper
  run (L6).
- **Symbols the identifier can't hold.** The identifier is validated before any broker call and
  refused as `invalid_symbol`, so no order is placed that couldn't be recorded.
- **Stop-loss confirmation (ADR 0014).** The monitor needs the last trade and a fresh bid both
  through the line; the gate rejects triggers older than 10 minutes.
- **Intraday equity (ADR 0014).** One account snapshot per stop-loss window. On a buy, the loss
  line is checked right after the pre-buy snapshot, before any other broker call.
- **Robustness.** Pending approvals are parsed one at a time inside isolation; sync and
  reconciliation share one transaction (no reader sees a half-applied state) with reconciliation
  isolated per symbol; a session-level advisory lock makes Execution single-instance; an order
  stuck on an unexpected broker status is reported every tick.
