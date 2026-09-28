# 0012. Order identifiers are per verdict, not per symbol, side and day

Status: accepted

## Context

Feature 001 made `orders.id` the deterministic string `{trading_day}-{symbol}-{side}`
(`specs/001-data-model/research.md` R11). Execution sends the same string to the broker as the
order's client identifier. A process that restarts after a crash re-derives it, finds the order it
already placed, and can't place a second one: the primary key and the broker both reject the
duplicate.

That format allows one order per symbol, per side, per day. R11 already flagged one clash and left
it to the Execution feature: a stop-loss exit and a Portfolio Manager sell of the same symbol on the
same day. [0011](0011-event-driven-portfolio-manager-runs.md) added another. The Portfolio Manager
now runs several times a day, so two buys of the same symbol on one day are possible (a first buy
trimmed by a limit, then topped up on a later run). In both cases the second order is legitimate,
approved by the Risk Gate, and would be refused as a duplicate. Refusing the stop-loss exit is the
dangerous one: it leaves a losing position open.

## Decision

An order's identifier is `{trading_day}-{symbol}-{side}-{v}`, where `{v}` is the first 8 hexadecimal
characters of the approving verdict's id (e.g. `2026-09-28-AAPL-sell-3f9c2a1b`). It is still the
order's primary key and still sent to the broker as the client order identifier.

Each approved verdict produces at most one order (`orders.risk_verdict_id` is already unique), and
the verdict row is durable, so a restarted process re-derives exactly the same identifier from the
same verdict. Two different verdicts only share an identifier if they share the trading day, symbol,
side, and the first 32 bits of their ids, which is negligible at a handful of orders a day. If it
ever happens, Execution refuses the second approval rather than mistaking it for the first
(`specs/003-execution` FR-008).

Decided in `specs/003-execution` Clarifications (2026-09-27). Supersedes the format in
`specs/001-data-model/research.md` R11; R11's rationale (readable, deterministic, two independent
duplicate guards) still holds.

## Alternatives considered

- **The full verdict id alone.** Unique by construction, but unreadable in the broker's order list
  and in logs, where the readable prefix is what the owner scans.
- **A per-day counter** (`…-sell-1`, `…-sell-2`). Readable, but a restarted process has to rebuild
  the counter from the database before it can re-derive an identifier, which is its own crash
  window.
- **Keep the old format and refuse the second order.** Rejected: it silently blocks a stop-loss
  exit on any day the Portfolio Manager already sold the same symbol.

## Consequences

- A forward-only migration (feature 003) rewrites the comment and adds a `CHECK` on the new format.
  `orders` has no rows yet in any environment, so no data is converted.
- The grants contract's forbidden-operation example "second order for the same
  `{day}-{symbol}-{side}`" becomes "second order for the same verdict"; the per-verdict uniqueness
  was already enforced by `orders.risk_verdict_id`.
- The at-most-one-order-per-symbol-per-side-per-day property is gone. Nothing relied on it for
  safety: the Risk Gate's daily order cap and target-weight sizing bound churn, not the identifier.
