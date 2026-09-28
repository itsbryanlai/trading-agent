# 0014. Stop-loss triggers must be fresh and confirmed; equity is recorded every stop-loss window

Status: accepted

## Context

An adversarial review of Execution (`specs/003-execution`) found two gaps in how the system's two
automatic protections see prices and equity.

1. **One price decides a full sale.** Execution's monitor records a stop-loss trigger when a held
   position's last traded price is at or below its line (`specs/003-execution` Clarifications). The
   Risk Gate re-checks the line from its own entry price, but takes the observed price at face value
   and never looks at when it was observed
   ([0010](0010-stop-loss-monitor-and-universe-reference-data.md)). The price comes from IEX, one
   exchange with a small share of volume. So one odd print below the line sells the whole position.
   A trigger evaluated long after it was recorded (the gate's trigger runner was down, per
   [0013](0013-deterministic-services-run-their-own-loops.md)) is approved even if the price has
   since recovered.
2. **The daily-loss line is only seen when someone looks.** Equity is recorded before the open and
   just before each buy. A drop through the 20% line between those moments is never recorded. Buys
   resume once equity recovers, although [0006](0006-autonomous-operation-with-daily-loss-breaker.md)
   says new orders halt for the rest of the day once the line is crossed.

## Decision

1. **The gate rejects a stale trigger.** A stop-loss trigger observed more than **10 minutes**
   before the gate evaluates it is rejected with a new rule, `stop_loss_trigger_stale`, checked
   right after `market_closed`. The monitor records a new trigger on its next check if the breach
   is still real. The 10 minutes is a constant in the gate's code, not a `config/risk.yaml` limit:
   it decides whether an observation is still usable, not how much risk to take.
2. **The monitor needs a second reading.** A position triggers only if its last traded price **and
   its current bid** (what a market sell would actually get) are both at or below the line. The
   trigger still records the last traded price as its observed price. A missing, zero or stale bid
   (older than 60 seconds) means the check failed and is retried within the window, as a failed
   trade fetch already is.
3. **Execution records equity at every stop-loss window.** In each 30-minute window during market
   hours it fetches the account and records an `account_snapshots` row, once per window, retried
   within the window on failure. A crossing is then recorded within 30 minutes. Execution's buy
   check already refuses buys for the rest of the day once any snapshot since the open is at or
   below the line (`specs/003-execution` FR-004), and the gate records the halt from the same
   snapshots at its next evaluation.

Decided by the owner on 2026-09-28, after the adversarial review of feature 003.

## Alternatives considered

- **Leave both as they were.** Rejected by the owner: a single print can end a position, and the
  "halt for the rest of the day" promise only held if a buy happened to sample equity at the
  wrong moment.
- **An age limit without a second reading.** It covers the runner-was-down case but not the odd
  print.
- **The gate also re-checks the bid.** That would mean recording the bid on the trigger and a new
  column, for a check the monitor already makes. Not chosen; revisit if the monitor is ever
  suspected of recording unconfirmed triggers.
- **A continuous equity stream** from the broker's account updates. More precise, but it is a
  long-lived connection to keep alive and fake, which `specs/003-execution` research E7 declined for
  order updates for the same reason.

## Consequences

- A genuine exit can be delayed: when the last trade is through the line but the bid isn't yet,
  no trigger is recorded until the next window. When a trigger goes stale before the gate sees it,
  one more window passes. The owner accepted that delay in exchange for not selling on one bad print.
- `specs/002-risk-gate` gains a rule name (`stop_loss_trigger_stale`) and the gate's stop-loss
  request carries the observation time; its FR-012 and rejection-rules contract are amended.
- `specs/003-execution` FR-014 (second reading) and FR-015 (snapshot every window) are amended.
  The broker port's quote gains a bid.
- There are up to 13 more `account_snapshots` rows per trading day. The gate's "latest snapshot
  today" is now at most 30 minutes old during market hours, so its sizing is also fresher.
