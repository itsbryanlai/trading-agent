# Research: The Risk Gate counts orders still in flight

Decisions behind [plan.md](plan.md), numbered I1–I9 so tasks, code comments and reviews can cite them.

## I1. A narrow view, not table grants

**Decision**: migration 0013 creates the view `in_flight_orders`, owned by the migration admin and run with its owner's rights (like `reference_candidate_symbols`, migration 0009). `ta_risk_gate` gets `SELECT` on the view only. No grant on `orders` or `execution_refusals`.

The view has one row per in-flight approval, with exactly these columns:

| Column | Meaning |
|---|---|
| `trading_day` | the approval's trading day (`risk_verdicts.trading_day`) |
| `symbol` | `approved_order->>'symbol'` |
| `side` | `buy` or `sell` |
| `unsettled_qty` | the approved quantity less what has filled, above 0 |
| `limit_price` | a buy's price ceiling from the approval; null for a sell |

An approval is **in flight** when it is `approved` and either:
- it has no `orders` row and no `execution_refusals` row (Execution hasn't acted on it yet), or
- its `orders` row's status is `submitted` or `partially_filled` (the same two statuses Execution's own `_OPEN_STATUSES` treats as open).

`unsettled_qty = (approved_order->>'qty')::numeric − coalesce(orders.fill_qty, 0)`. Rows where this isn't above 0 are left out.

**Why**:
- **Least privilege** (Constitution III, FR-007): the gate learns only quantities and ceilings it already wrote itself, plus one number Execution wrote (`fill_qty`). It never sees broker order ids, fill prices, refusal reasons or details.
- **One definition of "in flight"**, in SQL, read the same way by tests.
- **No change to Execution's grants or code** (FR-009).

**Alternatives**:
- `SELECT` on `orders` and `execution_refusals` for `ta_risk_gate`: broader than the gate needs, and the definition of "in flight" would live in gate code. Not chosen.
- Execution writes a pending-quantity table: a new write for Execution and a second source of truth. Not chosen.

## I2. Which approvals count

**Decision**: every in-flight approval from **today's trading day**, from decisions and stop-loss triggers alike (FR-001), filtered by the gate's caller on `trading_day = calendar.trading_day(now)`. An earlier day's approval is never in flight: approvals are valid only on their own day, and day orders end at the close (spec Edge Cases).

## I3. The context gains four numbers

**Decision**: `risk.model.Context` gains, all `Decimal`, default 0:
- `in_flight_buy_qty`: the sum of `unsettled_qty` of today's in-flight buys of this symbol;
- `in_flight_buy_cost_symbol`: the sum of `unsettled_qty × limit_price` of those same buys;
- `in_flight_sell_qty`: the sum of `unsettled_qty` of today's in-flight sells of this symbol;
- `in_flight_buy_cost_all`: the sum of `unsettled_qty × limit_price` of today's in-flight buys of **every** symbol (FR-001a).

`risk.service._load_context` reads them from the view in the same transaction and under the same advisory lock as every other input, so an evaluation sees one consistent moment, and no two evaluations interleave (002 research G10). The pure core stays a function of its inputs (FR-006).

## I4. Buy sizing

**Decision** (amended after `/speckit-analyze` F1, F2; owner, 2026-10-05): in `risk.gate._buy`:
- **Settled value**: `settled_value = shares_held × quote + in_flight_buy_cost_symbol − in_flight_sell_qty × quote`, floored at 0. In-flight buys are valued at their own price ceiling, the price they were sized at, so the same target re-decided leaves nothing to buy (analyze F2).
- **Direction check**: `target × equity < settled_value − quote` is `direction_contradicts_target`, as today with `held × quote` replaced.
- **Wanted**: `floor((target × equity − settled_value) / ceiling)`; below 1 is `target_already_met` (FR-002).
- **Position room** counts in-flight buys and **never subtracts in-flight sells**: `floor(max_position_pct × equity / ceiling) − shares_held − in_flight_buy_qty`. An unfilled sell might never fill, so it can't make room. This is exactly how Execution's position check counts (analyze F1; FR-005).
- **Cash room** subtracts every in-flight buy's cost: `floor((cash − in_flight_buy_cost_all − cash_reserve_pct × equity) / ceiling)` (FR-001a). This is at least as strict as Execution's check, which values open buys at their submitted price (at or below the ceiling) and doesn't yet count approvals it hasn't placed (analyze F5).

With all four in-flight values at 0, every formula is exactly today's. The precedence of rules is unchanged; only their inputs change.

**Accepted, as today**: once an order fills, its shares are valued at the quote while it was sized at the ceiling, so a re-decided target can buy a few more shares once, then converges. That's the gate's existing behaviour with nothing in flight, deliberately left alone (SC-003).

## I5. Sell sizing

**Decision**: in `risk.gate._sell`:
- **`no_position`** still uses the physical `shares_held`. A symbol with only an in-flight buy has nothing to sell yet (spec US2-2).
- **Available to sell**: `available = shares_held − in_flight_sell_qty`. If below 1: `target_already_met`, because every held share is already being sold (spec US1-3). This includes `in_flight_sell_qty > shares_held` (analyze F3).
- **Target 0 (full exit)**: sell `available`.
- **Partial target**: with `settled = shares_held + in_flight_buy_qty − in_flight_sell_qty` (floored at 0), `keep = ceil(target × equity / quote)`, `qty = min(settled − keep, available)`. Below 1: `target_already_met`. The direction check (`direction_contradicts_target`) uses `settled`.

**Why `min(…, available)`**: an in-flight buy raises `settled`, but its shares can't be sold before they arrive. Execution would refuse a sell above `held − open sells` anyway (`execution/checks.py` row for exits). Example: 50 held, 24 in flight to buy, target 0: sell 50 now. Once the buy fills, the PM's next run sees 24 held and decides again. That is accepted, and recorded in the ADR.

**Never more than today**: the sell quantity is never above what today's rule gives, because `available ≤ shares_held`. So this can only shrink a sell, never enlarge one (SC-002).

## I5a. A fill seen twice for a moment

Execution updates `orders.fill_qty` and `positions` from its broker calls; for a moment, `positions` can show a fill that `orders.fill_qty` doesn't yet (analyze F3). The gate then counts those shares twice:
- **a buy fill**: settled holdings look higher, so a buy is sized smaller. Stricter.
- **a sell fill**: held looks lower while the sell still counts in flight, so `available` and settled holdings look lower. A PM sell can be shrunk or rejected as `target_already_met`, and a buy's wanted quantity can look larger. The position room never subtracts sells (I4), so the ceiling holds; the cash and position limits are also re-checked live by Execution.

Accepted: it lasts until Execution's next tick (about a minute) and the PM's next run decides again. Stop-loss exits don't read any in-flight number (I6).

## I6. Stop-loss exits are untouched

**Decision**: `_stop_loss` keeps using `shares_held` alone. No in-flight number reaches it (FR-004, SC-004). The daily-loss line, the halt, the pause, the order cap and the universe checks read no in-flight number (FR-005).

## I7. The order cap

**Decision**: unchanged. It already counts approved exposure-increasing verdicts today, including in-flight ones. A re-decided target that's already met is now `target_already_met`, so it no longer takes a slot (FR-002).

## I8. A failed read

**Decision**: the view is read like every other input in `_load_context`. A database error propagates exactly as today: the evaluation writes nothing, and the runner logs and isolates it, or exits on a lost connection (spec Edge Cases). There's no fallback to "nothing in flight".

## I9. Tests

- **Unit, pure core** (`tests/unit/risk/`):
  - the spec's acceptance scenarios with exact quantities: US1-1 to US1-3, US2-1 to US2-3, US3-1 to US3-4;
  - a property: with nothing in flight, every verdict equals today's (SC-003), checked by evaluating the same random inputs with the in-flight fields at 0;
  - a property: an approved sell never exceeds `shares_held − in_flight_sell_qty` (SC-002);
  - a property: an approved buy plus `in_flight_buy_cost_all` keeps cash at or above the reserve at the ceilings, and `shares_held + in_flight_buy_qty` plus the buy stays within `max_position_pct` at the ceiling, whatever is in flight to sell (SC-002a, FR-005);
  - a property: re-evaluating any decision with its own approval added as in flight is not approved (SC-001; a trimmed approval may come back as a limit rejection rather than `target_already_met`, analyze F4);
  - a case: an in-flight sell larger than `shares_held` (I5a) gives `target_already_met` for a sell and no looser position room for a buy;
  - stop-loss verdicts are identical with arbitrary in-flight values (SC-004).
- **Integration** (`tests/integration/`):
  - migration 0013: each in-flight state appears in the view (no outcome yet; submitted; partially filled with the right remainder), and each ended state doesn't (filled, rejected, canceled, expired, refused);
  - `ta_risk_gate` can select from the view, still can't select `orders` or `execution_refusals`, and gained no write; the grants matrix records the one new grant;
  - `evaluate_decision` end to end: two decisions with the same target, the second rejected `target_already_met` while the first's order is open.
- **Mutation check** every new test.
