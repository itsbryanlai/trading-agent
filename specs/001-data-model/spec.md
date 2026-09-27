# Feature Specification: Shared Data Model

**Feature Branch**: `001-data-model`

**Created**: 2026-09-26

**Status**: Draft

**Input**: User description: "Shared Postgres data model for the trading-agent system: the tables that make up the single shared knowledge base every agent and service reads from and writes to with its own role-scoped credential. Source of truth: docs/specs/data-model.md — reports, decisions, risk_verdicts, orders, positions, journal, system_state, each with a named sole writer and broad default read access, enforced by database role, not application code."

## Clarifications

### Session 2026-09-27

- Q: How should a report's `status` (open → expired/consumed/rejected) actually get updated, given that only the Research and Opportunistic Identifier roles have write access to `reports`? → A: `status` is not a stored, separately-written value. `open` vs `expired` is computed at read time from `expires_at` versus now; "consumed" is computed by checking whether any `decisions` row references the report's id. No additional write grant is needed on `reports` beyond the initial insert.
- Q: Does a "rejected" state ever apply to a report itself, separate from the "rejected" verdict a decision can get from the Risk Gate? → A: No — dropped entirely. A report is only ever open, expired, or consumed, all computed; nothing in this system produces a report-level rejection. The Risk Gate rejects decisions, not the reports that fed them.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Analyst reports are recorded, each writer confined to its own rows (Priority: P1)

Research and the Opportunistic Identifier each run on their own schedule and record what they found — including a run that found nothing — as a durable row the Portfolio Manager can later read. Neither agent can write a row attributed to the other.

**Why this priority**: Nothing downstream (decisions, attribution, the journal) exists without a durable, correctly-attributed record of what each analyst produced. This is the first thing that must work.

**Independent Test**: Connect as the Research role and insert a report row; connect as the Opportunistic Identifier role and insert a different report row; verify each role's connection is rejected when it attempts to insert a row attributed to the other agent. Verify a "found nothing" run persists a row rather than being silently skipped.

**Acceptance Scenarios**:

1. **Given** the Research role's credential, **When** it records a report with a symbol, direction, conviction, sources, and rationale, **Then** the row is stored and readable by every role whose component spec reads reports (see `contracts/role-grants.md`), and by no other.
2. **Given** the Research role's credential, **When** it attempts to insert a row attributed to the Opportunistic Identifier, **Then** the write is rejected.
3. **Given** a scheduled run that finds no actionable opportunity, **When** the agent completes that run, **Then** a row recording "no action" is still stored, distinguishable from the agent simply not having run.
4. **Given** a report whose trading day has ended, **When** any reader queries open reports, **Then** the expired report is excluded without needing to be deleted.

---

### User Story 2 - A decision is traceable end-to-end to the order it produced (Priority: P1)

The Portfolio Manager records a decision citing the report(s) it drew on; the decision is evaluated and the verdict recorded; an approved verdict results in an order. Anyone reading the system afterward can walk from one order back to the exact decision and reports that led to it.

**Why this priority**: Auditability of the trading path (decision → verdict → order) is the core safety property of the whole system — it must exist before any component that produces these rows is built.

**Independent Test**: Insert a decision row citing report ids; insert a risk-verdict row referencing that decision; insert an order row referencing that verdict; verify each row can be joined back through the chain to the original report(s), and that only the Portfolio Manager role can write decisions, only the Risk Gate role can write verdicts, and only the Execution role can write orders.

**Acceptance Scenarios**:

1. **Given** two open reports on the same symbol from different agents, **When** the Portfolio Manager records a decision, **Then** the decision stores both report ids, enabling later attribution to each agent.
2. **Given** a decision, **When** the Risk Gate evaluates it, **Then** exactly one verdict row is stored referencing that decision, carrying either an approved fully-specified order or a named rejection rule.
3. **Given** an approved verdict, **When** Execution submits the order, **Then** the order row references that verdict, and its identifier is derived deterministically from trading day, symbol, and side rather than generated fresh each time.
4. **Given** a process restart after an order was submitted but before its outcome was recorded, **When** the same trading day/symbol/side combination is submitted again, **Then** the store recognizes it as the same order rather than creating a duplicate.
5. **Given** any role other than Risk Gate, **When** it attempts to write a verdict row, **Then** the write is rejected; the same holds for any role other than Execution attempting to write an order row.

---

### User Story 3 - Current holdings and daily performance are always derivable, with per-agent attribution (Priority: P2)

Positions reflect confirmed fills. Once a trading day closes, a journal entry summarizes the day and computes what the portfolio would look like if sized purely off each analyst agent's reports — separately from the actual blended result — so the agents' individual performance can be tracked over time.

**Why this priority**: This is the system's stated purpose (observe how each agent performs over time) but depends on User Stories 1 and 2 already existing to have anything to summarize.

**Independent Test**: Record a filled order, verify the position table reflects it; end a trading day, verify a journal row is created with both the actual equity change and a computed per-agent attribution figure, and verify the Risk Gate's and Execution's own read paths never include the journal.

**Acceptance Scenarios**:

1. **Given** a confirmed order fill, **When** the fill is recorded, **Then** the corresponding position row is updated to reflect the new holding, and only the Execution role can make that update.
2. **Given** a completed trading day, **When** the day's journal entry is written, **Then** it includes a per-agent attribution figure computed from that day's decisions and reports, distinct from the actual portfolio result.
3. **Given** a journal entry exists, **When** the Risk Gate or Execution evaluates anything, **Then** neither reads the journal — attribution is measurement only, never a feedback input.

---

### User Story 4 - Trading can be paused, and a daily-loss halt clears itself automatically (Priority: P2)

A single manual toggle can pause trading; the Risk Gate can set an automatic halt when a day's losses cross the configured threshold, and that halt clears itself at the start of the next trading day without a manual step.

**Why this priority**: This is the one piece of shared state that isn't a record of something that already happened — it's a live control input the orchestrator and Risk Gate must check. Needed before the orchestrator or Risk Gate can be built, but depends on nothing else here.

**Independent Test**: Set the manual pause flag and verify it's readable by the orchestrator's role; record a daily-loss halt as the Risk Gate role and verify a UI-facing role cannot record one; with the halt recorded against the previous trading day, verify the halt and the starting-equity baseline both read as inactive/unset without any write from a human or agent.

**Acceptance Scenarios**:

1. **Given** the manual pause flag is set to true, **When** any role reads system state, **Then** the flag reads true until explicitly toggled back.
2. **Given** the Risk Gate detects the daily-loss threshold has been crossed, **When** it records the halt against the current trading day, **Then** the halt reads active for the rest of that day, and no other role can record or alter a halt.
3. **Given** a halt fired on the previous trading day, **When** any role reads system state on the next trading day, **Then** the halt reads inactive and the previous day's starting-equity baseline reads as unset — with no write by any human, agent, or service — until the Risk Gate records that day's baseline.

### Edge Cases

- What happens when two roles attempt to write to the same table concurrently (e.g., two Risk Gate evaluations racing on the same decision)? Each decision must produce exactly one verdict; a second evaluation of an already-verdicted decision must not create a duplicate verdict row.
- How does the system handle a report whose `expires_at` has already passed by the time a reader queries it mid-transaction? Expiry is a computed property of the read (comparing `expires_at` to current time), not a background job that must run first — a query must never depend on a cleanup process having already executed.
- What happens if a role's credential is used to attempt a write to a table it has no grant on at all (not just the wrong row, but an entirely unrelated table)? The write must be rejected at the same enforcement point (the database grant) as a wrong-row write within an owned table.
- What happens when an order references a `risk_verdicts` row that was somehow never approved? This must be structurally prevented (or rejected) rather than merely discouraged by convention.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a durable store for analyst reports (Research and Opportunistic Identifier), each row attributed to exactly one agent, including runs that produced no actionable finding.
- **FR-002**: The system MUST restrict write access to report rows such that each analyst role can only write rows attributed to itself, enforced at the storage layer rather than by the writing component's own logic. No role, including the Portfolio Manager, requires any write access to `reports` beyond that initial insert.
- **FR-003**: The system MUST make a report's open/expired state derivable at read time by comparing its expiry (end of the trading day it was generated on) to the current time, and its consumed state derivable by whether any decision references it — neither is a value written after the report's initial insert.
- **FR-004**: The system MUST provide a durable store for Portfolio Manager decisions, each referencing the report(s) it drew on, its own reasoning, and the quote it fetched at decision time.
- **FR-005**: The system MUST restrict write access to decision rows to the Portfolio Manager role alone.
- **FR-006**: The system MUST provide a durable store for risk verdicts, each referencing exactly one decision, recording either an approved fully-specified order or the specific rule that caused rejection.
- **FR-007**: The system MUST restrict write access to risk-verdict rows to the Risk Gate role alone, and that role MUST have no credential granting it broker or market-data access.
- **FR-008**: The system MUST provide a durable store for submitted orders, each referencing exactly one approved risk verdict, with an identifier derived deterministically from trading day, symbol, and side so that a repeated submission for the same combination is recognized rather than duplicated.
- **FR-009**: The system MUST restrict write access to order rows, and to the sole broker-facing credential in the system, to the Execution role alone.
- **FR-010**: The system MUST maintain current position holdings, updated only from confirmed order fills, writable only by the Execution role.
- **FR-011**: The system MUST maintain a daily journal entry per trading day, including a per-agent attribution figure computed separately from the actual portfolio result, writable only by a dedicated journal-writing role.
- **FR-012**: The system MUST ensure the journal is never read by the Risk Gate or Execution, so that attribution measurement cannot become a feedback input to trading decisions.
- **FR-013**: The system MUST maintain a small set of shared control state: a manual trading-pause flag, and the daily-loss halt state with its supporting starting-equity baseline.
- **FR-014**: The system MUST restrict recording the daily-loss halt and its baseline to the Risk Gate role, and restrict write access to the manual pause flag to a UI-facing role, kept distinct from each other.
- **FR-015**: The system MUST make the daily-loss halt and the starting-equity baseline read as inactive/unset from the start of each new trading day, without any write — neither needs to be reset by anyone.
- **FR-016**: The system MUST grant read access broadly by default, narrowed wherever a component's own spec (`docs/specs/*.md`) disclaims reading something — a role is never granted read access its component's spec says it doesn't use.
- **FR-017**: The system MUST enforce every write restriction above at the storage layer (e.g., database-level grants), not solely through the writing component's own code choosing to behave.
- **FR-018**: The system MUST maintain a durable record of broker account state (equity, cash, buying power) over time, writable only by the Execution role, so that components without broker access (the Portfolio Manager, Risk Gate, and journal) can read cash and equity without holding a broker credential.

### Key Entities

- **Report**: One analyst-agent run's finding (or non-finding). Attributed to exactly one agent; carries a direction, a conviction level, suggested sizing, structured sources, a narrative rationale, and an expiry tied to the trading day it was generated on. Its open/expired/consumed state is derived at read time, never stored as a separately-written value (see Clarifications).
- **Decision**: One Portfolio Manager judgment on a symbol. References the report(s) that informed it, carries the PM's own direction and size, its reasoning, and the quote it fetched independently.
- **Risk Verdict**: One evaluation of a decision against the current risk configuration and state. References exactly one decision; carries either an approved, fully-specified order or a named rejection reason.
- **Order**: One submission to the broker. References exactly one approved risk verdict; carries a deterministic identifier and its fill/status lifecycle.
- **Position**: Current holding in one symbol, derived from confirmed order fills.
- **Account Snapshot**: Broker account equity, cash, and buying power at a point in time, recorded by Execution for every component that must reason about cash or equity without broker access.
- **Journal Entry**: One trading day's narrative summary and computed per-agent attribution, distinct from the actual portfolio result.
- **System State**: A small set of named control flags and baselines governing pause/halt behavior. The daily-loss halt is recorded as the trading day it fired, so "halt active" is derived at read time and needs no reset write.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every order in the system can be traced back, through exactly one chain of references, to the risk verdict, decision, and report(s) that produced it, with no broken or ambiguous links.
- **SC-002**: A write attempted by a role outside what it's permitted to write is rejected before it reaches storage, 100% of the time, regardless of what the writing component's own code intended.
- **SC-003**: The journal holds at most one entry per trading day, and re-running a day's journal write replaces that day's entry rather than duplicating it or failing. (That an entry is written for *every* trading day with a decision is a property of the journal writer, verified by that feature.)
- **SC-004**: A restarted process that re-submits an order for the same trading day, symbol, and side is recognized as a duplicate rather than creating a second position, in 100% of observed restart scenarios.
- **SC-005**: On every new trading day, a halt recorded on a previous day reads inactive and a previous day's baseline reads unset, with zero writes by any human, agent, or service.

## Assumptions

- One shared relational database instance serves as the entire knowledge base; there is no per-agent database.
- "Trading day" boundaries follow the exchange calendar already assumed elsewhere in this project (a day the market is open, in the exchange's local time).
- Each role's distinct credential is provisioned and rotated as an operational/deployment concern outside this feature's scope; this feature defines what each role may read and write, not how the credential itself is issued.
- `config/risk.yaml` (the Risk Gate's configuration) is a separate artifact from this data model and is out of scope here, referenced only insofar as risk-verdict rows record its outcome.
- A UI-facing role's ability to toggle the manual pause flag is in scope here as a data-access rule; the dashboard surface that calls it is a separate feature.
- This feature proves what the storage layer enforces: who may read and write what, the constraints on each row, and the computed views. Clauses describing another component's runtime behavior are verified by that component's feature, not here: which component holds broker or market-data credentials (FR-007, FR-009 → Execution, Risk Gate), that positions change only from confirmed fills (FR-010 → Execution), how per-agent attribution is calculated (FR-011 → journal), the broker rejecting a duplicate `client_order_id` (SC-004 → Execution), and the Risk Gate recording each day's baseline on its first evaluation (SC-005 → Risk Gate).
