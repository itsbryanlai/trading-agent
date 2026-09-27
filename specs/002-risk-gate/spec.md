# Feature Specification: Risk Gate

**Feature Branch**: `002-risk-gate`

**Created**: 2026-09-27

**Status**: Draft

**Input**: User description: "Risk Gate: the single deterministic checkpoint every Portfolio Manager decision must pass before it can become an order. Source of truth: docs/specs/risk-gate.md, ADR 0005, ADR 0006. For one decision, record exactly one verdict — approved with a fully-specified order, or rejected naming the rule that fired. Pure function over inputs its caller supplies; rules from a reviewed config file (position ceiling, cash reserve, stop-loss, daily order cap, 20% daily-loss halt, universe floors, market closed). Known gaps: universe reference data, stop-loss price source, and the source of the market-open signal and daily baseline."

## Clarifications

### Session 2026-09-27

- Q: Where does the universe reference data (market cap, average daily dollar volume, share price, listing type) come from? → A: A daily reference table, filled once per trading day by a separate deterministic job that uses a read-only data key (Finnhub) and cannot trade. The gate reads it. A symbol missing from it, or whose data isn't from the current trading day, fails the universe check (fail closed). The job is its own component ([ADR 0010](../../docs/adr/0010-stop-loss-monitor-and-universe-reference-data.md)).
- Q: Which component detects a position crossing its stop-loss line and produces the exit? → A: A monitor inside Execution checks every held position twice an hour (every 30 minutes) during market hours. When one has fallen to or below the stop-loss line, it records a stop-loss trigger with the price it observed. Constitution Principle I requires every order to pass the gate, so the gate evaluates the trigger: it re-checks the drop from the recorded price and the position's entry price, and approves a full exit that no cap or halt can block.
- Q: Stop-loss distance? → A: **20%** below the position's average entry price (raised from 8% at the owner's request; a loosened limit, flagged per `CLAUDE.md`).
- Q: What is the source of the market-open signal, and which account snapshot defines the day's starting-equity baseline? → A: Market open is computed by the caller from an exchange calendar (regular hours, holidays, early closes), with no credential. The baseline is the last account snapshot taken before the current trading day's market open, recorded at the gate's first evaluation of the day. If none exists, every exposure-increasing decision is rejected until one does.

## User Scenarios & Testing *(mandatory)*

The "users" of the Risk Gate are the system's owner (who needs every trade provably inside the configured limits, with no human approving each one) and the neighbouring components: the Portfolio Manager, whose decisions it judges, and Execution, which may only act on what it approves.

### User Story 1 - Every buy is sized inside the limits, or rejected with a reason (Priority: P1)

The Portfolio Manager decides to buy a symbol at some size. The Risk Gate turns that into a fully-specified order no larger than the position ceiling and the cash reserve allow — trimming it when only part of it fits — or rejects it, naming the rule that stopped it.

**Why this priority**: This is the gate's core job and the reason nothing else may construct an order. With no per-trade human approval, it is the only thing between a model's decision and real exposure.

**Independent Test**: Feed decisions with known portfolio state and config into the gate and check each verdict: the exact quantity, the trims, and the named rejection rule. No other component needs to exist.

**Acceptance Scenarios**:

1. **Given** equity of $100,000, no position in AAPL, ample cash, and a decision to buy AAPL at 5% of equity at a quote of $200, **When** the gate evaluates it, **Then** it approves an order for 25 shares with a limit price and a day time-in-force.
2. **Given** an existing AAPL position worth 6% of equity, **When** a decision asks to buy another 5%, **Then** the gate approves a trimmed order that brings the position to exactly the 8% ceiling (rounded down to whole shares) and records that it was trimmed and by which rule.
3. **Given** an AAPL position already at the 8% ceiling, **When** a decision asks to buy more, **Then** the gate rejects it, naming the position ceiling.
4. **Given** cash that is already at the 20% reserve floor, **When** a buy decision arrives, **Then** the gate rejects it, naming the cash reserve; when only part of the buy fits above the floor, it trims to what fits.
5. **Given** a decision to sell AAPL, **When** the gate evaluates it, **Then** it approves a sell of no more than the shares actually held, and rejects a sell of a symbol not held.
6. **Given** a decision to hold, **When** it would be evaluated, **Then** no order and no verdict are produced — a hold is not an order request.

---

### User Story 2 - Hard stops block new exposure but never block an exit (Priority: P1)

Some conditions mean no new exposure is allowed at all: the market is closed, the daily-loss halt is active, the day's order cap is reached, or the symbol isn't in the eligible universe. Under every one of them, a decision that *reduces* exposure still goes through.

**Why this priority**: These are the system's non-negotiable safety rails (Constitution Principles IV and VI). Blocking exits during a bad day would turn a risk control into a risk.

**Independent Test**: For each hard stop in turn, evaluate one buy and one sell against state that triggers it; the buy must be rejected naming that rule and the sell must be approved.

**Acceptance Scenarios**:

1. **Given** the market is closed, **When** any decision is evaluated, **Then** it is rejected naming "market closed" — including sells, since no order can execute outside market hours anyway.
2. **Given** the daily-loss halt is active today, **When** a buy is evaluated, **Then** it is rejected naming the halt; a sell is still approved.
3. **Given** five exposure-increasing orders have already been approved today, **When** a sixth buy is evaluated, **Then** it is rejected naming the daily order cap; a sell is still approved and does not count toward the cap.
4. **Given** a buy of a symbol that fails any universe rule (not US common equity, market cap below $500M, average daily dollar volume below $10M, share price below $5), **When** it is evaluated, **Then** it is rejected naming the specific universe rule, regardless of what the analysts or the Portfolio Manager concluded.

---

### User Story 3 - A bad day trips the daily-loss halt automatically (Priority: P2)

When account equity falls 20% below the day's starting equity, the gate records the halt for today. From that moment every new-exposure decision is rejected for the rest of the day, open positions are left alone, and the halt clears itself the next trading day.

**Why this priority**: It is the system's one automatic hard stop (ADR 0006). It depends on User Story 2's rejection behaviour already existing.

**Independent Test**: With a baseline recorded for today, evaluate a buy while equity sits 20% or more below it; the halt must be recorded against today and the buy rejected. Re-read state as if on the next trading day; the halt must read inactive with no write.

**Acceptance Scenarios**:

1. **Given** today's baseline equity of $100,000 and current equity of $80,000, **When** a buy is evaluated, **Then** the halt is recorded against today, the buy is rejected naming the halt, and no position is sold as a result.
2. **Given** current equity of $80,001 against a $100,000 baseline, **When** a buy is evaluated, **Then** the halt is not triggered.
3. **Given** no baseline has been recorded yet today, **When** the first evaluation of the day runs, **Then** the gate records today's baseline from the agreed source before judging the decision.
4. **Given** a halt recorded yesterday, **When** a buy is evaluated today, **Then** the halt reads inactive and the buy is judged on the other rules.

---

### User Story 4 - Losing positions are exited at the stop-loss line (Priority: P2)

A position that falls 20% below its average entry price is exited in full, whatever the Portfolio Manager currently thinks about it. Execution's monitor notices the drop; the gate confirms it and approves the exit.

**Why this priority**: It bounds the loss on any single position. It ranks below the other stories because the exit only happens when a trigger arrives from Execution's monitor, which is the next feature.

**Independent Test**: Record a stop-loss trigger for a held position with an observed price at, above, and below the line, and evaluate each. Only a genuine breach is approved, as a full exit, even with the daily order cap reached and the halt active.

**Acceptance Scenarios**:

1. **Given** 50 shares of AAPL held at an average entry of $200 and a trigger observing $160 (20% down), **When** the gate evaluates the trigger, **Then** it approves a sell of all 50 shares.
2. **Given** the same position and a trigger observing $161, **When** the gate evaluates it, **Then** it rejects the trigger, naming "stop-loss not breached". A faulty monitor can't force a sale.
3. **Given** a trigger for a symbol no longer held, **When** it is evaluated, **Then** it is rejected, naming "no position".
4. **Given** the daily order cap is reached and the daily-loss halt is active, **When** a genuine trigger is evaluated while the market is open, **Then** the exit is still approved.

---

### User Story 5 - The rules come from a reviewed file, and a bad file stops trading rather than loosening it (Priority: P3)

The owner tunes limits by editing one configuration file under code review. If the file is missing, unreadable, or has an out-of-range value, the gate refuses to approve anything rather than falling back to some default.

**Why this priority**: Editability is a deliberate trade-off (ADR 0005 §0005a). It matters, but only once the rules themselves work.

**Independent Test**: Start the gate with a valid file, a missing file, and a file with an invalid value (e.g. a negative percentage); only the first may produce approvals.

**Acceptance Scenarios**:

1. **Given** a valid configuration, **When** the gate evaluates decisions, **Then** every limit it applies equals the value in the file.
2. **Given** a missing, malformed, or out-of-range configuration, **When** the gate is asked to evaluate, **Then** it approves nothing and reports which setting is invalid.
3. **Given** any verdict, **When** it is inspected later, **Then** it identifies the configuration it was judged against, so a limit change can be traced to the verdicts it affected.

### Edge Cases

- **The same decision evaluated twice** (a retry, or two runs racing): the second evaluation returns the verdict already recorded; it never produces a second or different one.
- **Stale or missing account state**: if there is no account snapshot recent enough to trust, the gate rejects new exposure rather than sizing against old equity or cash. Exits are still approved, sized against the shares actually held.
- **A buy so small that it rounds to zero shares** at the quote: rejected, naming the sizing rule, rather than approved as an empty order.
- **Several rules would reject the same decision**: the verdict names one rule, chosen by a fixed, documented precedence, so the same inputs always name the same rule.
- **A sell decision larger than the holding**: approved for the shares held, not rejected; recorded as trimmed.
- **Equity crosses the loss line between two decisions in the same session**: the decision evaluated after the crossing is rejected, even though the one before it was approved.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The gate MUST record exactly one verdict for every buy or sell decision and every stop-loss trigger it evaluates: approved with a fully-specified order (symbol, side, whole-share quantity, limit price, time-in-force), or rejected naming the single rule that stopped it. Hold decisions MUST produce no verdict and no order.
- **FR-002**: The gate's judgement MUST be a deterministic function of its inputs: the decision, current positions, the latest account state, today's halt and baseline state, the configuration, whether the market is open, and the evaluation time. Identical inputs MUST always yield an identical verdict. The judgement MUST make no network call and consult no source it isn't given.
- **FR-003**: The gate MUST size a buy from the decision's size and the quote the Portfolio Manager recorded with it, rounding down to whole shares.
- **FR-004**: The gate MUST cap every buy so the resulting position does not exceed the configured per-symbol ceiling (default 8% of equity), trimming a partially-fitting buy and rejecting one that fits not at all.
- **FR-005**: The gate MUST cap every buy so that cash does not fall below the configured reserve (default 20% of equity), trimming or rejecting in the same way.
- **FR-006**: The gate MUST approve a sell only for shares actually held, trimming an oversized sell to the holding and rejecting a sell of a symbol not held. The system is long-only.
- **FR-007**: The gate MUST reject every decision when the market is closed at evaluation time.
- **FR-008**: While the daily-loss halt is active, the gate MUST reject every exposure-increasing decision and MUST still approve exposure-reducing ones.
- **FR-009**: When its evaluation finds current equity at or below the configured loss line (default 20%) under today's baseline, the gate MUST record the halt against today before rejecting the decision. It MUST NOT sell or reduce any position as a consequence.
- **FR-010**: The gate MUST reject an exposure-increasing decision once the configured number of exposure-increasing orders (default 5) has already been approved that trading day. Exposure-reducing orders MUST NOT count toward or be blocked by the cap.
- **FR-011**: The gate MUST independently re-check every buy's symbol against every universe rule (US common equity only; market cap, average daily dollar volume, and share price floors) using the daily universe reference data, and reject one that fails, whatever upstream components concluded. A symbol with no reference data from the current trading day MUST fail the check.
- **FR-012**: The gate MUST evaluate stop-loss triggers recorded by Execution's monitor. It MUST approve a full exit of the shares held when the trigger's observed price is at or below the configured stop-loss distance (default 20%) under the position's average entry price. It MUST reject a trigger that doesn't meet the line or names a symbol not held. No hard stop in FR-008 to FR-010 may block an approved stop-loss exit. The market being closed (FR-007) still does.
- **FR-013**: The gate's caller MUST derive the market-open signal from an exchange calendar, with no credential. The gate MUST take today's starting-equity baseline from the last account snapshot taken before the current trading day's market open, recording it at its first evaluation of the day. If no such snapshot exists, it MUST reject every exposure-increasing decision.
- **FR-014**: The gate MUST read its limits only from the reviewed configuration file. A missing, malformed, or out-of-range configuration MUST cause it to approve nothing and report the offending setting. No agent may write the file, and the Portfolio Manager MUST NOT read it.
- **FR-015**: Every verdict MUST record which configuration it was judged against and, for an approved order that was trimmed, which rule trimmed it.
- **FR-016**: When several rules would reject a decision, the gate MUST name one, chosen by a fixed, documented precedence.
- **FR-017**: Evaluating a decision or stop-loss trigger that already has a verdict MUST return the existing verdict and record nothing new.
- **FR-018**: The gate MUST reject exposure-increasing decisions when no sufficiently recent account state is available, rather than size against stale equity or cash.

### Key Entities

- **Decision** (existing, feature 001): the Portfolio Manager's direction, size as a share of equity, and the quote it fetched itself.
- **Verdict** (existing, feature 001): approved with a fully-specified order, or rejected with a named rule. There is one per decision or per stop-loss trigger, and each verdict comes from exactly one of the two. This feature adds what the verdict records about trims and the configuration version, and lets a verdict come from a trigger.
- **Risk configuration**: the reviewed file of limits: position ceiling, cash reserve, stop-loss distance, daily order cap, daily-loss line, and universe floors.
- **Portfolio state** (existing): current positions, and the latest account equity and cash.
- **Daily control state** (existing): today's halt and today's starting-equity baseline.
- **Universe reference data** (new): per-symbol listing type, market cap, average daily dollar volume, and share price, as of a trading day. Written only by the reference-data job; read by the gate.
- **Stop-loss trigger** (new): one observation by Execution's monitor that a held position is at or below its stop-loss line, recording the symbol, the observed price, and when. Written only by Execution; evaluated by the gate into exactly one verdict.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Across every combination of inputs in the test suite, and at least 10,000 randomly generated ones, no approved order would take a position above the ceiling or cash below the reserve.
- **SC-002**: Evaluating the same inputs twice yields an identical verdict (same outcome, quantity, limit price, named rule) in 100% of cases.
- **SC-003**: Under every hard stop (market closed excepted), 100% of exposure-reducing decisions are approved and 100% of exposure-increasing decisions are rejected naming that stop.
- **SC-004**: The first evaluation after equity crosses the daily-loss line records the halt; no exposure-increasing decision is approved after that moment on the same trading day.
- **SC-005**: With a missing or invalid configuration, zero decisions are approved.
- **SC-006**: Every recorded verdict can be traced to the configuration it was judged against.

## Assumptions

- A decision's size is a share of *current* equity for buys, and a share of equity to divest for sells, capped at the holding.
- The approved order's limit price is the quote the Portfolio Manager recorded with the decision. Whether Execution may re-price within a tolerance at submission is the Execution feature's decision.
- "Sufficiently recent" account state means an account snapshot taken on the current trading day. The exact freshness window is a planning detail.
- Hold decisions are not submitted to the gate. The Portfolio Manager's caller skips them.
- Enforcing "the Portfolio Manager never reads the configuration" is a code-level rule verified by test. Both run in the same worker process, so an operating-system permission can't separate them.
- The gate writes only verdicts and today's halt and baseline, within the grants already provided by feature 001. This feature adds the storage for universe reference data and stop-loss triggers, with a grant letting the gate read both.
- **Dependencies on later features, not built here.** The reference-data job that fills the universe table; Execution's 30-minute stop-loss monitor that records triggers; and Execution recording an account snapshot every trading day before the open, so the baseline exists. Until the reference job runs, every buy fails the universe check. That fails closed, which is the intended behaviour.
- Placing orders, re-pricing, and fill tracking belong to Execution (next feature). Deciding direction and size belongs to the Portfolio Manager. Judging whether a trade is *good* is out of scope.
