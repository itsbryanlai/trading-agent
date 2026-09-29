# Feature Specification: Universe Reference-Data Job

**Feature Branch**: `004-reference-data`

**Created**: 2026-09-29

**Status**: Draft

**Input**: User description: "Feature 004: universe reference-data job (ADR 0010 decision 3, ADR 0013). A deterministic daily job, its own component with the existing ta_reference_data role and instrument_reference table (migration 0006), that uses a read-only Finnhub key (never a trading credential) to record, per symbol, listing/security type, exchange MIC, market cap, average daily dollar volume, and share price, so the Risk Gate's universe check can pass buys. Decisions from the owner: (1) Symbol set = symbols the system actually touches: currently held positions, symbols named in recent analyst reports and PM decisions, plus a small configured seed list. (2) Runs as its own loop per ADR 0013: main run in a pre-open window (~08:00–09:15 ET) on XNYS trading days, plus intraday retries for symbols missed or newly named. (3) Failure is fail-closed per symbol: a failed fetch writes no row for today (gate rejects buys of it), never falls back to yesterday's data, logs, and retries later; sells are never affected. Open item: whether Finnhub's free tier provides average daily dollar volume (historical candles may be premium) is being researched in parallel; mark as needing clarification rather than assuming."

## Clarifications

### Session 2026-09-29

- Q: Where does average daily dollar volume come from, given the read-only provider's free tier doesn't serve daily price history? → A: The provider's free 10-day average trading volume × the previous session's closing price. Same read-only key, so it stays within ADR 0010 and needs no new ADR. The volume figure's unit (millions of shares) is known only from the provider's sample response, so a plausibility check guards against a unit mistake: a row whose dollar volume exceeds its market cap is not written.
- Q: How should the job's database role find out which symbols to fetch? → A: Through one new read-only view that lists only the candidate symbols (held, recently reported, recently decided). The view runs with its owner's rights, so the job's role gets SELECT on that view alone and no access to positions, reports or decisions, and the `reports` row-level security policy is unchanged.
- Q: Which price is recorded as the share price? → A: Always the previous session's closing price, whenever the symbol is fetched. It is the same price used for dollar volume (FR-010), and a rerun records the same value.
- Q: What happens when the provider rejects the job's key itself? → A: At startup the job makes one read to check the key and exits if it's rejected, so a misconfigured deploy fails visibly. If the key is rejected later, the job logs one error-level line for that run naming the key rejection, skips the rest of the run, and stays up to try again at the next check.
- Q: Should the job's role keep the UPDATE permission on `instrument_reference` from migration 0006? → A: No. This feature's migration revokes it; the role can only read and insert, and inserting a row that already exists for that symbol and day is a no-op.

### Session 2026-09-29 (after `/speckit-analyze`)

- Q: Can a stored value be zero? → A: No. FR-005 is aligned with SC-005: price, market cap, average volume and dollar volume must all be above zero, because the provider uses 0 for "no data".
- Q: What catches a market cap reported in the wrong unit, which would otherwise pass every check? → A: A sanity ceiling: a market cap above $20 trillion (about four times the largest company) fails the symbol (`implausible_market_cap`). It is a data check, not a risk limit, and lives in code, not `config/risk.yaml`.

### Session 2026-09-30 (after the adversarial review)

- Q: How is the previous close known to be the previous session's, not an older one? → A: From the quote's own time. If it is on today's trading day (pre-market or in session), the quote has rolled over and its previous-close field is the previous session's close; if it is on the previous session's day, the quote hasn't rolled yet and its last price is that session's close. Any other time (missing, or older, e.g. a halted symbol) fails the symbol as `stale_quote`.
- Q: What if the provider reports market cap in another currency? → A: Fail closed (`non_usd_market_cap`) unless the profile's currency is USD. Foreign issuers reporting in other currencies can't be bought; that is the safe direction.
- Q: Does a 403 on one symbol mean the key is rejected? → A: No. Only a 401, or a 403 on the symbol list, is a key rejection. A 403 on one symbol's request fails that symbol (`not_permitted`) and backs it off; the other symbols carry on.
- Q: Should a provider outage or rate limit at startup stop the process? → A: No, only a rejected key does. Otherwise the key check is deferred to the first tick, which fetches the list itself; exiting would only restart the process into the same call. Amends FR-019a.
- Q: How are values rounded to the stored precision? → A: Down, never up, so rounding can't lift a value over a gate floor (e.g. $4.99995 is stored as $4.9999, not $5.0000).
- Q: What if the symbol list names a symbol twice with different types or exchanges? → A: Fail closed (`conflicting_listing`); which entry is right is unknowable.
- Q: The owner's live `--check` returned about 270 shares a day for BRK.B, which is BRK.A's volume. How are share-class tickers handled? → A: Fail closed (`share_class_unverified`) for any ticker with a class separator (`.` or `-`), before any provider call. The observed mix-up only rejected buys, but the reverse (a quiet class given its busy sibling's volume) would pass the liquidity floor wrongly, and the job can't tell which way a mix-up goes. Revisit if a reliable per-class source is chosen (that would need an ADR).

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
- **Key rejected by the provider**: see FR-019a. It is reported once per run, not once per symbol. A 403 on one symbol's request is that symbol's failure (`not_permitted`), not a key rejection.
- **Share-class tickers** (`BRK.B`, `BF-B`): fail closed (`share_class_unverified`) until a reliable per-class volume source exists, so they can't be bought.
- **Stale quote**: a halted or delisted symbol whose quote is older than the previous session fails as `stale_quote`, so a days-old price never passes the $5 floor.
- **Symbol text from reports**: a candidate that isn't a plain ticker is logged as a quoted repr, so a newline in an LLM-written symbol can't forge a log line.
- **Symbol the provider doesn't know** (delisted, renamed, typo in a report): treated like any failure; no row, logged, retried at the normal cadence rather than every tick.
- **Malformed symbol** in a report or decision (`decisions.symbol` has no format check): the job skips symbols that don't look like a US ticker, logs them, and never sends them to the provider.
- **Exchange segment codes**: the provider may report a market segment (for example a Nasdaq tier) rather than the exchange itself. The job records the exchange-level code the gate expects (XNYS, XNAS or XASE) for known segments; anything it can't map is recorded as reported, so the gate's listing check rejects it.
- **Security types**: anything the provider doesn't clearly identify as common stock, ETF or ADR is recorded as `other`, so the listing check rejects it. Uncertainty never becomes `common_stock`.
- **Mixed units from the provider** (for example market cap in millions): the job converts to US dollars before recording; the stored values are always whole US-dollar amounts as the gate reads them.
- **Share price is the previous close**, even for symbols fetched during the day, so it can differ from the live price. The gate's $5 floor is a coarse filter and this is acceptable; Execution checks the live price before any buy.
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

- **FR-004**: For each symbol in the set without a row for today, the job MUST fetch security type, exchange, market cap, average daily dollar volume and share price from the read-only market-data provider, and record them as one row for today's trading day. The share price MUST be the previous session's closing price, whether the symbol is fetched before the open or during the day, taken from the quote according to the quote's own time; a quote whose time is missing or older than the previous session MUST fail the symbol (Clarifications 2026-09-30).
- **FR-005**: The job MUST write a row only when all five values were obtained and pass sanity checks (share price, market cap, average volume and dollar volume all above zero; market cap no more than $20 trillion, a ceiling that catches a provider unit error; every value within what the stored columns can hold after rounding; exchange and type present; market cap reported in USD; the symbol listed exactly once, or consistently; not a share-class ticker). Values are rounded down to the stored precision. A zero from the provider means "no data", never a real value. Otherwise it MUST write nothing for that symbol today.
- **FR-006**: The job MUST NOT copy, carry forward or fall back to a row from an earlier trading day, and MUST NOT write a row for any trading day other than today's.
- **FR-007**: The job MUST normalize security type to exactly one of `common_stock`, `etf`, `adr`, `other`, mapping anything not clearly one of the first three to `other`.
- **FR-008**: The job MUST record the exchange as an ISO 10383 operating-exchange code, mapping known market segments to their exchange (for example Nasdaq tiers to XNAS), and record any unmapped code unchanged.
- **FR-009**: The job MUST convert all monetary values to US dollars in the units the gate compares against `config/risk.yaml`.
- **FR-010**: Average daily dollar volume MUST be the provider's 10-day average daily trading volume (converted to shares) multiplied by the previous session's closing price. If the result exceeds the symbol's market cap (more than 100% of the company traded per day, a sign of a unit error), the job MUST treat the fetch as failed and write no row.
- **FR-011**: Once a symbol has a row for today, the job MUST NOT fetch it again that day, and MUST NOT change that row.

**Schedule and retries**

- **FR-012**: The job MUST run in its own process with its own loop ([ADR 0013](../../docs/adr/0013-deterministic-services-run-their-own-loops.md)), taking every time judgement (trading day, open, close, early closes) from the shared exchange calendar.
- **FR-013**: On an XNYS trading day, the main run MUST start at the beginning of a pre-open window (08:00 ET) and aim to finish before 09:15 ET. If the job starts later in the day, it MUST run the full set at once.
- **FR-014**: Between the main run and that day's close, the job MUST periodically rebuild the symbol set, fetch symbols newly in it, and retry symbols that failed, backing off per symbol so a persistently failing symbol (whatever the reason, including an unknown or invalid symbol) is not retried or re-logged every check.
- **FR-015**: The job MUST do nothing on days that are not XNYS trading days, and MUST NOT fetch after the day's close.
- **FR-016**: The job MUST stay within the provider's rate limit, slowing down on a rate-limit response rather than abandoning the remaining symbols.
- **FR-017**: Only one instance of the job MUST run at a time.
- **FR-018**: Running the job twice for the same day MUST produce the same rows as running it once (idempotent).
- **FR-019**: On a lost database connection the process MUST exit so the platform restarts it (ADR 0013 §5). Per-symbol provider failures MUST NOT stop the process.
- **FR-019a**: At startup the job MUST make one read-only call with its market-data key and exit if the key is rejected (a 401, or a 403 on that call). If the check can't complete (an outage or rate limit), the job MUST NOT exit: it logs a warning and the first tick repeats the call (amended 2026-09-30). If the key is rejected after startup, the job MUST log one error-level line for that run naming the key rejection, skip the rest of the run, and keep running.

**Credentials and access**

- **FR-020**: The job MUST hold only its own database login (`ta_reference_data`) and the read-only market-data key. It MUST NOT hold or read any broker credential or any other component's database login.
- **FR-021**: The job's database role MUST be able to read and insert `instrument_reference` (its UPDATE permission from migration 0006 is revoked), and to read one candidate-symbols view that exposes only symbols (held positions, and symbols named in reports and decisions within the FR-001 window). It MUST have no access to `positions`, `reports`, `decisions` or any other table, and the `reports` row-level security policy MUST stay unchanged. This amends `specs/001-data-model/contracts/role-grants.md`, and the grants-matrix test MUST match the database both ways.
- **FR-022**: The job MUST NOT write to any table other than `instrument_reference`, and MUST NOT update or delete rows in it. Inserting a row for a symbol and day that already has one MUST leave the existing row unchanged.
- **FR-023**: The job MUST never log the market-data key or the database connection string.

**Visibility**

- **FR-024**: Each run MUST log the size of the symbol set, the number of symbols recorded, and one line per failed or skipped symbol with its reason.
- **FR-025**: At the market open, the job MUST log a warning naming any symbol in the set that still has no row for today.

**Tests**

- **FR-026**: No test may call the real market-data provider; tests use a fake provider (the suite already blocks non-local connections).

### Key Entities

- **Instrument reference row** (existing `instrument_reference`, migration 0006): one symbol on one trading day — security type, exchange code, market cap, average daily dollar volume, share price, and when it was fetched. Written only by this job; read by the Risk Gate, Assistant and dashboard. Kept as history; never deleted or rewritten.
- **Candidate symbols view**: a read-only list of symbols that are held or were named in reports or decisions within the FR-001 window. Symbols only: no text, reasoning, sizes or quantities. The only thing the job can read outside `instrument_reference`.
- **Symbol set**: the day's list of symbols to fetch: the candidate symbols view plus the seed list. Not stored.
- **Seed list**: a short, version-controlled list of symbols to fetch every trading day regardless of activity.
- **Market-data provider**: an external, read-only source that cannot trade. Its key belongs to this job alone.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a trading day with the provider healthy, 100% of symbols in the set that the provider knows have a row for today before the 09:30 ET open.
- **SC-002**: A symbol set of up to 200 symbols completes within the 08:00–09:15 ET window under the provider's free-tier rate limit.
- **SC-003**: A symbol newly named during market hours has today's row within 5 minutes, when the provider is healthy.
- **SC-004**: Zero rows are ever written for a trading day other than today's, and zero rows are carried forward from an earlier day (verified by tests over failing-provider scenarios).
- **SC-005**: Zero rows are written with a missing, zero or negative required value, a market cap above $20 trillion, or a security type outside the four allowed values.
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
