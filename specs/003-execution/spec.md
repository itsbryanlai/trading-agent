# Feature Specification: Execution

**Feature Branch**: `003-execution`

**Created**: 2026-09-27

**Status**: Draft

**Input**: User description: "Feature 003 — Execution. Source: docs/specs/execution.md, ADR 0010 (stop-loss monitor, pre-open snapshot, live equity check before buys), specs/002-risk-gate Clarifications (buy limit at live quote under ceiling; sells/stop-loss exits are day market orders; approvals lapse at close, FR-019). Open questions to mark for /speckit-clarify: (1) order id scheme — {trading_day}-{symbol}-{side} clashes with same-day stop-loss + PM sell and with multiple event-driven PM buys (ADR 0011); recommended: include a verdict-derived component; (2) paper-only guard mechanism — recommended: pinned paper endpoint + refuse any other configured URL + startup account check confirming paper; (3) tests use a fake broker only, never real broker calls."

## Clarifications

### Session 2026-09-27

- Q: How should each order's identifier be built so two different approvals on the same day never share one? → A: `{trading_day}-{symbol}-{side}-{first 8 hex characters of the verdict id}`. Readable, rebuilt identically after a restart (the verdict is durable), and unique because each verdict gets at most one order. Supersedes the `{trading_day}-{symbol}-{side}` format from `specs/001-data-model` research note R11, so it needs an ADR and a migration before implementation.
- Q: How should Execution make sure it is connected to the paper-trading account and never the live one? → A: Two independent checks. The broker's paper address is fixed in code, not taken from configuration; if configuration names any other broker address, startup stops. Then a read-only account check at startup must confirm the account is a paper account. If either check fails, or the account check can't be completed, Execution does not start.
- Q: While the owner has trading paused, should Execution still place buys the Risk Gate approved before the pause? → A: No. While paused, Execution refuses to submit approved buys, recorded as a final "trading paused" refusal. Sells and stop-loss exits still go through. This needs a read of the pause flag for Execution's database role.
- Q: If an approved buy isn't placed because the live price is above its ceiling, does Execution retry later that day? → A: No. The refusal is final and the approval is spent; the next Portfolio Manager run decides again from fresh prices (ADR 0011 runs are event-driven, at least 30 minutes apart).
- Q: Which price does the stop-loss monitor compare against the stop-loss line? → A: The last traded price. It is also the observed price recorded on the trigger, from which the gate re-checks the drop.

### Session 2026-09-28 (after `/speckit-analyze`)

- Q: The Execution process would hold the Risk Gate's database credential to evaluate stop-loss triggers, a separation enforced only by code (Constitution Principle III). How are triggers evaluated? → A: The Risk Gate evaluates recorded triggers in its own process, holding only its own credential. Execution records the trigger and later submits the approved exit like any other approval, at the cost of up to a couple of minutes' extra delay. Execution never holds the gate's credential.

## User Scenarios & Testing *(mandatory)*

The "users" of Execution are the system's owner, who needs every order the broker sees to be one the Risk Gate approved and that Execution's own live numbers confirm, and the neighbouring components: the Risk Gate, whose approvals Execution carries out and which depends on Execution for account snapshots and stop-loss triggers, and every reader of positions and orders (the Portfolio Manager, the journal, the Assistant, the dashboard).

Execution is the only component that holds the broker credential and the only one that talks to the broker. It never decides direction or size, and never judges whether a trade is allowed. It carries out approvals, and refuses any approval that its own fresh numbers can't confirm.

### User Story 1 - An approved buy is placed only if live numbers still confirm it (Priority: P1)

The Risk Gate approves a buy: a symbol, a whole-share quantity, and a price ceiling, for today. Before placing it, Execution fetches the live quote, live account equity and cash, and the current holding. It places a day limit order at the live quote only if the quote is at or under the ceiling, equity is above today's daily-loss line, and the position ceiling and cash reserve still hold at live numbers. Otherwise it places nothing and records why.

**Why this priority**: This is the last check between a model's decision and exposure. The gate sized the buy against a snapshot that may be hours old; Execution's live re-check is what makes a stale snapshot cause a refused buy rather than a breach (`specs/002-risk-gate` Assumptions).

**Independent Test**: With a fake broker supplying the quote, equity, cash and holdings, hand Execution approved buys and check, for each, whether an order was placed, at what limit price and quantity, or which refusal was recorded. No real broker, no other agent.

**Acceptance Scenarios**:

1. **Given** an approved buy of 24 AAPL with a ceiling of $202 approved today, a live ask of $201.50, live equity of $100,000 against a baseline of $100,000, ample cash, and no AAPL held, **When** Execution processes it during market hours, **Then** it places a day limit buy of 24 AAPL at $201.50 and records the order.
2. **Given** the same approval and a live ask of $202.01, **When** Execution processes it, **Then** it places nothing and records the refusal as "quote above ceiling".
3. **Given** a baseline of $100,000 and live equity of $80,000, **When** Execution processes any approved buy, **Then** it first records an account snapshot of $80,000, then places nothing and records the refusal as "daily-loss line crossed".
4. **Given** live equity of $100,000, 10 AAPL already held, and an approved buy of 31 AAPL at a live ask of $200 (so the position would be 41 × $200 = $8,200, 8.2% of equity, over the 8% ceiling), **When** Execution processes it, **Then** it places nothing and records the refusal as "position ceiling", with the numbers it saw. It never trims the order itself.
5. **Given** a buy that would leave live cash below 20% of live equity, **When** Execution processes it, **Then** it places nothing and records the refusal as "cash reserve".
6. **Given** an approval whose trading day is yesterday, **When** Execution sees it, **Then** it places nothing and records the refusal as "approval expired".
7. **Given** an approval from today but the market is closed (before the open, after the close, a holiday, or after an early close), **When** Execution sees it, **Then** it places nothing. After today's close the approval lapses permanently.
8. **Given** the owner paused trading after the buy was approved, **When** Execution processes it, **Then** it places nothing and records the refusal as "trading paused". An approved sell processed at the same moment is still placed.

---

### User Story 2 - Approved sells and stop-loss exits are placed as market orders (Priority: P1)

The Risk Gate approves a sell (from a Portfolio Manager decision) or a stop-loss exit (from a trigger). Execution places a day market order for the approved quantity, as long as that many shares are actually held. It does not run the equity check on exits, so a bad day never traps a position.

**Why this priority**: Exits are how losses are bounded. A limit on an exit could leave it unfilled in a falling market, and a daily-loss check on an exit would block the very trades that reduce risk.

**Independent Test**: Hand Execution approved sells and stop-loss exits against a fake broker whose holdings and equity vary, including equity below the daily-loss line; check that a market order is placed for each one backed by shares held, and that none is blocked by equity.

**Acceptance Scenarios**:

1. **Given** 50 AAPL held and an approved sell of 50 AAPL today, **When** Execution processes it during market hours, **Then** it places a day market sell of 50 AAPL.
2. **Given** live equity below the daily-loss line, **When** Execution processes an approved sell or stop-loss exit, **Then** it still places the market sell.
3. **Given** an approved sell of 50 AAPL but only 30 AAPL held now (a fill landed after the gate's evaluation), **When** Execution processes it, **Then** it places nothing and records the refusal as "shares held differ from approval", with both numbers.
4. **Given** an approved sell whose trading day is yesterday, **When** Execution sees it, **Then** it places nothing and records "approval expired".

---

### User Story 3 - No order is ever placed twice, even across a crash (Priority: P1)

Every approval results in at most one broker order. If Execution crashes after placing an order but before recording it, the restarted process finds the existing broker order and records it rather than placing a second one.

**Why this priority**: A duplicate order is an unapproved order. With no per-trade human approval (ADR 0006), idempotency is a safety property, not a convenience.

**Independent Test**: With a fake broker, simulate a crash at each step (before placing, after placing but before recording, after recording) and restart; the broker must end up with exactly one order per approval in every case.

**Acceptance Scenarios**:

1. **Given** an approval already recorded as submitted, **When** Execution processes it again, **Then** it places nothing new.
2. **Given** an order placed at the broker but not recorded (crash in between), **When** Execution restarts and processes the approval, **Then** it finds the broker's order by its deterministic identifier, records it, and places nothing new.
3. **Given** a stop-loss exit and a Portfolio Manager sell of the same symbol approved on the same trading day, or two Portfolio Manager buys of the same symbol on the same day (ADR 0011's event-driven runs), **When** Execution processes both, **Then** each approval gets its own order, and neither is rejected as a duplicate of the other (see FR-008).

---

### User Story 4 - Execution refuses to run against anything but the paper account (Priority: P1)

At startup, before doing anything else, Execution confirms it is pointed at the broker's paper-trading environment. If it isn't, or it can't tell, it refuses to start.

**Why this priority**: Paper-only is the hard boundary between this project and real money (Constitution Principle VI). Nothing else in the feature is safe without it.

**Independent Test**: Start Execution with configuration naming the paper environment, a live environment, an unknown address, and no address; only the first may start.

**Acceptance Scenarios**:

1. **Given** configuration pointing at the broker's paper environment, **When** Execution starts, **Then** it starts.
2. **Given** configuration pointing at any other environment, including the broker's live one, **When** Execution starts, **Then** it exits immediately with a clear error and makes no broker call that could place an order.
3. **Given** the startup guard passes but the broker reports the account is not a paper account, **When** Execution starts, **Then** it refuses to start (see FR-013 for how the guard works).
4. **Given** the account check can't reach the broker, **When** Execution starts, **Then** it refuses to start rather than assuming paper.

---

### User Story 5 - Held positions are checked against the stop-loss line every 30 minutes (Priority: P2)

During market hours, every 30 minutes, Execution fetches the last traded price of every held position. For each one at or below 20% under its average entry price, it records a stop-loss trigger with the price it saw. The Risk Gate evaluates the trigger on its own, and Execution places the exit only if the gate approves it.

**Why this priority**: It bounds the loss on any one position (ADR 0010). It ranks below the P1 stories because it reuses their exit path.

**Independent Test**: With a fake broker supplying prices, run one monitor cycle over held positions above, at, and below the line; triggers must be recorded only for those at or below it, each must be handed to the gate, and only approved exits placed.

**Acceptance Scenarios**:

1. **Given** 50 AAPL held at an average entry of $200 and a last traded price of $160, **When** the monitor runs, **Then** it records a trigger at $160, the gate approves a full exit, and on its next cycle Execution places a market sell of 50 AAPL.
2. **Given** the same position at $161, **When** the monitor runs, **Then** it records no trigger.
3. **Given** the gate rejects a trigger, **When** the monitor finishes, **Then** Execution places nothing for that position. Execution never constructs an exit itself.
4. **Given** an exit order for a symbol already open at the broker from earlier today, **When** the monitor runs again, **Then** it records no new trigger for that symbol.
5. **Given** the market is closed, **When** the schedule would fire, **Then** no check runs.

---

### User Story 6 - Account state is recorded before every open and before every buy (Priority: P2)

Every trading day before the open, Execution records the broker's account equity, cash and buying power as an account snapshot. The Risk Gate takes the day's daily-loss baseline from it. Execution also records a snapshot immediately before every buy (User Story 1).

**Why this priority**: Without the pre-open snapshot the gate rejects every buy that day (fails closed). It's needed for the system to trade at all, but it's simple.

**Independent Test**: Run the pre-open duty on a trading day, a weekend, and a holiday against a fake broker; a snapshot must be recorded only on the trading day, dated before that day's open.

**Acceptance Scenarios**:

1. **Given** a trading day, **When** the pre-open duty runs, **Then** exactly one snapshot is recorded before the open.
2. **Given** a weekend or exchange holiday, **When** the schedule would fire, **Then** nothing is recorded.
3. **Given** the broker is unreachable, **When** the pre-open duty runs, **Then** it retries until the open. If it never succeeds, no snapshot is recorded and the gate fails closed on buys that day. Execution never writes a snapshot it didn't fetch.

---

### User Story 7 - Positions and orders reflect confirmed fills (Priority: P2)

Execution follows each order it placed until it is filled, partly filled and done, rejected, or cancelled (including day orders expiring at the close), and updates the order record and the held positions from what the broker confirms.

**Why this priority**: Every other component reads positions from the database, not the broker. The gate's sizing and the stop-loss monitor are only as right as these records.

**Independent Test**: With a fake broker, drive orders through full fill, partial fill then expiry, rejection, and a buy on top of an existing position; the order records and positions must match what the broker reports after each step.

**Acceptance Scenarios**:

1. **Given** a buy of 24 AAPL fills at $201.50 with none held, **When** Execution records the fill, **Then** the position is 24 AAPL at an average entry of $201.50.
2. **Given** 24 AAPL held at $200 and a buy of 6 fills at $210, **When** the fill is recorded, **Then** the position is 30 AAPL at an average entry of $202.
3. **Given** a sell of all shares fills, **When** it is recorded, **Then** the position no longer exists.
4. **Given** a buy of 24 fills 10 shares and then expires at the close, **When** Execution records it, **Then** the order shows 10 filled and a final state, and the position reflects 10.
5. **Given** the broker rejects an order (e.g. halted symbol), **When** Execution records it, **Then** the order shows rejected with the broker's reason and is not retried that day.

### Edge Cases

- **Approval pending when the broker is unreachable**: nothing is placed; the approval is retried on the next cycle while it's still valid (same trading day, market open). A transient failure is never recorded as a final refusal.
- **Several approved buys in quick succession**: when re-deriving the position ceiling and cash reserve, Execution counts its own still-open buy orders as already spent and already held, so two unfilled buys can't together breach a limit that each passes alone.
- **An approval arrives in the last minutes before the close**: Execution still submits it if the market is open; a day order unfilled at the close expires, and the approval is spent.
- **No live quote available for a buy** (e.g. symbol halted, quote missing or zero): nothing is placed; treated as transient and retried while the approval is valid.
- **No baseline for today when a buy arrives**: Execution refuses it. (The gate would already have rejected it; this is the same distrust applied again.)
- **Records disagree with the broker** (positions in the database differ from the broker's): Execution logs the discrepancy and treats the broker as the record of what is actually held.
- **Stop-loss position partly sold between the trigger and the exit**: the approved exit's quantity exceeds what's held, so Execution refuses it (User Story 2, scenario 3); the next monitor cycle catches the remainder.
- **Early-close days and holidays**: every "market open" and "trading day" judgement comes from the same exchange calendar the gate uses.
- **Execution down for part of the day**: at restart it recovers state first (User Story 3), then resumes. Approvals from earlier trading days are never submitted; missed monitor cycles are not replayed, the next one simply runs.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Execution MUST place a broker order only for a Risk Gate verdict recorded as approved. It MUST NOT construct, resize, re-price above the ceiling, or change the side of any order beyond what the approval specifies.
- **FR-002**: Execution MUST submit an approval only on the trading day it was approved for and only while the market is open, judged from the same exchange calendar the gate uses. An approval from an earlier trading day MUST never be submitted (`specs/002-risk-gate` FR-019).
- **FR-003**: For an approved **buy**, Execution MUST fetch the live asking price at submission time and place a day limit order at that price for the approved quantity, only if the price is at or under the approval's ceiling. Otherwise it MUST place nothing and record a final "quote above ceiling" refusal; it MUST NOT wait for or retry at a lower price later.
- **FR-004**: Immediately before submitting any approved buy, Execution MUST fetch live account equity, cash and buying power from the broker and record them as an account snapshot. It MUST NOT submit if that equity, or the equity of any account snapshot recorded since today's open, is at or below today's daily-loss line (baseline × (1 − `daily_loss_halt_pct`)), so that once the line has been crossed no buy is submitted for the rest of the trading day even if equity recovers (Constitution Principle IV). The baseline is the last snapshot taken on today's date before today's open, the same definition the gate uses. With no baseline for today, it MUST NOT submit.
- **FR-005**: Before submitting any approved buy, Execution MUST independently re-derive the position ceiling and the cash reserve from live broker figures (equity, cash, the current holding) valued at the live ask (never below the submitted limit price), counting its own still-open buy orders as already held and already spent. It MUST refuse (never trim) a buy that would breach either, using the same limits the gate reads from the reviewed risk configuration.
- **FR-006**: For an approved **sell or stop-loss exit**, Execution MUST place a day market order for the approved quantity, and MUST NOT apply the daily-loss check in FR-004. It MUST refuse if fewer shares are held than the approval specifies.
- **FR-007**: Every refusal MUST be recorded durably against its approval with a named reason (approval expired, quote above ceiling, daily-loss line crossed, position ceiling, cash reserve, shares held differ from approval, no baseline today, trading paused, identifier clash) and the numbers Execution saw. A recorded refusal is final for that approval. Transient failures (broker unreachable, no live quote) MUST NOT be recorded as refusals; they are retried while the approval remains valid.
- **FR-008**: Each approval MUST produce at most one broker order, identified by a deterministic identifier that a restarted process re-derives identically and that the broker also holds, so a crash-restart finds the existing order instead of placing another. Two different approvals MUST never share an identifier, including a stop-loss exit and a Portfolio Manager sell of the same symbol on the same day, and two buys of the same symbol on the same day. The identifier is `{trading_day}-{symbol}-{side}-{first 8 hex characters of the verdict id}`, used both as the order's record id and as the identifier sent to the broker. If two approvals ever produced the same identifier, Execution MUST refuse the second rather than treat it as already submitted.
- **FR-009**: At startup, before processing any approval, Execution MUST reconcile every order it recorded as not yet final with the broker, and look up at the broker any approval from today that has no recorded outcome, recording any order found there, before deciding whether to submit.
- **FR-010**: Execution MUST follow every order it placed to a final state (filled, rejected, or cancelled/expired, with any partial fill) and record the broker-confirmed status, filled quantity, and average fill price on the order.
- **FR-011**: Execution MUST update held positions from broker-confirmed fills only: a buy fill increases the quantity and recomputes the weighted average entry price; a sell fill reduces the quantity and leaves the average entry price unchanged; a position reduced to zero is removed. Where the resulting positions disagree with the broker's, Execution MUST log the discrepancy and adopt the broker's figures.
- **FR-012**: A broker rejection MUST be recorded on the order with the broker's reason and MUST NOT be retried automatically that trading day.
- **FR-013**: Execution MUST refuse to start unless both of these pass, before it processes anything: (a) the broker address it uses is the paper-trading address fixed in code, and any broker address supplied by configuration that differs from it stops startup; (b) a read-only account check at that fixed paper address succeeds with the configured credentials. The broker documents no account field that marks an account as paper; paper keys differ from live keys and the paper address serves only paper accounts, so a successful authenticated read there is the confirmation (research E2). If the account check cannot be completed (e.g. broker unreachable), Execution MUST NOT start. The guard MUST make no call that could place or change an order.
- **FR-014**: During market hours, every 30 minutes, Execution MUST fetch the last traded price of every held position and, for each at or below `stop_loss_pct` under its average entry price, record a stop-loss trigger with the observed price and time. Recording the trigger is the hand-off: the Risk Gate evaluates recorded triggers in its own process, with its own credential, and Execution never holds the gate's credential. Execution MUST submit an exit only for an approved verdict, through the same path as FR-006, picking it up like any other approval. It MUST NOT record a new trigger for a symbol that already has an exit order open, a trigger not yet evaluated, or an approved exit not yet submitted. A 30-minute window counts as checked only if the price check for every held position succeeded. While the risk configuration fails to load, the monitor cannot run; Execution MUST log this at error level on every cycle, because stop-loss protection is off until it is fixed.
- **FR-015**: On every trading day, before the open, Execution MUST record an account snapshot of equity, cash and buying power fetched from the broker, retrying until the open if the broker is unreachable. It MUST NOT record a snapshot it did not fetch, and MUST record none on non-trading days.
- **FR-016**: Execution MUST be deterministic in its decisions: given the same approval, the same broker responses, the same risk configuration and the same time, it MUST reach the same outcome. It MUST make no model call.
- **FR-017**: Execution MUST be the only component holding the broker credential, and MUST hold no other external credential. Its database access MUST be limited to what this spec names (reading approvals, positions, the manual pause flag and its own records; writing orders, positions, account snapshots, stop-loss triggers, and refusals), enforced by its database role.
- **FR-018**: Immediately before submitting any approved buy, Execution MUST read the manual pause flag and, if trading is paused, refuse the buy with the final reason "trading paused". Approved sells and stop-loss exits MUST still be submitted while paused. A buy refused this way is not resubmitted if trading resumes; the next Portfolio Manager run decides again.
- **FR-019**: Every behaviour in this spec MUST be testable, and tested, against a fake broker with no network access. No automated test may call the real broker, including the paper account.
- **FR-020**: If the risk configuration is missing or invalid, Execution MUST submit no buy (the same fail-closed rule as the gate); sells and stop-loss exits that the gate approved MUST still be submitted.

### Key Entities

- **Approved verdict** (existing, features 001/002): the gate's approved order: symbol, side, whole-share quantity, order type, price ceiling for buys, and the trading day it's valid for. From either a Portfolio Manager decision or a stop-loss trigger.
- **Order** (existing, feature 001): one per approval Execution submitted, with its deterministic identifier, the broker's own id, status, filled quantity and average fill price.
- **Execution refusal** (new): an approval Execution declined to submit, with the named reason and the live numbers it saw. At most one per approval, and never alongside an order for the same approval.
- **Position** (existing): symbol, quantity, average entry price. Written only by Execution, from confirmed fills.
- **Account snapshot** (existing): equity, cash and buying power at a moment, fetched from the broker. Recorded before each open and before each buy.
- **Stop-loss trigger** (existing, feature 002): a monitor observation of a held position at or below its stop-loss line: symbol, observed price, time.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Across the test suite, 100% of broker orders correspond to an approved verdict from the same trading day, placed while the market was open; zero orders exist for rejected, missing, or earlier-day verdicts.
- **SC-002**: For every crash point simulated in tests, each approval ends with exactly one broker order or one recorded refusal, never two orders.
- **SC-003**: 100% of approved buys whose live numbers fail any check in FR-003 to FR-005 are refused with the correct named reason, and 0% of approved sells or stop-loss exits are blocked by the daily-loss check.
- **SC-004**: Across at least 10,000 randomly generated live-account states, no submitted buy would leave a position above the ceiling or cash below the reserve at the live limit price.
- **SC-005**: Every held position is checked against the stop-loss line at least once in every 30-minute window the market is open while Execution is running.
- **SC-006**: A pre-open snapshot is recorded on every trading day on which the broker is reachable before the open, and on no other day.
- **SC-007**: After every fill in tests, recorded positions equal the broker's positions.
- **SC-008**: 100% of startup attempts pointed at anything other than the paper environment are refused; the test suite makes zero calls to a real broker.

## Assumptions

- **How Execution is invoked**: the orchestrator (a later feature) calls Execution after the gate approves a Portfolio Manager decision. Execution also picks up, on each of its own cycles, any approval from today with no recorded outcome, so a missed hand-off or a restart never strands a valid approval.
- **Live asking price for buys**: "the live quote" in `docs/specs/execution.md` means the current ask, the price a buyer pays. A quote at or under the ceiling is submitted at that price, not at the ceiling.
- **Limits come from the same reviewed configuration file the gate reads** (`config/risk.yaml`). Execution reads it; it never writes it.
- **Baseline**: Execution derives today's daily-loss baseline itself from account snapshots, with the same definition as the gate (the last snapshot on today's date before the open), rather than reading the gate's stored copy. The two can't disagree because they read the same snapshot.
- **Pre-open snapshot timing**: during the hour before the open. Any snapshot taken on the day before the open qualifies as the baseline.
- **Submissions are serialized**: Execution processes one submission at a time, so its own re-derivation (FR-005) sees every order it has already placed.
- **The broker is the record of what's held**: positions are maintained from fills, and reconciled to the broker where they disagree (FR-011).
- **Stop-loss monitor timing**: the first check runs shortly after the open and then every 30 minutes until the close, following the exchange calendar (early closes included). Prices are the last traded price for each symbol (Clarifications).
- **Not in scope**: deciding direction or size (Portfolio Manager), judging whether a trade is allowed (Risk Gate), order-splitting or price improvement, orders held across sessions, broker-held stop orders (rejected in ADR 0010), the reference-data job, the orchestrator's scheduling of the Portfolio Manager, and giving the Portfolio Manager or Opportunistic Identifier a price source (a separate open issue: broker keys that can read prices can also trade, which would break Principle I if shared).
- **Dependencies**: features 001 (data model, roles) and 002 (the gate's `evaluate_stop_loss_trigger` and approved verdicts). Until the reference-data job exists, the gate rejects every buy, so in practice only sells and stop-loss exits can occur. That is the intended fail-closed behaviour.
- **Database changes** this feature is expected to need (settled in the plan): storage for execution refusals, the new order identifier format (FR-008; an ADR superseding 001's research note R11 comes first, then a migration), a read of the manual pause flag (FR-018), and matching grants for `ta_execution` in `specs/001-data-model/contracts/role-grants.md`.
