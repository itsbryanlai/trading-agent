# 0005. Risk Gate and Execution are deterministic, not LLM agents

Status: accepted

## Context

With no per-trade human approval (0006), the Risk Gate is the only thing
standing between a PM decision and a real order. If the gate itself were a
model call, the one component meant to be a hard, predictable check would
inherit the model's own variability. Separately, once the PM decides direction
and size, someone has to actually place the order — the question is whether
that step needs judgment of its own.

## Decision

- **Risk Gate**: a pure function. No network call, no model call, no
  environment reads beyond what's passed to it (whether the market is open
  arrives as a boolean from the caller). It validates a PM decision against
  `config/risk.yaml` (position size, cash reserve, stop-loss, portfolio-wide
  daily order cap, the daily-loss halt, universe/eligibility filters) and
  returns approve-with-fully-specified-order or reject-with-reason. Nothing
  else in the system may construct an order.
- **Execution**: deterministic and rule-based. It accepts only a Risk
  Gate–approved verdict, re-derives the position and cash ceilings itself
  rather than trusting the verdict it was handed, and submits a limit order at
  the current quote with a day time-in-force. It is the only component holding
  broker credentials.

## Alternatives considered

- Execution as its own LLM agent with judgment over order mechanics (timing,
  working a large order in slices, limit price aggressiveness). Rejected: at
  this trading pace (day trading, explicitly not HFT) and with position sizes
  capped at 8% of equity by the Risk Gate, there is no order-working problem a
  fixed rule doesn't already solve. An LLM agent here would add a second path
  by which model output reaches the broker, for no realized benefit.
- Risk limits as code constants (as in `trading-bot`). Rejected for this
  project specifically at the user's request — see 0005a below.

### 0005a. Risk config is a file, not code

The limits the Risk Gate enforces live in `config/risk.yaml` (or equivalent),
not as constants compiled into the service. This trades `trading-bot`'s
"a limit that can be changed from a dashboard is not a limit" argument for
easier iteration — accepted deliberately, on the condition that changes to
this file go through the same review process as any other change (see
`docs/policy/agent-management.md`), so editability doesn't become a way to
quietly loosen risk controls.

## Consequences

- The Risk Gate and Execution are the two components in the system it should
  be possible to reason about completely without an LLM's non-determinism —
  test them with fixed inputs and expect fixed outputs.
- Loosening a limit is a one-line diff to a config file and is exactly as
  visible in review as any other change — it is not hidden inside a prompt or
  a model's judgment.
