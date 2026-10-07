# Feature Specification: Opportunistic Identifier agent

**Feature Branch**: `011-opportunistic-identifier`

**Created**: 2026-10-07

**Status**: Draft

**Input**: User description: "Opportunistic Identifier agent (feature 011). Source: docs/specs/opportunistic-identifier-agent.md, plus owner decisions: design A — deterministic code pre-screens the eligible universe (config/risk.yaml floors and exclusions), then one LLM call reviews a shortlist of about 20 names and argues undervaluation theses; rejected: an agent loop fetching data through tools (~4x tokens). Cadence hourly 10:00–15:00 ET, started by the orchestrator (already configured, disabled). Model: Qwen qwen3.7-plus by default with optional Anthropic switch in config (ADR 0018), no failover, endpoint from env only (https). Market data from its own read-only Finnhub key, free endpoints (ADR 0016). Writes only reports rows agent='opportunistic_identifier', no_action row on quiet/failed cycles, never sees positions/cash/decisions. Skips incubation (ADR 0017). Measure real token counts before locking in the model."

## Clarifications

### Session 2026-10-07 (at specify)

- Q: Where do the names to scan come from? → A: An owner-maintained list in the OI's own config file, shipping empty like Research's watchlist. Rejected: Finnhub's full US symbol list (thousands of names, mostly failing the floors, one pass would take many trading days on the free tier) and the reference-data job's recorded names (only names the system already touches).
- Q: How does code rank eligible names before the cut to about 20? → A: By how far the price has fallen: today's move from the previous close and the distance below the 52-week high. The model judges whether a fall is undervaluation from the fundamentals. The owner accepted that this builds a buy-the-dip bias into the pre-screen. Rejected: no ranking (model sees only 20 names an hour) and owner-specified signals.
- Q: Which directions may the OI propose? → A: `buy` only. It hunts undervaluation and can't see holdings, so a sell on an unheld name would mean nothing to the PM. A `sell` or `hold` proposal is dropped and logged.

### Session 2026-10-07 (clarify)

- Q: Should a quiet OI run (only a `no_action` row) trigger an event-driven PM run? → A: No. Only reports that argue something wake the PM, for both analysts. The orchestrator's "newest report" read stops counting `no_action` rows. This reads ADR 0011's "at least one *new* report" as an argued report; spec 005 and `docs/specs/orchestrator.md` gain a one-line clarification.
- Q: Should a name with a still-open OI report today be left out of the ranking? → A: Yes. It is removed before ranking, so its shortlist slot goes to a name that could still produce a report, and the model isn't shown the OI's own open reports. Re-emits stay ruled out; the open-report check at validation remains as a backstop.
- Q: How are today's move and the distance below the 52-week high combined into one ranking? → A: Each measure ranks the names separately (largest fall first), and the two ranks are averaged; ties break by symbol. Rejected: either measure first with the other as a tie-break, and adding the two percentages (the 52-week distance would dominate).
- Q: How fresh must a quote be for its "today's move" to count? → A: Its own trade time must be from today's session and within 15 minutes of the fetch, the same limit the Risk Gate applies to the PM's quotes (`decision_stale`, ADR 0019). Otherwise the name is skipped as stale.

## User Scenarios & Testing *(mandatory)*

The users of the Opportunistic Identifier (OI) are:
- **the Portfolio Manager (PM)**, which reads the OI's reports alongside Research's and is the only one that decides ([ADR 0002](../../docs/adr/0002-pm-synthesizes-rather-than-analysts-deciding.md));
- **the owner**, who wants the system to keep looking at the market during the session for names that look undervalued, and wants to see later how the OI's ideas performed, separately from Research's.

The OI is an analyst, like Research ([`specs/007-research-agent`](../007-research-agent/spec.md)). It finds an opportunity and argues it. Where the two differ:

| | Research | Opportunistic Identifier |
|---|---|---|
| Looks at | news for a watchlist; no prices | prices and fundamentals across the eligible universe; no news |
| When | once, 08:30 ET | hourly, 10:00–15:00 ET |
| Picks what to look at | the owner's watchlist | the owner's scan list, sliced by a documented rotation; code applies the eligibility floors, then ranks by how far the price has fallen and keeps about 20 |
| Directions | buy or sell | buy only |

The same boundaries apply as for Research:
- **It never sees the portfolio:** no positions, cash, decisions, verdicts or orders.
- **It writes only its own report rows.**
- **Its worst outcome is a bad report the PM weighs.** It can't place, approve or block a trade.
- **The PM reads its reports from the start** ([ADR 0017](../../docs/adr/0017-original-analysts-skip-incubation.md)).

Design A (owner, 2026-09-30): code does the screening, and the model makes exactly one call per run to review the shortlist. The model fetches nothing itself. An agent loop that fetched data through tools was rejected at about four times the tokens.

### User Story 1 - An hourly undervaluation scan that leaves a record (Priority: P1)

Each hour from 10:00 to 15:00 ET on a trading day, the orchestrator starts the OI. The OI:
1. takes the next slice of its scan universe, in a fixed, documented order;
2. fetches each name's current price and fundamentals from its own read-only market-data key;
3. drops names that fail the universe floors or have incomplete data;
4. leaves out names with a still-open OI report today, ranks the rest, and keeps a shortlist of about 20;
5. asks the model, once, which shortlisted names look undervalued and why;
6. checks every proposal and writes one report per valid one, or one `no_action` report if there are none.

Each report carries a direction (`buy`), a conviction from 1 to 5, a suggested size (a target weight, as for Research), sources rebuilt by code from the data fetched in that run, and a rationale. It expires at that trading day's close.

**Why this priority**: this is the agent. Without it the PM has no intraday input, and ADR 0011's event-driven PM runs have nothing to react to after the morning.

**Independent Test**: run the OI against fake market data and a fake model answer. Check the shortlist against a hand-computed average of the two ranks. Check that the rows written match the valid proposals, that every source points at data fetched in that run, and that a run where the model proposes nothing writes exactly one `no_action` row.

**Acceptance Scenarios**:

1. **Given** a trading day at 11:00 ET and market data for a slice of eligible names, **When** the OI runs and the model argues two of the shortlisted names, **Then** two reports are written for those names, each with at least one source built from data fetched in that run, and no other rows.
2. **Given** a run where the model argues nothing, **When** the run ends, **Then** exactly one `no_action` report is written, saying how many names were scanned, skipped for missing data, and shortlisted.
3. **Given** a still-open OI report on a symbol, **When** a later run fetches that symbol, **Then** it is left out before ranking, isn't sent to the model, and no duplicate row is written.
4. **Given** a run that writes only a `no_action` report, **When** the orchestrator next checks for new reports, **Then** it doesn't start an event-driven PM run on account of it.
5. **Given** the PM's next event-driven run, **When** it reads open reports, **Then** it sees the OI's reports next to Research's, attributed to `opportunistic_identifier`.

---

### User Story 2 - Model output can't write anything the run didn't support (Priority: P1)

Everything the model says is checked by code before it is written ([ADR 0018](../../docs/adr/0018-qwen-as-a-model-provider.md) §5). A proposal is dropped, with its reason logged, when its symbol wasn't on that run's shortlist, its direction or conviction is out of bounds, its size is out of range, it repeats a symbol already proposed in the run, or it matches one of the OI's still-open reports. Sources are never taken from the model's text.

**Why this priority**: the OI writes rows the PM acts on with no human in between ([ADR 0006](../../docs/adr/0006-autonomous-operation-with-daily-loss-breaker.md)). A shortlist bound is what keeps a confused or manipulated model answer from naming an illiquid or ineligible ticker.

**Independent Test**: feed fake model answers that name a symbol off the shortlist, a `sell`, a conviction of 7, a negative size, a duplicate symbol, and an invented source; check that none is written and each drop has its reason.

**Acceptance Scenarios**:

1. **Given** a model answer naming a symbol that wasn't on the shortlist, **When** it is checked, **Then** it is dropped (`not_shortlisted`) even if the symbol is listed and eligible.
2. **Given** a model answer whose every proposal is invalid, **When** the run ends, **Then** one `no_action` report says how many proposals were dropped and why.

---

### User Story 3 - A failed or quiet run is still a data point (Priority: P2)

A run that can't do its job still writes a `no_action` report naming why: the market-data key rejected, every fetch failing, the model key rejected, the model unavailable, refused, truncated or unusable, or an unexpected error. A silent agent and a broken agent must be distinguishable in the journal and on the dashboard.

**Why this priority**: needed for trust in the record, but the scan itself (Story 1) comes first.

**Independent Test**: inject each failure in turn with fakes and check one `no_action` row per run, naming the failure, and the documented exit status.

**Acceptance Scenarios**:

1. **Given** the model provider returns an error, **When** the run ends, **Then** one `no_action` report names the model failure and the run exits with a failure status.
2. **Given** the run can't write even its `no_action` row, **When** it exits, **Then** it exits with a distinct status so the orchestrator's record shows it, never a success.

---

### User Story 4 - The owner measures real token use before switching it on (Priority: P2)

Before the OI is enabled in the schedule, the owner runs it in a dry-run mode that does everything except write: it fetches real data, makes the real model call, and prints the shortlist, each would-be row, each drop, and the input and output token counts. Every scheduled run also logs its token counts. The owner uses these numbers to confirm the model choice and its cost.

**Why this priority**: the owner asked that real token counts be measured before the model is locked in. It doesn't block the scan, but it gates enabling it.

**Independent Test**: run the dry-run mode with fakes and check it writes nothing and prints the token counts the fake model reported.

**Acceptance Scenarios**:

1. **Given** the dry-run mode, **When** it completes, **Then** nothing is written to the database and the output includes the shortlist, the would-be rows, the drops, and input/output token counts.

---

### Edge Cases

- **The universe is too large to fetch every hour**: the run fetches only a slice, chosen by a deterministic rotation documented in code, so that over a fixed number of runs every name in the scan universe is fetched once before any is fetched twice. The rotation must not depend on run order or on anything held in memory between runs.
- **A symbol already flagged and still open**: it is left out before ranking, so it never reaches the model, and it is not written again that day. With buy-only reports there is no change of direction to re-emit on. If a proposal for it slips through anyway, validation drops it.
- **Missing data for a symbol** (missing fundamentals, a quote not from today's session or more than 15 minutes old, a halted symbol, a value that is zero or implausible): the symbol is skipped for that run, never sent to the model, and logged with its reason. It produces no report.
- **The market-data provider is rate-limited or slow**: fetching slows down or stops at a deadline, and the run continues with what it has, so it always finishes inside the orchestrator's 10-minute timeout and leaves a report. Names it couldn't fetch count as skipped.
- **Fewer eligible names than the shortlist size**: the model sees all of them. With none, no model call is made, and one `no_action` report says the shortlist was empty.
- **A shared market-data account**: the OI's pace leaves room for the other components that may share one Finnhub account ([ADR 0016](../../docs/adr/0016-market-data-for-the-llm-agents.md) §5).
- **A run starts outside 10:00–15:00 ET or on a closed day** (started by hand): it refuses, except in dry-run mode.
- **A report written after 15:30 ET**: it expires unused, by design ([ADR 0011](../../docs/adr/0011-event-driven-portfolio-manager-runs.md)). The schedule's last slot is 15:00.
- **A quiet run**: its `no_action` row doesn't wake the PM (FR-023), so six quiet runs cost no PM calls.
- **The OI flags a name the reference-data job hasn't recorded today**: nothing special is needed. Symbols named in reports are picked up by that job within minutes ([`docs/specs/reference-data.md`](../../docs/specs/reference-data.md)), and the gate rejects a buy until they are.

## Requirements *(mandatory)*

### Functional Requirements

**Running**
- **FR-001**: The OI MUST be started by the orchestrator on its configured schedule (hourly, 10:00–15:00 ET, trading days). It MUST NOT schedule itself.
- **FR-002**: The OI MUST offer a dry-run mode that does everything except write to the database, and prints the shortlist, would-be rows, drops and token counts. The dry-run mode needs no database login.
- **FR-003**: Every run MUST finish within the orchestrator's timeout for the OI (10 minutes), stopping fetches at a deadline and carrying on with what it has.

**Choosing what to look at (deterministic, no model)**
- **FR-004**: The scan universe MUST be an owner-maintained list of symbols in the OI's own version-controlled configuration, changed only through code review. It ships empty: the owner fills it, and no tickers are invented. With an empty list, a run makes no fetches and no model call and writes one `no_action` report saying the scan universe is empty.
- **FR-005**: Each run MUST fetch a slice of the scan universe chosen by a deterministic rotation that depends only on the scan universe, the trading day and the run's slot, documented in code, so coverage is even over time.
- **FR-006**: The OI MUST drop every fetched name that fails the universe rules in `config/risk.yaml`: US-listed common equity on the allowed exchanges, the market-cap, average daily dollar volume and share-price floors. Values MUST be derived the same way the reference-data job derives them, so the OI doesn't flag a name the Risk Gate would reject on the same data. The OI reads only the universe section of that file and MUST NOT write it.
- **FR-007**: The OI MUST skip a name with incomplete, stale or implausible data rather than send it to the model. A quote is stale unless its own trade time is from today's session and within 15 minutes of the fetch.
- **FR-008**: The OI MUST leave out every name with a still-open OI report before ranking, then rank the remaining names by how far their price has fallen: each name is ranked separately by today's move from the previous close and by its distance below the 52-week high (largest fall first in both), the two ranks are averaged, and the lowest averages are kept, up to N, where N is configuration (default 20). Ties break by symbol, alphabetically. The ranking is code; whether a fall is undervaluation is the model's judgement, made from the fundamentals sent with each shortlisted name (valuation ratios and margins, as available).

**The model call**
- **FR-009**: Each run MUST make at most one model call, sending only the shortlisted names' fetched data. It MUST NOT send the OI's own earlier reports, or any portfolio, decision, verdict, order or journal data, and the model MUST NOT be given tools.
- **FR-010**: The provider and model MUST be configuration, changed only through code review: Qwen `qwen3.7-plus` by default, with Anthropic as an optional switch. No automatic failover between providers. The Qwen endpoint MUST come from the environment, https only, never from source.
- **FR-011**: The input sent to the model MUST be capped in size, and the run MUST record the input and output token counts the provider reports, in its log and in dry-run output.

**Checking and writing**
- **FR-012**: A proposal MUST be dropped, with its reason logged, when: its symbol is not on that run's shortlist; its direction is not `buy`; its conviction is not 1–5; its suggested size is out of range; it repeats a symbol already proposed in the run; or it matches the symbol and direction of one of the OI's still-open reports.
- **FR-013**: Every non-`no_action` report MUST carry at least one structured source built by code from data fetched in that run (what was fetched, from which provider, and when), never from the model's text. A source MUST never carry a credential: no API key in any URL or field.
- **FR-014**: The rationale MUST be capped in length and treated as untrusted model-written text by every reader.
- **FR-015**: A run's rows MUST be written together or not at all, and expire at the end of that trading day.
- **FR-016**: When a run writes no proposal (nothing argued, everything dropped, empty shortlist, or a failure), it MUST write exactly one `no_action` report naming why, with the counts of names scanned, skipped and shortlisted, and of proposals dropped by reason.

**Failures**
- **FR-017**: A rejected market-data key, every fetch failing, any model failure (rejected key, rejected request, unavailable, refused, truncated, unusable answer), and any unexpected error MUST each produce a `no_action` report naming the failure and a failure exit status. If even that row can't be written, the run MUST exit with its own distinct status.
- **FR-018**: Partial data (some names unfetched) MUST NOT fail the run. The skipped count is reported.

**Boundaries and credentials**
- **FR-019**: The OI MUST write only `reports` rows with `agent = 'opportunistic_identifier'`, enforced by its database role (already in place), and MUST NOT read `decisions`, `risk_verdicts`, `orders`, `positions`, `account_snapshots` or `journal`.
- **FR-020**: The OI MUST use only variables named with its own prefix, `OPPORTUNISTIC_IDENTIFIER_` ([ADR 0015](../../docs/adr/0015-orchestrator-starts-agents-with-their-own-credentials.md)): its database login, its read-only Finnhub key, and the configured model provider's key and (for Qwen) base URL. It MUST hold no broker credential.
- **FR-021**: The OI MUST pace its market-data calls at a configurable rate that leaves room for other components on a shared account.
- **FR-022**: The OI's database login MUST be provisioned the same way as the other agents' logins, and the schedule entry MUST list exactly its variables before it is enabled.
- **FR-023**: A `no_action` report MUST NOT count as a new report for the orchestrator's event-driven PM trigger, from either analyst. Only reports with a direction other than `no_action` wake the PM.

### Key Entities

- **Scan universe**: the owner's list of names the OI may fetch, in a fixed order the rotation slices.
- **Shortlist**: the up-to-N eligible, complete, highest-ranked names without a still-open OI report one run sends to the model. Only these may appear in that run's reports.
- **OI report**: a `reports` row with `agent = 'opportunistic_identifier'` (direction, conviction, suggested target weight, code-built sources, rationale, expiry at the day's close), or a `no_action` row with the run's counts and reason.
- **Run summary**: what each run logs: names scanned, skipped by reason, shortlisted, proposals written and dropped by reason, token counts, and outcome.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every OI run that the orchestrator records as started leaves at least one report row, or exits with the distinct can't-write status. Zero runs end with no trace.
- **SC-002**: Zero written reports name a symbol that wasn't on that run's shortlist, or cite a source not fetched in that run, across the test suite's fake model answers, including deliberately malicious ones.
- **SC-003**: Every name in the scan universe is fetched at least once within a fixed, documented number of trading days, computable from the universe size and the per-run slice.
- **SC-004**: 100% of runs finish inside the 10-minute timeout, including when the market-data provider is slow or rate-limited.
- **SC-005**: Before the OI is enabled, the owner has measured input and output token counts from at least one real dry run, and every scheduled run afterwards logs its counts.
- **SC-006**: The PM's run after an OI report sees that report and records it in `decision_reports` when it draws on it, so the OI's track record is measurable on its own.

## Assumptions

- **The schedule is already set**: the disabled `opportunistic_identifier` entry in `config/schedule.yaml` (hourly, 10:00–15:00 ET, 10-minute timeout) is the cadence. This feature enables it and fills its `env` list. The orchestrator's only change is FR-023's narrower read of report times.
- **The database role and its row-level insert rule already exist** (feature 001). Only a login is new.
- **Market data comes from Finnhub's free endpoints** with the OI's own key, `OPPORTUNISTIC_IDENTIFIER_FINNHUB_API_KEY` ([ADR 0016](../../docs/adr/0016-market-data-for-the-llm-agents.md)). The free tier's rate limit bounds how many names a run can fetch, which is why the rotation exists.
- **No new table**: the rotation is derived, not stored, so the OI needs no write beyond `reports` (Constitution III).
- **"Thesis materially changed"** (the behavior spec's re-emit case): with buy-only reports, a symbol with a still-open OI report is not re-emitted that day. An open report expires at the day's close, so the next day starts fresh.
- **Recurring data gaps** are surfaced through each run's log and `no_action` counts, not tracked across runs, since that would need state the OI has no table for.
- **Suggested size** means a target weight, as for Research, within the same range rules.
- **Inputs are mostly numbers**, but company names and industry labels come from the provider, so the model's input is still treated as data, not instructions, and the output checks above are the real enforcement.
- **Releasing** follows feature 010: a PR to `main`, then a release PR to `release/prod`, with the owner setting the Railway variables. The deployed service shape doesn't change; the OI runs as a child of the orchestrator ([ADR 0015](../../docs/adr/0015-orchestrator-starts-agents-with-their-own-credentials.md)).
