# Research: Opportunistic Identifier agent

Decisions made while planning feature 011. The spec's Clarifications (2026-10-07) are inputs here, not reopened: an owner scan list, buy only, names with an open report left out before ranking, the average of two "fallen" ranks, a 15-minute quote limit, and `no_action` rows not waking the PM.

## O1. Package shape and imports

**Decision**: a new top-layer package, `trading_agent.opportunistic_identifier`, built like `research`. It is added to the top layer of the import-linter contract in `pyproject.toml`, next to `research` and `portfolio_manager`.
- **It may import**:
  - `trading_agent.llm`: the model port, the Qwen and Anthropic clients, and the model settings;
  - `trading_agent.reference`: `provider` (the `Listing`, `Profile` and `Quote` dataclasses and their errors) and `normalize` (the eligibility derivation, O4);
  - `trading_agent.risk`: `calendar`, `config.load_config` and `rules.US_LISTED_MICS`;
  - `trading_agent.storage.db` and `trading_agent.no_redirect`.
- **It imports nothing** from `research`, `portfolio_manager`, `orchestrator` or `execution`. That's enforced by the layers contract, plus an import-guard test like `tests/unit/research/test_import_guard.py`.

**Rationale**: the layering in `CLAUDE.md` puts analysts above `reference`. Reusing `reference.normalize` is how FR-006 ("derived the same way the reference-data job derives them") becomes literally true, not just a promise.

**Alternatives considered**: sharing Research's modules (rejected: siblings never import each other); moving the shared pieces to a new lower layer first (not needed, since everything the OI shares already sits below it).

## O2. Market data: Finnhub, three calls per name

**Decision**: the OI's own adapter, `opportunistic_identifier/finnhub.py`, built like `reference/finnhub.py`:
- the key goes in the `X-Finnhub-Token` header, never in a URL;
- redirects are refused (`open_without_redirects`);
- numbers become `Decimal`, and anything unusable becomes `None`.

Per run:
- **Symbol list:** `/stock/symbol` for each of XNYS, XNAS and XASE, merged. That's 3 calls a run, and it gives each name's type, exchange and description (the company name).

Per name, in order:
- **`/quote`:** `c`, `pc`, `t`.
- **`/stock/profile2`:** `marketCapitalization`, `currency`, `finnhubIndustry`.
- **`/stock/metric?metric=all`:** the fixed set of keys in [data-model.md](data-model.md#fundamentals-sent-to-the-model), including `10DayAverageTradingVolume` and `52WeekHigh`.

If the quote is already stale (O5), the other two calls are skipped, so a halted name costs one call, not three.

**Rationale**: ADR 0016 §2 names these free endpoints. Feature 004 already uses `/quote`, `/stock/profile2` and `/stock/metric` and has confirmed their units. The OI needs more metric keys than `reference.provider.Metrics` carries, so it has its own `Fundamentals` dataclass rather than a change to feature 004's port.

**Not yet confirmed**: the exact metric key names beyond the two feature 004 uses. The owner's `--check` run (quickstart step 2) prints them before enabling.

## O3. Rotation: which slice a run fetches

**Decision**: pure code in `rotation.py`. It uses no state, no database and no memory between runs.
1. **Scan order:** the scan list, sorted alphabetically.
2. **Batches:** consecutive slices of `slice_size` names, `B = ceil(U / slice_size)` batches for a universe of `U` names.
3. **A day's slots** are the times from `slots.first`, every `slots.every_minutes`, up to `min(slots.last, close − slots.before_close_minutes)`: exactly the orchestrator's `oi_slots` rule (`orchestrator/planner.py`). A normal day has 6 (10:00–15:00); an early close at 13:00 has 3 (10:00–12:00, the cap being 12:30). This fixes `/speckit-analyze` C1: counting a fixed 6 a day would skip the same batches on every early close.
4. **Run index:** `k = (slots on every XNYS session from the epoch, 2026-01-02, up to yesterday) + today's slot number`. Today's slot number is the latest of today's slots at or before `now`, or 0 before the first.
5. **Batch fetched:** `k mod B`.

Every slot that exists gets its own run index, so every batch is fetched once in any `B` consecutive slots, and every name within the number of trading days it takes the calendar to provide `B` slots (6 a normal day; SC-003). A missed slot is never backfilled, the same as the orchestrator. That batch's turn simply passes.

**A late start (accepted, `/speckit-analyze` C2)**: the OI computes its slot from its own clock. The orchestrator starts a slot only within that slot's interval, but if it starts one in its last seconds, the OI may read the next slot: that batch is fetched twice that day and one batch waits a day. It is rare and harmless, and fixing it would mean the orchestrator passing the slot to the agent, a change to how it launches agents. Documented in `rotation.py`.

The `slots` block in the OI's config (`first`, `last`, `every_minutes`, `before_close_minutes`) must match the orchestrator's schedule (`window_start`, `window_end`, `interval_minutes`, and the PM's `before_close_minutes`). A unit test asserts this, the same way `reference/finnhub.py`'s `SYMBOL_LIST_MICS` is kept equal to the gate's list. The OI reads nothing from `config/schedule.yaml` at runtime.

**Rationale**: the behavior spec requires an even, documented rule "not left to run order". A stateless rule needs no table, which would otherwise be a write beyond `reports` (Constitution III).

**Alternatives considered**:
- **The calendar date as the run index:** weekends and holidays skip indices, so coverage would be uneven for some values of `B`.
- **A "last scanned" table:** a new write and a new grant.
- **Reading `config/schedule.yaml` at runtime:** it would duplicate the orchestrator's parser, or import a sibling.

**Edge**: when the scan list is no bigger than `slice_size`, `B = 1` and every run fetches the whole list. The slot count is a sum over a few hundred sessions a year: computed directly, no state.

## O4. Eligibility: the gate's rule on the reference job's derivation

**Decision**: for each fetched name, `screen.py`:
1. builds the row exactly as the reference-data job would, by calling `reference.normalize.normalize(symbol, listing, profile, quote, metrics, now)`. That fixes the type, exchange, market cap, 10-day average dollar volume × previous close, and previous close as share price, all rounded down. Any `Failure` it returns is a skip, under the job's own reason name;
2. applies the universe rule from `config/risk.yaml` (`risk.config.load_config(...).universe`). The listing must be `common_stock` on `rules.US_LISTED_MICS`, and the three floors use the gate's comparisons and the gate's rule names (`universe_listing`, `universe_market_cap`, `universe_dollar_volume`, `universe_share_price`).

The gate's own check, `risk/gate.py:_universe_stop`, is private, and the gate is **not touched** by this feature. The OI has its own five-line copy of the check. A Hypothesis property test asserts that it agrees with `gate._universe_stop` on generated rows and configs, so the two can't drift without a test failing.

**Rationale**: a name the OI flags but the gate rejects wastes a PM decision. Using the same derivation and the same comparisons keeps them aligned.

**Note for the owner**: the OI loads the whole of `config/risk.yaml` with the gate's strict loader, so it refuses to start on a bad file, just as the gate does. It uses only `universe`, and it never logs or sends any other value. Constitution II forbids the **PM** from reading this file. The OI's behavior spec explicitly calls for its universe floors.

## O5. Freshness, completeness and plausibility

**Decision**: checks run in this order, cheapest first (`/speckit-analyze` P1):
1. **From the symbol list, before any per-name call:** `reference.normalize.listing_failure` (not listed, share class, conflicting, missing type or exchange) and the listing half of the universe rule (`universe_listing`: not `common_stock`, or not on `US_LISTED_MICS`). An ETF or an OTC name costs no call.
2. **The quote**, then (only if fresh) the profile and fundamentals.

A name is skipped (never sent to the model) when any of these holds:
- **`stale_quote`:** the quote's `t` isn't on today's trading day, or is more than `quote_max_age_minutes` (15) before the fetch. Checked first, before the other two calls (O2);
- **`missing_price`:** `c` or `pc` is missing or zero;
- any `reference.normalize` failure (O4), such as `share_class_unverified` or `non_usd_market_cap`;
- **`missing_52_week_high`:** `52WeekHigh` is missing or zero;
- **`missing_fundamentals`:** neither `peTTM` nor `pbQuarterly` is present. Other fundamentals are optional and are sent as `null`;
- **`implausible_move`:** today's move is beyond ±50%, which suggests a split or a bad print, not a price.

A value of zero is treated as missing, as in feature 004. Today's move is `(c − pc) / pc`. The distance below the 52-week high is `(52WeekHigh − c) / 52WeekHigh`. This is negative when today's price is above the stored high (the metric updates daily), which simply ranks the name last.

**Rationale**: the behavior spec says to skip on incomplete data and not flag it. The ±50% bound keeps one corporate action from topping the ranking.

## O6. Ranking and the shortlist

**Decision** (pure, in `screen.py`):
1. Leave out every name with a still-open OI report (`already_open`, before ranking).
2. Rank the remaining eligible names twice, each time largest fall first:
   - by today's move, ascending;
   - by the distance below the 52-week high, descending.

   Ranks are 1-based ordinals, and equal values are ordered by symbol.
3. Score each name as the average of its two ranks. Keep the `shortlist_size` (20) lowest scores, with ties broken by symbol.

**Rationale**: the spec's Clarifications. Ordinal ranks keep the rule easy to compute by hand in a test (the spec's Independent Test).

## O7. The model call

**Decision**: one call through `trading_agent.llm`, with Qwen `qwen3.7-plus` by default (strict JSON-schema mode, thinking off, as Research found in R5), or Anthropic by configuration. No failover, and the Anthropic refusal fallback is off, as in Research.
- **System prompt** (`prompt.py`, with a `PROMPT_VERSION`): the OI's role (argue undervaluation, never decide), what each field means, buy only, size as a target weight, conviction 1–5, and "propose only names worth arguing; an empty list is normal".
- **Untrusted text:** the company name and industry come from the provider. They are cut to 100 characters and marked in the prompt as data, never instructions.
- **User document:** one JSON object, `{"now", "trading_day", "names": [...]}`, with one entry per shortlisted name holding the fields in [data-model.md](data-model.md#fundamentals-sent-to-the-model). No portfolio, decisions, journal or earlier OI reports (FR-009).
- **Size limit:** the document is capped at `max_input_chars`. The shortlist is at most 20 names, so the cap is a backstop. If it's exceeded, the run fails with `input_too_large` rather than silently truncating.
- **Tokens:** `ModelReply.input_tokens` and `output_tokens` are logged on every run and printed by `--dry-run` (FR-011, SC-005).

**Estimate, to be replaced by the owner's measurement**:

| | Value |
|---|---|
| Input per name | about 600 characters (~200 tokens) |
| Input per run | 20 names plus a ~1.5k-token prompt: ~6k tokens |
| Output per run | up to ~3k tokens |
| Cost per run (list price, ADR 0018) | ~$0.007 |
| Runs per month | 6 a day × ~21 days = ~126 |
| Cost per month | ~$0.90 |

## O8. Checking the answer

**Decision**: the answer is `{"proposals": [...]}`. Each proposal has exactly `symbol`, `direction`, `conviction`, `suggested_size_pct` and `rationale`. The JSON Schema is generated in code from the same table the checker uses, so they can't drift apart (as in Research).

A proposal is dropped, with its reason logged, as one of:
- `malformed_answer`: the wrong shape or extra fields;
- `not_shortlisted`: a symbol not on this run's shortlist, checked by exact string match. A symbol off the shortlist can't pass, whatever else is true of it;
- `invalid_direction`: anything but `buy`;
- `invalid_conviction`: not an integer 1–5;
- `invalid_size`: not a number, above 100, or 0 or below once rounded **down** to 3 decimal places (the column is `numeric(6,3)`). Research's `_size` rule, buy branch, copied: 0.0004 is dropped rather than rounding to 0 and failing the insert (`/speckit-analyze` S2);
- `duplicate_symbol`: a second proposal for the same symbol in this run;
- `already_open`: a backstop, since open names were never shortlisted.

An answer that isn't JSON, or isn't the top-level shape, is `unusable_answer` (a failure).

**Text cleaning** (`/speckit-analyze` S1): every piece of outside text that can reach a row or the prompt (the rationale, the provider's company name and industry) passes through `opportunistic_identifier/text.py`, a copy of `research/text.py`'s `clean` (siblings can't import each other). It removes NUL and other C0 controls except newline and tab, DEL, and lone surrogates, which Postgres would refuse, losing the run's whole write. Cleaning happens before the length cut.

## O9. Sources, built by code

**Decision**: every written report carries three sources, one for each endpoint fetched for that name in that run. Each has these fields:

| Field | Value |
|---|---|
| `title` | e.g. `Finnhub quote for MSFT`, `Finnhub company profile for MSFT`, `Finnhub basic financials for MSFT` |
| `url` | the endpoint with its query string and no key, e.g. `https://finnhub.io/api/v1/quote?symbol=MSFT` |
| `publisher` | `Finnhub` |
| `published_at` | for the quote, its own `t`; for the other two, the time the run fetched them |
| `relevance` | `primary`, since the data is about the company itself |

A unit test asserts that no source field ever contains the key (the FR-013 credential rule). The key travels only in a header, so there's nothing to strip.

**Rationale**: `reports_sources_required_when_actionable` needs at least one source. The PM counts `primary` sources as evidence, and these are the exact numbers the argument rests on. Nothing in a source comes from the model.

**Note**: the PM's prompt describes report text as "written by other models from public news". For the OI it's market data, not news. That's harmless (the text is still untrusted), and changing the PM's prompt is out of scope here.

## O10. Run budget

**Decision** (after `/speckit-analyze` B1; owner, 2026-10-07): the orchestrator's timeout for the OI rises from 10 to **15 minutes** (`config/schedule.yaml` `opportunistic_identifier.timeout_minutes: 15`; `RUN_BUDGET_SECONDS = 900`). That is allowed by the schedule's own rule for the OI: its timeout must be shorter than its 60-minute interval (`orchestrator/config.py` `_interval_outlasts_timeout`).

**The guarantee is a runtime deadline**, not a worst-case sum. Fetching stops at:

`fetch_deadline = start + 900 − margin (60) − slack (60) − attempts × model.timeout_seconds − one Finnhub socket timeout (10)`

- `attempts` is 1 on Qwen (`llm/qwen.py` makes one request, no retry) and 2 on Anthropic (the SDK retries once, `llm/anthropic_client.py` `MAX_RETRIES = 1`). A test keeps `MODEL_ATTEMPTS["anthropic"] == MAX_RETRIES + 1` without importing the SDK module at runtime.
- No call starts after the deadline, and a started call is bounded by its 10 s socket timeout, so the run ends within the budget whatever Finnhub does. Unfetched names count as `not_fetched` (FR-003, FR-018).
- **Slack** covers the symbol list, screening and the write; **margin** covers startup, the database connect and the SDK's wait before its retry (Research's review M3).

**The loader's check** only makes sure a *normally paced* slice fits in that window, so a healthy run fetches its whole slice:

`(3 + 3 × slice_size) × 60 / finnhub_calls_per_minute ≤ fetch window`

| Provider | Fetch window | Largest slice at 20 calls/min | Default 40 names |
|---|---|---|---|
| Qwen (default) | 900 − 60 − 60 − 120 − 10 = 650 s | 71 | 369 s |
| Anthropic | 900 − 60 − 60 − 240 − 10 = 530 s | 57 | 369 s |

A unit test asserts that 900 equals `60 × config/schedule.yaml`'s `opportunistic_identifier.timeout_minutes`.

**Alternatives considered**: Research's worst-case formula (every call assumed to hit its socket timeout) leaves about 5 names a run at 10 minutes and about 12 at 15: not viable for a scanner. Keeping 10 minutes with a 90 s model timeout gives about 25 names a run.

## O11. Pacing and a shared Finnhub account

**Decision** (owner, 2026-10-07): the OI shares one Finnhub account with the other components and `finnhub_calls_per_minute` defaults to **20**, with the same pacer pattern as Research and feature 004. A 429 backs off and continues until the deadline.

**Rationale**: with the reference-data job at 30 a minute and the PM's quotes, the OI at 20 keeps the shared account's combined pace near the free tier's commonly quoted 60 a minute (unconfirmed) even while an OI run overlaps a PM run. The cost is a smaller slice: 40 names a run, so a 240-name list is covered daily. Rejected: a separate account (recommended, not chosen) and sharing at 30 with 429 backoff absorbing overlaps.

## O12. Exit codes, failures and quiet runs

**Decision**: the same exit codes as Research, so the orchestrator and the owner read them the same way:

| Code | Meaning |
|---|---|
| 0 | Reports written, or a quiet `no_action` (`empty_scan_universe`, `empty_shortlist`, `nothing_argued`, `all_dropped`), or outside the window with nothing done |
| 1 | A failure `no_action` was written |
| 5 | The close passed mid-run, so no row could be written (`window_closed`; `reports_expires_after_generated` forbids a row after the close). Distinct, so it's never read as a recorded failure (`/speckit-analyze` E1) |
| 2 | Refused to start |
| 3 | A database error |
| 4 | Crashed with nothing written |

The failure categories are `symbol_list_unavailable`, `market_data_unavailable` (the key rejected, or every per-name fetch failed), `input_too_large`, `model_key_rejected`, `model_rejected_request`, `model_unavailable`, `model_refused`, `model_truncated`, `unusable_answer` and `internal_error`.

Every `no_action` rationale carries the run's counts: names in the slice, fetched, skipped by reason, already open, eligible, shortlisted, and proposals dropped by reason. That's where a recurring data gap shows (the spec's assumption).

**The trading window** (corrects the spec's "outside 10:00–15:00"): a run does nothing, exit 0, unless today is a session, the market is open, and the time is at or after the first slot (10:00 ET). A 15:00 slot that starts late still runs until the close. `--dry-run` skips this check.

## O13. The PM trigger ignores `no_action` (FR-023)

**Decision**: migration `0014_latest_argued_report.sql` replaces the view the orchestrator reads:

```sql
CREATE OR REPLACE VIEW latest_report_time AS
    SELECT max(generated_at) AS generated_at FROM reports
    WHERE generated_at <= now() AND direction <> 'no_action';
```

It keeps the same name, column and grants, so no orchestrator code changes. Only the view's definition does.

Docs: `docs/specs/orchestrator.md` and `specs/005-orchestrator` (a dated amendment note) say that a `no_action` report doesn't count as new. An integration test asserts that a newer `no_action` row doesn't move `latest_report_time`.

**Rationale**: ADR 0011 says "at least one *new* report", and a `no_action` row argues nothing for the PM to consider. This narrows a read; it adds no grant, role or flow, so it needs no ADR (Constitution V). Research's 08:30 `no_action` already came before the 10:00 morning session, so this changes nothing in practice for Research.

## O14. Credentials, login, schedule and deployment

**Decision**:
- **Variables** (ADR 0015 prefix): `OPPORTUNISTIC_IDENTIFIER_DATABASE_URL`, `_FINNHUB_API_KEY`, `_DASHSCOPE_API_KEY` and `_QWEN_BASE_URL` (when on Qwen), and `_ANTHROPIC_API_KEY` (when switched). They're listed in `config/schedule.yaml`'s `env`, in `.env.example`, and on the orchestrator service in `.railway/railway.ts` as `preserve()`. `tests/unit/deploy/test_deployed_shape.py` already checks that the orchestrator's variables equal the schedule's lists. Its pinned table gains the five names.
- **Login**: `Login("ta_opportunistic_identifier_login", "ta_opportunistic_identifier")` is added to `storage/logins.py`. Its contract, `specs/010-observe-only-deployment/contracts/logins-command.md`, gets a dated amendment note ("nine rows"). The role and its grants already exist (migrations 0001 and 0002). No new grant: an integration test asserts that the role still can't read `positions`, `decisions`, `orders`, `risk_verdicts`, `account_snapshots` or `journal`.
- **Rollout, recommended**: this feature ships with `opportunistic_identifier.enabled: false`, with its `env` list filled in. Enabling is a separate one-line PR, made after the owner's real dry run has measured tokens (SC-005) and the scan list is filled in. So merging this feature to `release/prod` deploys nothing that runs.

## O15. Tests

- **Offline**: fakes for the market-data port and the model (`tests/fakes/model.py` already exists), plus a fake opener for the Finnhub adapter. `tests/conftest.py` already blocks the network.
- **Pure units**:
  - rotation, including a property test that every name is covered within `ceil(B/S)` sessions;
  - screening, with a table per skip reason;
  - ranking, against hand-computed shortlists;
  - the answer checker, including a property test that nothing off the shortlist is ever written (SC-002) and that malicious answers are dropped;
  - sources, with no key in any field;
  - the config loader and its budget check;
  - the gate-equivalence property (O4).
- **Service**: every failure category, quiet reasons, exit codes, the deadline, and `--dry-run` writing nothing.
- **Integration**: write as `ta_opportunistic_identifier` (all-or-nothing, row-level security, no access to the trading tables), migration 0014's view behavior, and the login command's nine rows.

## O16. Adversarial review (2026-10-08)

No high-severity findings. Resolved in code: M1 (a split-shaped 52-week range is skipped as `inconsistent_52_week_range`), M3 (the prompt no longer claims every name fell; `PROMPT_VERSION` 0.2), M4 (`rationale_max_chars` 800, stated to the model, and a loader check that the shortlist's rationales fit `max_output_tokens`), L4 (a `COMMENT ON VIEW` on `latest_report_time`) and L5 (a database expiry-check failure at the close maps to `window_closed`, exit 5).

Accepted or deferred, with reasons:
- **M2, each batch tied to the same hour.** When the number of batches equals the slots per day (for example 240 names in 6 batches), batch *i* is always fetched at slot *i*. Accepted: SC-003's exact coverage depends on consecutive indices, and a per-day offset would break it whenever `B` differs from the slot count. The risk is a time-of-day failure starving the same names. The likeliest one, Finnhub contention with the PM's 10:00 session, is bounded by the 20-a-minute pace and backoff, and shows in the per-run skip counts. Revisit if the 10:00 run's `not_fetched` or `rate_limited` counts are persistently higher than other slots'.
- **L1, socket timeouts aren't total timeouts.** A provider trickling bytes can stretch one call past its 10 s or 120 s, and so the run past 15 minutes, with no row written. Research has the same limit and the same margin. Accepted for now; a per-call wall-clock guard belongs in a change to the shared adapters and `llm/`.
- **L2, invisible Unicode controls** (C1 controls, bidi overrides, zero-width and line separators) survive `text.clean`. The cleaner is shared in behavior with Research's, so this is a separate change to both, flagged for its own session.
- **L3, a symbol with an open Research buy can get an OI buy.** Intended: the PM may weigh agreement between analysts as a signal (ADR 0002). Only the OI's own open reports are left out.
