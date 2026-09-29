# Feature Specification: Universe Reference-Data Job

**Feature Branch**: `004-reference-data`

**Created**: 2026-09-29

**Status**: Draft

**Input**: User description: "Feature 004: universe reference-data job (ADR 0010 decision 3, ADR 0013). A deterministic daily job, its own component with the existing ta_reference_data role and instrument_reference table (migration 0006), that uses a read-only Finnhub key (never a trading credential) to record, per symbol, listing/security type, exchange MIC, market cap, average daily dollar volume, and share price, so the Risk Gate's universe check can pass buys. Decisions from the owner: (1) Symbol set = symbols the system actually touches: currently held positions, symbols named in recent analyst reports and PM decisions, plus a small configured seed list. (2) Runs as its own loop per ADR 0013: main run in a pre-open window (~08:00–09:15 ET) on XNYS trading days, plus intraday retries for symbols missed or newly named. (3) Failure is fail-closed per symbol: a failed fetch writes no row for today (gate rejects buys of it), never falls back to yesterday's data, logs, and retries later; sells are never affected. Open item: whether Finnhub's free tier provides average daily dollar volume (historical candles may be premium) is being researched in parallel; mark as needing clarification rather than assuming."

## User Scenarios & Testing *(mandatory)*

The "users" of this job are the system's owner, who wants buys to be possible only in symbols that verifiably meet the universe rules in `config/risk.yaml`, and the Risk Gate, which judges every buy against today's reference data for its symbol and rejects the buy (`universe_no_reference_data`) when there is none ([ADR 0010](../../docs/adr/0010-stop-loss-monitor-and-universe-reference-data.md)). The Assistant and dashboard read the same data to explain rejections.

The job is deterministic code, not an LLM ([ADR 0005](../../docs/adr/0005-risk-gate-and-execution-are-deterministic.md) in spirit: it feeds the gate). It records facts; it never judges whether a symbol passes the universe rules. That judgement stays with the gate, which reads the thresholds from `config/risk.yaml`. The job holds one market-data credential that cannot trade, and its own database login. It never holds or reads a broker credential, and it never places, changes or cancels an order.

### User Story 1 - Today's reference data is in place before the open (Priority: P1)

On each XNYS trading day, before the market opens, the job works out which symbols the system cares about today and records, for each one, its security type, exchange, market cap, average daily dollar volume and share price, stamped with today's trading day. When the Portfolio Manager later decides to buy one of those symbols, the gate finds today's row and judges the buy on its merits instead of rejecting it for missing data.

**Why this priority**: Until this exists, every buy is rejected. This one story is what makes the system able to buy at all.

**Independent Test**: With a fake market-data provider and a database holding a few positions, reports and seed symbols, run the job in the pre-open window of a trading day; each symbol gets exactly one row for that day with the provider's values, normalized; a gate evaluation of a buy for one of them no longer returns `universe_no_reference_data`.

**Acceptance Scenarios**:

1. **Given** a trading day, the pre-open window, and a symbol set of held positions, recently named symbols and seed symbols, **When** the job runs, **Then** every symbol whose data was fetched completely has one row for today, and no symbol outside the set gets a row.
2. **Given** a day that is not an XNYS trading day (weekend or holiday), **When** the loop ticks, **Then** it fetches nothing and writes nothing.
3. **Given** today's rows already exist for every symbol in the set, **When** the job runs again (restart, doubled tick), **Then** it fetches nothing new and rows are unchanged.

---

### User Story 2 - A failed or implausible fetch fails closed for that symbol only (Priority: P1)

The market-data provider fails for one symbol (error, timeout, rate limit, missing field, or a value that can't be right, such as a zero or negative price). The job writes no row for that symbol today, never copies yesterday's row forward, logs the failure with the symbol and reason, and tries that symbol again later in the day. Every other symbol is unaffected. Sells and stop-loss exits never depend on reference data, so nothing about a failure here can block an exit.

**Why this priority**: A wrong or stale row could let the gate approve a buy in a symbol that doesn't meet the universe rules. Missing data costs only a skipped buy; wrong data costs real exposure. This is the safety half of Story 1.

**Independent Test**: With a fake provider that fails, times out, or returns incomplete or out-of-range data for chosen symbols, run the job; those symbols have no row for today (even when yesterday's row exists), the others do, and a later run once the provider recovers fills them in.

**Acceptance Scenarios**:

1. **Given** yesterday's row for a symbol and a failing provider today, **When** the job runs, **Then** there is no row for today and the gate rejects a buy with `universe_no_reference_data`.
2. **Given** the provider returns every field but one for a symbol, **When** the job runs, **Then** no row is written for it.
3. **Given** the provider is unreachable for the whole pre-open run, **When** it recovers during the day, **Then** the job's next retry fills in the missing symbols.
4. **Given** the provider rate-limits the job, **When** the job runs, **Then** it slows down and continues rather than giving up on the remaining symbols.

---

### User Story 3 - A symbol named during the day gets data the same day (Priority: P2)

During market hours an analyst report names a symbol that wasn't in the morning's set. The job notices it on its next check and fetches its reference data, so a Portfolio Manager decision on that symbol later in the day can pass the universe check.

**Why this priority**: Without it, any idea that surfaces intraday can't be bought until tomorrow. The system still works safely without it, so it follows Stories 1 and 2.

**Independent Test**: After the morning run, insert a report naming a new symbol, run the loop's next intraday check; that symbol now has a row for today, and previously fetched symbols are not fetched again.

**Acceptance Scenarios**:

1. **Given** a report naming a new symbol is written during the trading day, **When** the job next checks, **Then** the symbol has a row for today within the pick-up time in SC-003.
2. **Given** a symbol that already has today's row, **When** it is named again, **Then** it is not fetched again.
3. **Given** the trading session has closed, **When** a report names a new symbol, **Then** the job does not fetch it until the next trading day's run.

---

### User Story 4 - The owner can see how the day's run went (Priority: P3)

The owner (through logs now; the Assistant and dashboard later) can see which symbols got today's data and which didn't and why, so a buy rejected for missing data can be explained.

**Why this priority**: Operational visibility. Rows already show what succeeded; this adds the why for what didn't.

**Independent Test**: Run the job with a mix of successful and failing symbols; each run's log says how many symbols were in the set, how many were recorded, and for each failure the symbol and reason.

**Acceptance Scenarios**:

1. **Given** a run with failures, **When** it finishes, **Then** a summary log line gives the counts and each failed symbol has its own log line with its reason.
2. **Given** a pre-open run in which some symbols still have no row when the market opens, **When** the market opens, **Then** the job logs a warning naming them.

---

### Edge Cases

- **Held position, no data**: a held symbol with no row can still be sold and stop-loss exited; only buys need reference data. The job still tries to fetch it because a PM may want to add to it.
- **Symbol the provider doesn't know** (delisted, renamed, typo in a report): treated like any failure; no row, logged, retried at the normal cadence rather than every tick.
- **Malformed symbol** in a report or decision (`decisions.symbol` has no format check): the job skips symbols that don't look like a US ticker, logs them, and never sends them to the provider.
- **Exchange segment codes**: the provider may report a market segment (for example a Nasdaq tier) rather than the exchange itself. The job records the exchange-level code the gate expects (XNYS, XNAS or XASE) for known segments; anything it can't map is recorded as reported, so the gate's listing check rejects it.
- **Security types**: anything the provider doesn't clearly identify as common stock, ETF or ADR is recorded as `other`, so the listing check rejects it. Uncertainty never becomes `common_stock`.
- **Mixed units from the provider** (for example market cap in millions): the job converts to US dollars before recording; the stored values are always whole US-dollar amounts as the gate reads them.
- **Pre-open share price** is necessarily the previous session's closing or last price, not today's; the gate's share-price floor is a coarse filter and this is acceptable.
- **Early-close days and half days** are ordinary trading days. The pre-open window is the same; the intraday pick-up stops at that day's close.
- **Job down all morning**: if the job starts after the window, it runs the full set immediately on a trading day (catch-up) rather than waiting for tomorrow.
- **Two job processes at once** (a redeploy overlap): only one runs; the second waits or exits, as Execution's single-instance lock does.
- **Clock at midnight / trading-day boundary**: the trading day is always taken from the shared exchange calendar in ET, never from the host's local date.
- **Very large symbol set** (many reports): the run respects the provider's rate limit and still finishes within the pre-open window for the expected set size (SC-002).
- **Race with the PM**: a report naming a new symbol can lead to a PM decision before the job's next check. The gate then rejects the buy for missing data. This fails closed; the next PM run can try again. See Assumptions.

## Requirements *(mandatory)*

### Functional Requirements

**Symbol set**

- **FR-001**: The job MUST build each trading day's symbol set as the union of: (a) symbols currently held in positions; (b) symbols named in analyst reports that are still active (not yet expired) or were written since the previous trading day's open; (c) symbols named in Portfolio Manager decisions since the previous trading day's open; (d) a seed list kept in version-controlled configuration.
- **FR-002**: The seed list MUST be changeable only through code review, like `config/risk.yaml`; no agent writes it. An empty seed list is valid.
- **FR-003**: The job MUST skip, and log, any symbol that isn't a plausible US ticker (upper-case letters, optionally with a single class separator), and never send it to the provider.

**Fetching and recording**

- **FR-004**: For each symbol in the set without a row for today, the job MUST fetch security type, exchange, market cap, average daily dollar volume and share price from the read-only market-data provider, and record them as one row for today's trading day.
- **FR-005**: The job MUST write a row only when all five values were obtained and pass sanity checks (share price above zero, market cap and dollar volume not negative, exchange and type present). Otherwise it MUST write nothing for that symbol today.
- **FR-006**: The job MUST NOT copy, carry forward or fall back to a row from an earlier trading day, and MUST NOT write a row for any trading day other than today's.
- **FR-007**: The job MUST normalize security type to exactly one of `common_stock`, `etf`, `adr`, `other`, mapping anything not clearly one of the first three to `other`.
- **FR-008**: The job MUST record the exchange as an ISO 10383 operating-exchange code, mapping known market segments to their exchange (for example Nasdaq tiers to XNAS), and record any unmapped code unchanged.
- **FR-009**: The job MUST convert all monetary values to US dollars in the units the gate compares against `config/risk.yaml`.
- **FR-010**: Average daily dollar volume MUST be computed over [NEEDS CLARIFICATION: which window and from which source? The read-only provider's free tier may not serve daily price/volume history. Options: the provider's own average-volume figure × price, a paid provider tier, or another read-only source — pending the parallel research; a source change needs an ADR].
- **FR-011**: Once a symbol has a row for today, the job MUST NOT fetch it again that day, and MUST NOT change that row.

**Schedule and retries**

- **FR-012**: The job MUST run in its own process with its own loop ([ADR 0013](../../docs/adr/0013-deterministic-services-run-their-own-loops.md)), taking every time judgement (trading day, open, close, early closes) from the shared exchange calendar.
- **FR-013**: On an XNYS trading day, the main run MUST start at the beginning of a pre-open window (08:00 ET) and aim to finish before 09:15 ET. If the job starts later in the day, it MUST run the full set at once.
- **FR-014**: Between the main run and that day's close, the job MUST periodically rebuild the symbol set, fetch symbols newly in it, and retry symbols that failed, backing off per symbol so a persistently failing symbol is not retried every check.
- **FR-015**: The job MUST do nothing on days that are not XNYS trading days, and MUST NOT fetch after the day's close.
- **FR-016**: The job MUST stay within the provider's rate limit, slowing down on a rate-limit response rather than abandoning the remaining symbols.
- **FR-017**: Only one instance of the job MUST run at a time.
- **FR-018**: Running the job twice for the same day MUST produce the same rows as running it once (idempotent).
- **FR-019**: On a lost database connection the process MUST exit so the platform restarts it (ADR 0013 §5). Provider failures MUST NOT stop the process.

**Credentials and access**

- **FR-020**: The job MUST hold only its own database login (`ta_reference_data`) and the read-only market-data key. It MUST NOT hold or read any broker credential or any other component's database login.
- **FR-021**: The job's database role MUST be able to insert and read `instrument_reference`, and to read only the columns it needs to build the symbol set (the symbol and time columns of positions, reports and decisions). Nothing else. This amends `specs/001-data-model/contracts/role-grants.md`, and the grants-matrix test MUST match the database both ways.
- **FR-022**: The job MUST NOT write to any table other than `instrument_reference`, and MUST NOT delete rows from it.
- **FR-023**: The job MUST never log the market-data key or the database connection string.

**Visibility**

- **FR-024**: Each run MUST log the size of the symbol set, the number of symbols recorded, and one line per failed or skipped symbol with its reason.
- **FR-025**: At the market open, the job MUST log a warning naming any symbol in the set that still has no row for today.

**Tests**

- **FR-026**: No test may call the real market-data provider; tests use a fake provider (the suite already blocks non-local connections).

### Key Entities

- **Instrument reference row** (existing `instrument_reference`, migration 0006): one symbol on one trading day — security type, exchange code, market cap, average daily dollar volume, share price, and when it was fetched. Written only by this job; read by the Risk Gate, Assistant and dashboard. Kept as history; never deleted or rewritten.
- **Symbol set**: the day's list of symbols to fetch, derived each time from positions, reports, decisions and the seed list. Not stored.
- **Seed list**: a short, version-controlled list of symbols to fetch every trading day regardless of activity.
- **Market-data provider**: an external, read-only source that cannot trade. Its key belongs to this job alone.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a trading day with the provider healthy, 100% of symbols in the set that the provider knows have a row for today before the 09:30 ET open.
- **SC-002**: A symbol set of up to 200 symbols completes within the 08:00–09:15 ET window under the provider's free-tier rate limit.
- **SC-003**: A symbol newly named during market hours has today's row within 5 minutes, when the provider is healthy.
- **SC-004**: Zero rows are ever written for a trading day other than today's, and zero rows are carried forward from an earlier day (verified by tests over failing-provider scenarios).
- **SC-005**: Zero rows are written with a missing, zero or negative required value, or with a security type outside the four allowed values.
- **SC-006**: A provider outage of any length blocks no sell or stop-loss exit; buys in affected symbols are rejected for missing data, and nothing else changes.
- **SC-007**: The job's database role can do nothing beyond FR-021 and FR-022, verified by the grants-matrix test against the database's own permission records.

## Assumptions

- The read-only market-data provider is Finnhub, per [ADR 0008](../../docs/adr/0008-dashboard-stack-and-research-provider.md) and [ADR 0010](../../docs/adr/0010-stop-loss-monitor-and-universe-reference-data.md). Which of its endpoints serve which fields on the free tier is being researched in parallel; that result feeds FR-010 and `/speckit-plan`, not this spec.
- "Recently named" means reports still active or written since the previous trading day's open, and decisions since the previous trading day's open. This covers the next-morning case (yesterday's ideas get data today) and the intraday case, while keeping the set small.
- The expected symbol set is tens of symbols, up to about 200. A larger set is out of scope for the free-tier rate limit.
- Intraday pick-up checks every few minutes (fast enough for SC-003). The exact interval is a plan detail.
- The PM may decide on a newly reported symbol before the job has fetched it; the gate then rejects the buy for missing data. This fails closed. The orchestrator (feature 005) could reduce it by starting event-driven PM runs a few minutes after the report; that is feature 005's decision, noted here only as a dependency.
- A symbol's row is fetched once per day and not refreshed intraday. The gate's thresholds are coarse (market cap, liquidity, $5 price floor), so pre-open values are good enough for the day.
- Deployment config (Railway) is not part of this feature, as for 003; the job must be startable as its own process with only its own environment variables, documented for whichever feature first writes deployment config.
- Out of scope: judging universe eligibility (the gate does), changing universe thresholds, fetching quotes for trading, a UI for the seed list, and backfilling past days.
