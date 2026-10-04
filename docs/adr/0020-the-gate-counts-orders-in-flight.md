# 0020. The Risk Gate counts orders still in flight when sizing a decision

Status: accepted

Accepted by the owner on 2026-10-05, with the plan for `specs/009-pending-orders`.

## Context

The Risk Gate sizes each decision from the shares held now (`positions`). An order it approved earlier that hasn't filled yet doesn't show there. The Portfolio Manager decides target weights and may decide the same target on every run, at least 30 minutes apart ([0011](0011-event-driven-portfolio-manager-runs.md)). So while a buy is still working, a second decision with the same target is approved as a second full order. The position can end above its target, and the day's order cap is spent twice. Sells have the mirror problem: a second partial sell sized from unchanged holdings can sell past the target. Execution's live checks still hold the position ceiling, the cash reserve and the shares held, so no hard limit is breached; the target and the order cap are what go wrong.

Found by the adversarial review of `specs/008-portfolio-manager`. Specified in `specs/009-pending-orders`.

The gate's database role can read its own verdicts, but not `orders` or `execution_refusals`, so it can't tell whether an approval has filled, is still working or has ended.

## Decision

1. **A narrow view gives the gate what's in flight.** Migration 0013 adds `in_flight_orders`: one row per approval from a trading day that hasn't ended, with only its trading day, symbol, side, unsettled quantity and (for buys) price ceiling. An approval has ended once its order is filled, rejected, canceled or expired, or Execution refused it. The view runs with its owner's rights, like `reference_candidate_symbols`, and `ta_risk_gate` is granted `SELECT` on it alone. No grant on `orders` or `execution_refusals`, and no write.
2. **Decisions are sized from settled holdings**: shares held, plus today's in-flight buys, minus today's in-flight sells, on the same symbol. Re-deciding a met target is `target_already_met`; a changed target orders only the difference. A sell is also capped at the shares held less those already being sold, so it can only ever be smaller than today's rule gives.
3. **A buy's cash reserve check subtracts every in-flight buy's unsettled cost**, on any symbol, each at its own price ceiling, as Execution's check does. This only tightens buys.
4. **Stop-loss exits, the daily-loss breaker, the pause, the order cap and the universe rules are unchanged.** None reads an in-flight number, and no limit in `config/risk.yaml` changes.

## Alternatives considered

- **Reject any new decision on a symbol while an earlier order is in flight.** Simpler, but a changed target, including a PM exit, would wait until the earlier order ends, which for an unfilled day limit buy is the close. The owner chose sizing from settled holdings (`specs/009-pending-orders`, Clarifications 2026-10-05).
- **Grant `ta_risk_gate` `SELECT` on `orders` and `execution_refusals`.** Broader than needed: broker ids, fill prices and refusal details. Not chosen.
- **Execution writes a pending-quantity table.** A new write for Execution and a second source of truth. Not chosen.
- **Fix it in the PM.** The PM never reads orders (its spec), and the safety of order sizing belongs in the deterministic gate (Constitution I).

## Consequences

- **A target weight means what it says while orders are working.** Re-running the PM is harmless again, as [0011](0011-event-driven-portfolio-manager-runs.md) intended.
- **A sell can't anticipate an in-flight buy.** With 50 held and 24 being bought, a decision to exit sells 50; once the buy fills, the PM's next run decides again on 24 held. Canceling in-flight orders is out of scope.
- **The gate's verdicts match Execution's checks more closely**, so fewer approvals end in a refusal.
- **The gate depends on Execution keeping `orders` current.** If Execution stopped updating it, an order that has filled would look in flight, which makes the gate stricter, never looser.
