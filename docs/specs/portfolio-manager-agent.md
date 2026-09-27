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
  produced it.
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
  analysts' suggestions
- the report(s) this decision drew on, recorded as `decision_reports` rows in
  the same transaction, for per-agent attribution in the journal
- `reasoning_md` — including, when both analysts converged on a symbol, how
  it weighed that convergence (a positive signal, not a sizing formula —
  convergence never mechanically doubles size)

A symbol with no open report from either agent gets no decision — the PM does
not originate ideas of its own.

## Cadence

Once per trading day, on a fixed schedule (mid-morning, after both agents'
same-day reports exist). Event-triggered PM runs are explicitly out of scope
for now (see Alternatives in
[ADR 0003](../adr/0003-orchestrator-is-a-scheduler-not-an-authority.md)'s
neighboring discussion) — revisit only if the daily cadence is observed to
miss genuinely time-sensitive opportunities.

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
- **No open reports from either agent**: the PM's run produces no decisions
  that day. This is a normal outcome, not an error.

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
