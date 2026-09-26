# 0006. Fully autonomous operation; a daily-loss breaker is the sole automatic hard stop

Status: accepted

## Context

The explicit goal is to observe how the agents perform over time with minimal
day-to-day intervention — a per-trade approval step would defeat that, but
fully unattended trading needs at least one automatic backstop for a genuinely
bad day, since no human is checking each decision before it executes.

## Decision

No PM decision requires human approval before reaching the Risk Gate. The one
automatic hard stop is a **daily-loss breaker**: if account equity falls 20%
below the day's starting equity, new order submission halts for the remainder
of that trading day. Existing open positions keep their own per-position
stop-loss active — the breaker does not force-liquidate. It clears
automatically at the start of the next trading day; no manual step is
required to lift it.

## Alternatives considered

- Force-liquidate all positions on breach. Rejected: turns one bad day into a
  guaranteed realized loss on every open position, including ones whose own
  stop-loss hasn't been hit; the per-position stop-loss is a better-informed
  exit than a blanket panic-sell.
- Require the owner to manually lift the halt (as `trading-bot`'s drawdown
  breaker does, off a high-water mark). Rejected here: that pattern fits a
  system with a human reviewing it regularly; this system's stated goal is
  the opposite. An auto-clearing daily halt still surfaces loudly (dashboard +
  Assistant), it just doesn't block the next session on a human action.

## Consequences

- A losing day is contained to roughly 20% of that day's starting equity from
  new orders, plus whatever a position's own stop-loss allows on top of that.
- Because there's no manual-lift step, a repeat trigger on consecutive days is
  possible and would only show up as a pattern in the journal/UI — worth
  watching for, not itself an argument for adding a manual gate back in.
