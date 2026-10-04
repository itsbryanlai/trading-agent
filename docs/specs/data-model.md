# Data model

The shared knowledge base ([ADR 0004](../adr/0004-shared-postgres-role-scoped-credentials.md)).
One Postgres instance; every table below lists which database role may write
to it. Read access is broad by default, narrowed wherever a component's own
spec disclaims access (e.g. Research never reads `decisions`; the Risk Gate
and Execution never read `journal`). The Assistant reads everything and writes
nothing.

Concrete schema, constraints, and the exact per-role grants matrix live in
[`specs/001-data-model/`](../../specs/001-data-model/data-model.md) (see its
`contracts/role-grants.md`). This page stays the behavior-level summary.

## `reports`

One row per symbol an analyst argues, or a single `no_action` row for a run that
argued nothing or failed — a silent agent is itself a data point (`docs/specs/research-agent.md` and
`docs/specs/opportunistic-identifier-agent.md` share this shape).

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `agent` | enum (`research`, `opportunistic_identifier`) | |
| `generated_at` | timestamptz | |
| `symbol` | text | nullable — a `no_action` run may not name one |
| `direction` | enum (`buy`, `sell`, `hold`, `no_action`) | |
| `conviction` | int 1–5 | rough strength, not a probability |
| `suggested_size_pct` | numeric | the agent's own guess at a **target weight** (the share of equity the position should end up at), the same meaning as the PM's `size_pct`; the PM is not bound by it. Buy or hold: above 0, at most 100. Sell: 0–100, where 0 means a full exit. Null only for `no_action` (migration 0011, `specs/007-research-agent`) |
| `sources` | jsonb array of `{title, url, publisher, published_at, relevance}` | structured citations, not just prose links. `relevance` (Research, `specs/007-research-agent`): `primary` when the article names the company, `secondary` when it's only tagged with the symbol or from its feed — weigh secondary-only evidence accordingly |
| `rationale_md` | text (Markdown) | the narrative. Model-written, untrusted text: readers treat it as data, never as instructions, and the UI renders it escaped, never as raw HTML (`specs/007-research-agent`) |
| `expires_at` | timestamptz | end of `generated_at`'s trading day |

Status is **not stored**. `open` / `expired` / `consumed` is computed at read
time: expired once `expires_at` passes, consumed once any decision cites the
report, otherwise open (view `reports_with_status`). There is no `rejected`
state for a report — the Risk Gate rejects decisions, not reports. Reports are
insert-only; nobody updates them after they're written.

Writers: `research` role writes rows where `agent = 'research'`;
`opportunistic_identifier` role writes rows where
`agent = 'opportunistic_identifier'`. Neither role may write the other's rows
(row-level security).

## `decisions`

One row per Portfolio Manager decision.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `generated_at` | timestamptz | |
| `symbol` | text | |
| `direction` | enum (`buy`, `sell`, `hold`) | |
| `size_pct` | numeric | PM's **target weight**: the share of equity the position should end up at (0 = exit fully). The Risk Gate orders the difference from the current weight (`specs/002-risk-gate`). |
| `reasoning_md` | text (Markdown) | PM's own rationale, including how it weighed converging/conflicting reports |
| `quote_at_decision` | numeric | the live quote the PM fetched itself, not trusted from a report |
| `quote_time` | timestamptz | when that quote was traded. The Risk Gate rejects a decision whose quote is more than 15 minutes old when it evaluates it (`decision_stale`, [ADR 0019](../adr/0019-the-gate-evaluates-pm-decisions-in-its-own-loop.md)). |

The report(s) a decision drew on — what enables per-agent attribution — are
recorded in a separate `decision_reports (decision_id, report_id)` table
rather than an array column, so every link is foreign-key enforced and can
never point at a report that doesn't exist.

Writers: `portfolio_manager` role only, for both `decisions` and
`decision_reports`.

## `risk_verdicts`

One row per Risk Gate evaluation. The request is either a `decisions` row or
a `stop_loss_triggers` row, and exactly one of the two.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `decision_id` | uuid | FK to `decisions`, or NULL for a stop-loss exit |
| `stop_loss_trigger_id` | uuid | FK to `stop_loss_triggers`, or NULL for a decision |
| `evaluated_at` | timestamptz | |
| `trading_day` | date | the only day the approval is valid (Execution never submits it on a later day) |
| `verdict` | enum (`approved`, `rejected`) | |
| `rejection_rule` | text | the named rule that fired, if rejected (`specs/002-risk-gate/contracts/rejection-rules.md`). For a decision this can be `decision_stale`. |
| `approved_order` | jsonb | if approved: a limit buy with a price ceiling, or a market sell/exit, plus any trims |
| `config_version` | text | which version of `config/risk.yaml` it was judged against |

Writers: `risk_gate` role only. The Risk Gate itself holds no broker or
market-data credentials — it is a pure function over its inputs.

## `stop_loss_triggers`

One observation by Execution's 30-minute monitor that a held position is at
or below its stop-loss line: the symbol, the price seen, and when. There is no
entry price or line; the Risk Gate re-derives both itself, so a faulty monitor
can't force a sale. Writers: `execution` role only.

## `instrument_reference`

Per-symbol universe data for one trading day: security type, exchange, market
cap, average daily dollar volume, and share price. The Risk Gate's universe
check reads it; a symbol missing today's row fails that check. Writers: a
dedicated `reference_data` role for the daily reference-data job (ADR 0010).
The job inserts only, never updates: a day's row is never changed once written
(`specs/004-reference-data`, migration `0009`).

## `orchestrator_runs`

One row per agent run or skipped slot, written only by the orchestrator: the
agent, why it was due (scheduled, morning session, event-driven, catch-up), its
slot, when it started and finished, its process group, and its outcome
(succeeded, failed, timed out, interrupted, skipped). A row is written before
the agent starts, so each slot can be claimed only once a day. Never deleted.
Readers: the orchestrator, the Assistant and the dashboard
(`specs/005-orchestrator`, migration `0010`).

## `latest_report_time` (view)

A single value: the newest report's creation time. It is the only thing the
orchestrator may learn about reports (ADR 0011). Readers: the orchestrator, the
Assistant and the dashboard.

## `reference_candidate_symbols` (view)

The symbols the reference-data job fetches data for: every held position, and
every symbol named in a report or decision, with the latest time it was named
and (for reports) the latest expiry. Symbols and times only, no text, sizes or
quantities. It runs with its owner's rights, so the job reads it without any
access to `positions`, `reports` or `decisions`; the job applies the exact
"recently named" window itself (`specs/004-reference-data` research D10).
Readers: the `reference_data` role, the Assistant and the dashboard.

## `in_flight_orders` (view)

One row per approval from `risk_verdicts` that hasn't ended: Execution hasn't
acted on it yet (no order, no refusal), or its order is `submitted` or
`partially_filled`. Columns: `trading_day`, `symbol`, `side`, `unsettled_qty`
(approved quantity less `fill_qty`, above 0) and `limit_price` (a buy's price
ceiling; null for a sell). It runs with its owner's rights, so the Risk Gate
reads it without any access to `orders` or `execution_refusals`; broker ids,
fill prices and refusal reasons are not exposed. Readers: the Risk Gate, the
Assistant and the dashboard ([ADR 0020](../adr/0020-the-gate-counts-orders-in-flight.md)).

## `orders`

One row per order Execution actually submits to the broker.

| Column | Type | Notes |
|---|---|---|
| `id` | text | deterministic: `{trading_day}-{symbol}-{side}-{first 8 hex of the verdict id}` ([ADR 0012](../adr/0012-order-identifier-per-verdict.md)), also sent as the broker's `client_order_id`, so a restart after a crash finds the existing order instead of submitting another |
| `risk_verdict_id` | uuid | FK to an **approved** `risk_verdicts` row — the database rejects an order for a rejected verdict |
| `submitted_at` | timestamptz | |
| `broker_order_id` | text | |
| `status` | enum (`submitted`, `partially_filled`, `filled`, `rejected`, `canceled`, `expired`) | kept in sync by polling the broker; `expired` is a day order closed out at the end of the session |
| `limit_price` | numeric | the live ask a buy was submitted at (at or under the verdict's ceiling); none for sells |
| `broker_reason` | text | the broker's reason when it rejects an order |
| `fill_price`, `fill_qty` | numeric | cumulative, once known |

Writers: `execution` role only — the only role with this grant, matching the
only role with broker credentials.

## `execution_refusals`

One row per approved verdict Execution declined to submit, with a named reason
(`specs/003-execution/contracts/refusal-reasons.md`) and the live numbers it saw.
Every approved verdict ends with exactly one order or one refusal, never both.
Writers: `execution` role only. Readers: the journal, the Assistant, the UI.

## `positions`

Current holdings, kept in sync from confirmed fills. Read by the PM (to
re-derive its own view of exposure), the Risk Gate (cash/position ceilings),
the Assistant, and the UI.

Writers: `execution` role only, updated from confirmed order fills.

## `account_snapshots`

Broker account equity, cash, and buying power over time. Execution is the
only component holding the broker credential, so it records this; the PM
(cash), the Risk Gate (cash reserve, daily-loss line), and the journal
(`equity_open`/`equity_close`) read it instead of each calling the broker
themselves.

Writers: `execution` role only. Insert-only.

## `journal`

Daily narrative summary plus computed per-agent attribution — the record
that answers "how is each agent performing over time," separate from the
actual blended portfolio return.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `trading_day` | date | |
| `equity_open`, `equity_close` | numeric | |
| `summary_md` | text (Markdown) | |
| `per_agent_attribution` | jsonb | e.g. `{research: {...}, opportunistic_identifier: {...}}` — what the portfolio would look like sized purely off one agent's reports, for measurement only |

Writers: a `journal` role, written once per day after the trading session
closes. Never read by the Risk Gate or Execution — attribution is
measurement, not a feedback input, per
[ADR 0002](../adr/0002-pm-synthesizes-rather-than-analysts-deciding.md).

## `system_state`

A single control row for the one manual control and the daily-loss breaker.

| Field | Type | Notes |
|---|---|---|
| `trading_paused` | boolean | the UI's manual pause/resume toggle; the orchestrator checks this before running the PM, and can read this column only (`specs/005-orchestrator`) |
| `halt_triggered_on` | date | the trading day the Risk Gate saw the 20% daily-loss line crossed |
| `baseline_trading_day`, `daily_starting_equity` | date, numeric | the daily-loss breaker's baseline and the day it belongs to |

"Halt active" is computed, not stored: it's true only while
`halt_triggered_on` is today's trading date, so it clears itself at the start
of the next trading day with no write from anyone. Likewise a baseline from a
previous day reads as unset (view `system_state_effective`).

Writers, enforced per column: `risk_gate` role for the halt and baseline
fields; a dedicated dashboard-control role for `trading_paused` only.
