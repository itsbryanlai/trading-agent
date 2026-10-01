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

- Every open report (`reports_with_status` where `status = 'open'`) from both
  Research and the Opportunistic Identifier, regardless of which agent
  produced it. A report's `rationale_md` is untrusted, model-written text that
  may quote the news: the PM treats it as data to weigh, never as
  instructions, and its prompt must keep it clearly separated from its own
  instructions (`specs/007-research-agent`, second-order prompt injection).
  Its `suggested_size_pct` is a target weight, like the PM's own `size_pct`.
- Its own fresh reads, fetched itself rather than trusted from a report:
  current portfolio state (`positions`, cash from the latest
  `account_snapshots` row), a live quote for each symbol
  under consideration, and recent `journal` entries for context on how things
  have been going.
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
- the report(s) this decision drew on, recorded as `decision_reports` rows in
  the same transaction, for per-agent attribution in the journal
- `reasoning_md` — including, when both analysts converged on a symbol, how
  it weighed that convergence (a positive signal, not a sizing formula —
  convergence never mechanically doubles size)

A symbol with no open report from either agent gets no decision — the PM does
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
- **A report it already decided on is still open in a later run**: re-evaluate
  it on current state like any other. Deciding the same target again is
  harmless, and the Risk Gate turns a met target into no order.

## Interfaces

- Reads `reports` (all), `positions`, `account_snapshots`, `journal`. Writes
  only to `decisions` and `decision_reports`.
- Never writes `risk_verdicts` or `orders` — a decision is a proposal until
  the Risk Gate and Execution act on it.

## Non-goals

- Does not re-derive whether a trade is *allowed* (position limits, cash
  reserve, daily order cap, daily-loss halt) — that is entirely the Risk
  Gate's responsibility, kept out of the PM's reasoning on purpose.
- Does not place, modify, or cancel orders.
