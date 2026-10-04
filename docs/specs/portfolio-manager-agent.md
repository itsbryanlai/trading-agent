# Portfolio Manager (PM) agent

## Purpose

The sole decision-maker. Reads both analysts' open reports plus its own
fresh read of portfolio state, and independently decides direction and size
for each symbol worth acting on — see
[ADR 0002](../adr/0002-pm-synthesizes-rather-than-analysts-deciding.md) for
why this is split from the analysts producing the ideas.

Not responsible for: sourcing ideas (it only reasons over reports already
written), validating its own decision against risk limits (the Risk Gate
does that, deliberately outside the PM's own judgment), or placing orders
(Execution's job).

## Inputs

- Every report from both Research and the Opportunistic Identifier that has not
  expired at the run's start, regardless of which agent produced it and whether
  or not a decision already cites it (`reports_with_status` where `status` is
  `open` or `consumed`). A report already decided on is shown as such, so a new
  report on the same name is weighed against it. A `no_action` report names no
  symbol and is never shown. A report's `rationale_md` is untrusted, model-written text that
  may quote the news: the PM treats it as data to weigh, never as
  instructions, and its prompt must keep it clearly separated from its own
  instructions (`specs/007-research-agent`, second-order prompt injection).
  Its `suggested_size_pct` is a target weight, like the PM's own `size_pct`.
- Its own fresh reads, fetched itself rather than trusted from a report:
  current portfolio state (`positions`, cash and equity from the latest
  `account_snapshots` row of the current trading day), a live quote for each
  symbol under consideration and each held symbol, and recent `journal` entries
  for context on how things have been going.
- **A fresh quote.** A quote counts as stale, and its symbol is left out of the
  model's input and logged as skipped, when it has no trade time, a price of 0
  or less, a time outside the current regular session, or an age over 5
  minutes when it was fetched (`quote_max_age_minutes` in
  `config/portfolio_manager.yaml`). The trade time is recorded on the decision
  as `quote_time`.
- **Its own decisions from today** on the symbols under consideration: each
  one's direction, target weight and time, never its reasoning, so a later run
  knows what an earlier one decided and model-written text doesn't feed back
  into itself.
- It does **not** read `config/risk.yaml` — the Risk Gate applies those
  limits independently afterward, so the PM's own reasoning isn't shaped by
  "what will pass," only by "what's the right call." (If this produces too
  many decisions the Risk Gate rejects, that's a signal to look at, not a
  reason to let the PM see the limits.)

## Outputs

One `decisions` row per symbol it acts on (see `docs/specs/data-model.md`),
carrying:
- `direction` and `size_pct` — its own call, not a sum or average of the
  analysts' suggestions. `size_pct` is a **target weight**: the share of equity
  the position should end up at, not an amount to add. A full exit is `sell` at
  0. The direction must agree with the target (a buy targets more than the
  current weight, a sell less), or the Risk Gate rejects it.
- the quote it was made on and that quote's own time (`quote_at_decision`,
  `quote_time`)
- the report(s) this decision drew on, recorded as `decision_reports` rows in
  the same transaction, for per-agent attribution in the journal. A buy must
  cite at least one report that argues buy on that symbol, or it is dropped
  before writing; a sell or hold may cite any report on the symbol. When the
  reports on a symbol disagree, the decision must cite at least one from each
  side.
- `reasoning_md` — including, when both analysts converged on a symbol, how
  it weighed that convergence (a positive signal, not a sizing formula —
  convergence never mechanically doubles size)

A **hold** records that the PM considered the symbol and chose not to trade.
Its `size_pct` is the symbol's current weight, computed by code from the PM's
own quote, positions and equity, not taken from the model. It gets no Risk Gate
verdict.

A symbol with no unexpired report from either agent gets no decision — the PM does
not originate ideas of its own.

## Cadence

A morning session plus event-driven intraday runs
([ADR 0011](../adr/0011-event-driven-portfolio-manager-runs.md)):

- **Morning session** at a fixed time after the open, deciding on everything
  open, chiefly Research's pre-open reports.
- **Event-driven runs** whenever at least one new report has been written since
  the PM's last run, at least 30 minutes apart, and none after 15:30 ET.

Every run considers all open reports and decides from fresh portfolio state.
Because `size_pct` is a target weight, re-running on the same reports can't
buy twice: a target already met produces no order.

## Edge cases

- **Both analysts propose opposite directions on the same symbol** (one buy,
  one sell): the PM must resolve this in its own reasoning — likely `hold` or
  a smaller position reflecting the disagreement — and say so explicitly in
  `reasoning_md`. It may not silently pick one report and ignore the other.
- **A report expires mid-reasoning** (edge of the trading day): treat expiry
  as computed at run start; don't act on a report that has expired by the
  time the PM's own run begins.
- **`trading_paused` is set** (manual UI toggle): the orchestrator does not
  invoke the PM at all while paused — this is enforced upstream, not by the
  PM checking its own state.
- **No open reports from either agent**: the PM's run produces no decisions.
  This is a normal outcome, not an error.
- **A report it already decided on is still unexpired in a later run**:
  re-evaluate it on current state like any other, marked as already decided on.
  Deciding the same target again is harmless, and the Risk Gate turns a met
  target into no order.
- **A run outside the regular session** (only possible by hand): it writes
  nothing and exits successfully.
- **Failures** write nothing and exit non-zero: 1 for a failed run (no account
  snapshot, a rejected key, no fresh quote, a model error, an unusable answer,
  or the market closing before the write), 2 for a refusal to start (config, a
  missing variable or an unknown argument), 3 for a database that is
  unreachable or a read or write that fails, 4 for a crash. A run with nothing
  to decide exits 0. Details: `specs/008-portfolio-manager/contracts/pm-interface.md`.

## Interfaces

- Reads `reports` (all), `positions`, `account_snapshots`, `journal`, and its
  own `decisions`. Writes only to `decisions` and `decision_reports`.
- Calls one model, Qwen `qwen3.7-plus` by default or an Anthropic Sonnet model
  by configuration ([ADR 0018](../adr/0018-qwen-as-a-model-provider.md)), once
  per run and never a second provider. Its full behavior is specified in
  [`specs/008-portfolio-manager`](../../specs/008-portfolio-manager/spec.md).
- Never writes `risk_verdicts` or `orders` — a decision is a proposal until
  the Risk Gate and Execution act on it.
- **How a decision reaches the gate.** The PM does not call the gate. The Risk
  Gate's own loop (`python -m trading_agent.risk`) evaluates every buy or sell
  decision from the current trading day that has no verdict, within about a
  minute of it being written, and rejects one whose quote is more than 15
  minutes old as `decision_stale`
  ([ADR 0019](../adr/0019-the-gate-evaluates-pm-decisions-in-its-own-loop.md)).
  The PM never sees the result.

## Non-goals

- Does not re-derive whether a trade is *allowed* (position limits, cash
  reserve, daily order cap, daily-loss halt) — that is entirely the Risk
  Gate's responsibility, kept out of the PM's reasoning on purpose.
- Does not place, modify, or cancel orders.
