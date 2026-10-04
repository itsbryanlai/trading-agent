# Feature Specification: The Risk Gate counts orders still in flight

**Feature Branch**: `009-pending-buys`

**Created**: 2026-10-05

**Status**: Draft

**Input**: User description: "Feature 009: the Risk Gate counts in-flight orders when sizing a PM decision. Today the gate sizes a decision from filled holdings (positions) only. An approved order that hasn't filled yet is invisible to it, so when the PM runs again (event-driven runs are 30+ minutes apart, ADR 0011) and decides the same target, the gate approves a second full order: a buy to 5% approved at 10:00 and still unfilled at 10:30 is approved again, and the position can end above the PM's target (Execution's own ceiling checks still cap it at the 8% position limit and the cash reserve, but it overshoots the target and spends a second of the day's five exposure-increasing order slots). Sells have the mirror problem: a second partial sell sized from unchanged holdings can sell past the target, down to a full exit. Found by feature 008's adversarial review (specs/008-portfolio-manager, PR #10 follow-up). Goal: a target weight means what it says even while orders are in flight: re-deciding the same target never produces a second order for the same exposure, and a changed target orders only the difference from where holdings will be once in-flight orders settle. Constraints: the Risk Gate and Execution stay deterministic, with no model call (Constitution I, ADR 0005); config/risk.yaml limits don't change; the daily-loss breaker and stop-loss exits must be unaffected (an exit must never be blocked by this); Execution's own live re-checks stay as they are; the PM doesn't change and still never reads orders. The gate's database role can read risk_verdicts but not orders or execution_refusals today, so knowing whether an approval has filled, partly filled or ended needs a new least-privilege read grant, justified in this spec (Constitution III) and recorded in an ADR before code. This is order logic: flag before applying."

## Clarifications

### Session 2026-10-05

- Q: While an order on a symbol is in flight, should the gate size a new decision from the settled holdings, or reject any new decision on that symbol until the earlier approval ends? → A: Size from the settled holdings and order only the difference. The target stays exact, and a changed decision, including a PM exit, never waits behind an unfilled order.

## User Scenarios & Testing *(mandatory)*

The users are:
- **the owner**, who needs a target weight to mean what it says, with no human approving each trade ([ADR 0006](../../docs/adr/0006-autonomous-operation-with-daily-loss-breaker.md));
- **the Portfolio Manager**, whose decisions are target weights and which may decide the same target again on every run ([`specs/008-portfolio-manager`](../008-portfolio-manager/spec.md), FR-001);
- **Execution**, which keeps acting only on what the gate approves.

**Terms**:
- **In flight**: an approval from today that hasn't ended yet. Either Execution hasn't acted on it yet, or it placed an order that is still working, or partly filled and still working. An approval has ended once its order is filled, rejected, canceled or expired, or once Execution refused it.
- **Unsettled quantity**: the shares an in-flight approval may still add (a buy) or remove (a sell): its approved quantity, less what has already filled. Filled shares are already in the holdings.
- **Settled holdings**: the shares held now, plus the unsettled quantity of in-flight buys, minus that of in-flight sells. It's where the holdings will be if every in-flight order fills.

### User Story 1 - Re-deciding the same target orders nothing more (Priority: P1)

The PM decides "buy AAPL to 5%" at 10:00. The gate approves a buy, and the order is still working at 10:30 when the PM decides "buy AAPL to 5%" again. The gate sees the first order and approves nothing more. The same holds for a sell.

**Why this priority**: it is the bug. Without it, every PM run while an order is working can stack another full order, overshooting the target and spending the day's order cap.

**Independent Test**: record one approved buy with no order yet, then evaluate a second decision with the same target. It is rejected as "target already met". Repeat with the order placed and working, then partly filled.

**Acceptance Scenarios**:

1. **Given** equity of $100,000, no AAPL held, and an approved buy of 24 AAPL at a $200 quote from earlier today that Execution hasn't acted on, **When** a new decision targets 5% at $200, **Then** it is rejected as `target_already_met` and no second slot of the daily order cap is used.
2. **Given** the same buy placed and still working with 10 of 24 filled (10 now held), **When** a decision targets 5% again, **Then** it is rejected as `target_already_met`.
3. **Given** 50 AAPL held and an approved sell to 2% still working, **When** a decision targets 2% again, **Then** it is rejected as `target_already_met`, so the position isn't sold past 2%.

---

### User Story 2 - A changed target orders only the difference (Priority: P1)

The PM changes its mind while an order is in flight: from 5% to 7%, or from 5% down to 3%. The gate orders only the difference from the settled holdings, never from the filled holdings alone.

**Why this priority**: a target weight must mean the same thing whether or not orders are working. Sizing from filled holdings alone would buy or sell too much.

The gate sizes the new decision from the settled holdings and orders only the difference (Clarifications). A changed decision acts on the PM's next run; nothing waits for an earlier order to end.

**Independent Test**: with a buy in flight, evaluate a decision with a higher target and one with a lower target. The approved quantities are the differences from the settled holdings.

**Acceptance Scenarios**:

1. **Given** $100,000 equity, nothing held and an in-flight buy of 24 AAPL at $200, **When** a decision targets 7% at $200, **Then** a buy of about 10 more shares is approved (the 7% target less the 24 in flight, sized as today at the price ceiling).
2. **Given** the same in-flight buy, **When** a sell decision targets 0%, **Then** the gate rejects it as `no_position`, since nothing is held yet; the in-flight buy is Execution's to finish or the close's to expire.
3. **Given** 50 shares held and an in-flight sell of 30, **When** a decision targets 0%, **Then** a sell of the remaining 20 is approved, never 50.

---

### User Story 3 - Exits and the hard stops are untouched (Priority: P1)

Counting in-flight orders never stops a position from being exited and never weakens a limit.

**Why this priority**: the system's safety net is the stop-loss and the daily-loss breaker ([ADR 0006](../../docs/adr/0006-autonomous-operation-with-daily-loss-breaker.md)). This change must not reduce either.

**Independent Test**: with in-flight buys and sells present, evaluate a stop-loss trigger, a full-exit decision and a buy with the daily-loss line crossed. Each verdict is the one the gate gives today, apart from the sell quantities User Story 2 adjusts.

**Acceptance Scenarios**:

1. **Given** a breached stop-loss on a held position with an in-flight buy of more shares, **When** the trigger is evaluated, **Then** the exit is approved for the shares held, exactly as today.
2. **Given** the daily-loss line crossed and an in-flight buy, **When** any decision is evaluated, **Then** the halt is recorded and buys are rejected, exactly as today.
3. **Given** in-flight orders on other symbols, **When** a decision on AAPL is evaluated, **Then** its verdict depends only on AAPL's own in-flight orders.

---

### Edge Cases

- **An approval from an earlier day**: never in flight. Approvals are valid only on their own trading day, and day orders end at the close.
- **An approval Execution refused**: ended, with nothing unsettled.
- **An order filled after the gate read it**: the gate's view is one consistent moment. If it is slightly stale, Execution's live re-checks (position ceiling, cash reserve, shares held) still catch any breach, as today.
- **A stop-loss exit in flight**: counts like any sell. A PM sell on the same symbol is sized from what remains.
- **An in-flight buy on a symbol the PM now wants to sell**: covered by User Story 2, scenario 2.
- **The gate can't read the order state** (a database error): the evaluation fails and writes nothing, as any gate read failure does today. It never proceeds as if nothing were in flight.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When evaluating a buy or sell decision, the gate MUST account for every in-flight approval on the same symbol from the same trading day, from decisions and stop-loss triggers alike.
- **FR-002**: Re-deciding a target that the settled holdings already meet MUST be rejected as `target_already_met`, so it produces no order and uses no slot of the daily order cap.
- **FR-003**: A decision whose target differs from the settled holdings MUST be sized from the settled holdings, so it orders only the difference: a buy from the settled holdings up to the target, a sell from the settled holdings down to it. No new rule blocks a decision because an earlier order is in flight (Clarifications).
- **FR-004**: A stop-loss exit MUST be evaluated exactly as today. No in-flight order may block, delay or shrink it.
- **FR-005**: The daily-loss breaker, the pause, the order cap, the universe rules, the position ceiling and the cash reserve MUST behave as today. None of their limits changes, and `config/risk.yaml` is unchanged.
- **FR-006**: The gate MUST stay deterministic: the same decision, holdings, in-flight orders and configuration always give the same verdict, with no model call and no network call.
- **FR-007**: The gate's database role MUST gain read access to the order state it needs and nothing more. It MUST NOT gain any write, and no other role's access changes (Constitution III). The grant is justified here: the gate can't size a target correctly without knowing what is in flight.
- **FR-008**: An ADR recording the new read and the sizing rule MUST be accepted before code changes (Constitution V). `specs/002-risk-gate`'s contracts and `docs/specs/risk-gate.md` MUST be updated to match.
- **FR-009**: Execution, the PM and the orchestrator MUST NOT change behaviour.

### Key Entities

- **Approval** (existing, `risk_verdicts` approved): the gate's own record of an order it allowed, with side, quantity and trading day.
- **Order** (existing, `orders`, written by Execution): the broker order for an approval, with its status and filled quantity.
- **Refusal** (existing, `execution_refusals`, written by Execution): an approval Execution declined.
- **Settled holdings** (computed, never stored): held shares adjusted by every in-flight approval's unsettled quantity.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Across the test suite, deciding the same target any number of times while an order is in flight produces exactly one approved order.
- **SC-002**: No approved sell, together with the in-flight sells on the same symbol, exceeds the shares held.
- **SC-003**: Every existing Risk Gate test whose scenario has nothing in flight gives the same verdict as before.
- **SC-004**: Every stop-loss exit test gives the same verdict whether or not orders are in flight.
- **SC-005**: The gate's role can read the order state it needs, and an integration test proves it still can't write any table it couldn't write before.

## Assumptions

- **Partial fills are already in the holdings**: Execution updates positions from each confirmed fill, so only the unfilled part of an order is unsettled.
- **Settled holdings use the order's approved quantity**: the gate doesn't predict prices. Sizing stays as today, at the price ceiling for buys.
- **A small race is acceptable**: an order can fill between the gate's read and its verdict. Execution re-checks every limit against live broker data before submitting, as it does today.
- **The PM is unchanged**: it still never reads orders. It may keep re-deciding the same target, which is now harmless.
- **Out of scope**: canceling in-flight orders when the PM changes its mind; any change to Execution; any new risk limit.
