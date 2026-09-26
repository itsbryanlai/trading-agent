# Data model

The shared knowledge base ([ADR 0004](../adr/0004-shared-postgres-role-scoped-credentials.md)).
One Postgres instance; every table below lists which database role may write
to it. Read access is broad by default — assume every role can read every
table unless a spec says otherwise (the Assistant reads all of them and
writes none).

## `reports`

One row per analyst-agent run, including runs that found nothing — a silent
agent is itself a data point (`docs/specs/research-agent.md` and
`docs/specs/opportunistic-identifier-agent.md` share this shape).

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `agent` | enum (`research`, `opportunistic_identifier`) | |
| `generated_at` | timestamptz | |
| `symbol` | text | nullable — a `no_action` run may not name one |
| `direction` | enum (`buy`, `sell`, `hold`, `no_action`) | |
| `conviction` | int 1–5 | rough strength, not a probability |
| `suggested_size_pct` | numeric | the agent's own guess; PM is not bound by it |
| `sources` | jsonb array of `{title, url, publisher, published_at}` | structured citations, not just prose links |
| `rationale_md` | text (Markdown) | the narrative; rendered as-is in the UI activity log |
| `expires_at` | timestamptz | end of `generated_at`'s trading day |
| `status` | enum (`open`, `expired`, `consumed`, `rejected`) | lifecycle |

Writers: `research` role writes rows where `agent = 'research'`;
`opportunistic_identifier` role writes rows where
`agent = 'opportunistic_identifier'`. Neither role may write the other's rows.

## `decisions`

One row per Portfolio Manager decision.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `generated_at` | timestamptz | |
| `symbol` | text | |
| `direction` | enum (`buy`, `sell`, `hold`) | |
| `size_pct` | numeric | PM's final sizing decision |
| `report_ids` | uuid[] | the report(s) this decision drew on — enables per-agent attribution |
| `reasoning_md` | text (Markdown) | PM's own rationale, including how it weighed converging/conflicting reports |
| `quote_at_decision` | numeric | the live quote the PM fetched itself, not trusted from a report |

Writers: `portfolio_manager` role only.

## `risk_verdicts`

One row per Risk Gate evaluation of a `decisions` row.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `decision_id` | uuid | FK to `decisions` |
| `evaluated_at` | timestamptz | |
| `verdict` | enum (`approved`, `rejected`) | |
| `rejection_rule` | text | which `config/risk.yaml` rule fired, if rejected |
| `approved_order` | jsonb | fully-specified order (symbol, side, qty, limit price, TIF) if approved |

Writers: `risk_gate` role only. The Risk Gate itself holds no broker or
market-data credentials — it is a pure function over its inputs.

## `orders`

One row per order Execution actually submits to the broker.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | deterministic: derived from trading day + symbol + side, so a restart after a crash can't double-submit |
| `risk_verdict_id` | uuid | FK to `risk_verdicts` |
| `submitted_at` | timestamptz | |
| `broker_order_id` | text | |
| `status` | enum (`submitted`, `filled`, `partially_filled`, `rejected`, `canceled`) | kept in sync from broker polling/webhook |
| `fill_price`, `fill_qty` | numeric | once known |

Writers: `execution` role only — the only role with this grant, matching the
only role with broker credentials.

## `positions`

Current holdings, kept in sync from confirmed fills. Read by the PM (to
re-derive its own view of exposure), the Risk Gate (cash/position ceilings),
the Assistant, and the UI.

Writers: `execution` role only, updated from confirmed order fills.

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

Small key-value table for the one manual control and the daily-loss breaker.

| Key | Type | Notes |
|---|---|---|
| `trading_paused` | boolean | the UI's manual pause/resume toggle; the orchestrator checks this before running the PM |
| `daily_loss_halt_active` | boolean | set by the Risk Gate when the 20% daily-loss line is crossed; cleared automatically at the start of the next trading day |
| `daily_starting_equity` | numeric | reset each trading day; the daily-loss breaker's baseline |

Writers: `risk_gate` role for the halt fields; a UI-facing role for
`trading_paused`.
