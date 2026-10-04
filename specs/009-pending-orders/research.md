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

## I3. The context gains three numbers

**Decision**: `risk.model.Context` gains, all `Decimal`, default 0:
- `in_flight_buy_qty`: the sum of `unsettled_qty` of today's in-flight buys of this symbol;
- `in_flight_sell_qty`: the same for sells;
- `in_flight_buy_cost`: the sum of `unsettled_qty × limit_price` of today's in-flight buys of **every** symbol (FR-001a).

`risk.service._load_context` reads them from the view in the same transaction and under the same advisory lock as every other input, so an evaluation sees one consistent moment, and no two evaluations interleave (002 research G10). The pure core stays a function of its inputs (FR-006).

## I4. Buy sizing

**Decision**: in `risk.gate._buy`, with `settled = shares_held + in_flight_buy_qty − in_flight_sell_qty` (floored at 0):
- **Direction check and wanted quantity** use `settled` in place of `shares_held`: `wanted = floor((target × equity − settled × quote) / ceiling)`; below 1 is `target_already_met` (FR-002).
- **Position room** uses `settled`: `floor(max_position_pct × equity / ceiling) − settled`.
- **Cash room** subtracts the in-flight cost: `floor((cash − in_flight_buy_cost − cash_reserve_pct × equity) / ceiling)` (FR-001a). Each in-flight buy is valued at its own ceiling, the most it can cost, as Execution's check does.

The precedence of rules is unchanged; only their inputs change.

## I5. Sell sizing

**Decision**: in `risk.gate._sell`:
- **`no_position`** still uses the physical `shares_held`. A symbol with only an in-flight buy has nothing to sell yet (spec US2-2).
- **Available to sell**: `available = shares_held − in_flight_sell_qty`. If below 1: `target_already_met`, because every held share is already being sold (spec US1-3).
- **Target 0 (full exit)**: sell `available`.
- **Partial target**: with `settled = shares_held + in_flight_buy_qty − in_flight_sell_qty`, `keep = ceil(target × equity / quote)`, `qty = min(settled − keep, available)`. Below 1: `target_already_met`. The direction check (`direction_contradicts_target`) uses `settled`.

**Why `min(…, available)`**: an in-flight buy raises `settled`, but its shares can't be sold before they arrive. Execution would refuse a sell above `held − open sells` anyway (`execution/checks.py` row for exits). Example: 50 held, 24 in flight to buy, target 0: sell 50 now. Once the buy fills, the PM's next run sees 24 held and decides again. That is accepted, and recorded in the ADR.

**Never more than today**: the sell quantity is never above what today's rule gives, because `available ≤ shares_held`. So this can only shrink a sell, never enlarge one (SC-002).

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
  - a property: an approved buy plus `in_flight_buy_cost` keeps cash at or above the reserve at the ceilings (SC-002a);
  - a property: re-evaluating any decision with its own approval added as in flight gives `target_already_met` (SC-001);
  - stop-loss verdicts are identical with arbitrary in-flight values (SC-004).
- **Integration** (`tests/integration/`):
  - migration 0013: each in-flight state appears in the view (no outcome yet; submitted; partially filled with the right remainder), and each ended state doesn't (filled, rejected, canceled, expired, refused);
  - `ta_risk_gate` can select from the view, still can't select `orders` or `execution_refusals`, and gained no write; the grants matrix records the one new grant;
  - `evaluate_decision` end to end: two decisions with the same target, the second rejected `target_already_met` while the first's order is open.
- **Mutation check** every new test.
