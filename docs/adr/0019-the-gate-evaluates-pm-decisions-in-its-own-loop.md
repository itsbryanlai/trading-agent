# 0019. The Risk Gate evaluates Portfolio Manager decisions in its own loop, and rejects decisions on stale quotes

Status: proposed

## Context

Every PM decision must pass the Risk Gate before it can become an order (Constitution I). Something has to hand each decision to the gate, and nothing does today:

- **The gate's contract** (`specs/002-risk-gate/contracts/gate-interface.md`) says `evaluate_decision` is called by "the PM session runner (orchestrator feature)".
- **[0013](0013-deterministic-services-run-their-own-loops.md) point 3** says "the Portfolio Manager's gate evaluations still happen in the PM runner's process, as before".
- **The orchestrator** was then specified without it: it may not read decisions or verdicts (`specs/005-orchestrator` FR-002), and it starts the PM as a child process that holds only `PORTFOLIO_MANAGER_*` variables ([0015](0015-orchestrator-starts-agents-with-their-own-credentials.md)).

So the "PM runner" exists only in those two sentences. Evaluating in the PM's own process would put the gate's database login and `config/risk.yaml` inside an LLM agent's process. That runs against Constitution III (credentials scoped per component) and the gate contract's rule that the PM never reads `config/risk.yaml`. Found while specifying the PM (`specs/008-portfolio-manager`, Clarifications 2026-10-04).

A second question follows from the hand-off. The gate sizes every order from the quote recorded on the decision ([0016](0016-market-data-for-the-llm-agents.md)). If a decision reaches the gate late, for example after the gate's process was down, that quote may be well out of date. A buy fails safe, because Execution skips a buy whose live ask is above the ceiling. A partial sell doesn't: it is a market order sized from the old quote, so a quote that is too high sells more than the target meant.

## Decision

1. **The gate's own loop evaluates decisions.** `python -m trading_agent.risk`, which already evaluates stop-loss triggers every 60 seconds with only the `ta_risk_gate` login (0013 point 2), also evaluates every buy or sell decision from the current trading day that has no verdict yet. Triggers first, then decisions. Holds produce no verdict, and decisions from an earlier day are never evaluated. This supersedes 0013 point 3; the rest of 0013 stands.
2. **No new grant.** `ta_risk_gate` already reads `decisions` (`specs/001-data-model` role grants).
3. **The PM records its quote's time.** Migration 0012 adds `decisions.quote_time`, the quote's own trade time, which 0016 §4 already required.
4. **A new gate rule, `decision_stale`.** The gate rejects a buy or sell decision whose quote is more than 15 minutes old when it evaluates it. It comes right after `market_closed`. Like `stop_loss_trigger_stale` ([0014](0014-fresh-confirmed-stop-loss-triggers-and-intraday-equity.md)), it is a code constant about whether an observation is still usable, not a limit in `config/risk.yaml`. Stop-loss triggers are unaffected.
5. **The PM's own freshness limit leaves room.** The PM's config loader refuses any combination of its quote-freshness limit, run time and the gate's pass interval that could reach 15 minutes, so a decision is never stale at the gate unless the gate's loop is late.

## Alternatives considered

- **The PM's process calls the gate after writing.** Rejected: the gate's login and its config file would sit in an LLM agent's process and environment (Constitution III; the gate contract's rule that the PM never reads `config/risk.yaml`).
- **The orchestrator calls the gate after a PM run.** Rejected: it would need the gate's login and read access to decisions, reversing `specs/005-orchestrator` FR-002 and making the scheduler part of the trade path ([0003](0003-orchestrator-is-a-scheduler-not-an-authority.md)).
- **A separate gate service for decisions.** Not chosen: a second process with the same login and the same idempotent call adds a service and buys nothing.
- **No staleness rule.** Not chosen: a late partial sell could sell more than intended. The rule costs one PM run's delay; the PM's next run decides again on a fresh quote.
- **The staleness limit in `config/risk.yaml`.** Not chosen: it isn't a risk limit the owner tunes, and the trigger's equivalent is a constant. It can move there later through a reviewed change.

## Consequences

- **A decision is evaluated within one pass**, about 60 seconds, of being written. The PM never sees the result.
- **The gate's loop does more work** each pass: one more query, then one evaluation per new decision, a handful a day.
- **A PM sell, including a full exit, can now be rejected** as `decision_stale`. A stop-loss exit can't. The position's own stop-loss still applies in between.
- **If the gate's process is down**, decisions wait. Once it's back, they are evaluated and, if their quotes are too old, rejected visibly, rather than traded on old prices.
- **The gate's contract and rejection-rules documents change** with this ADR (`specs/008-portfolio-manager/contracts/gate-changes.md`).
