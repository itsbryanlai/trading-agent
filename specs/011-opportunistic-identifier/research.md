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
3. **Run index:** `k = session_index(today) × S + slot`, where:
   - `S` is the number of slots a day (`slots.count`, default 6);
   - `slot` is `floor((now_ET − slots.first) / slots.every_minutes)`, clamped to `0…S−1`;
   - `session_index` counts XNYS sessions from a fixed epoch, 2026-01-02, using `risk.calendar.is_session`.
4. **Batch fetched:** `k mod B`.

Consecutive run indices cover every batch once in `B` runs, so every name is fetched within `ceil(B / S)` trading days when no slot is missed (SC-003). A missed slot is never backfilled, the same as the orchestrator. That batch's turn simply passes.

The `slots` block in the OI's config must match the orchestrator's schedule entry (`window_start`, `interval_minutes`, and the number of slots in the window). A unit test asserts this, the same way `reference/finnhub.py`'s `SYMBOL_LIST_MICS` is kept equal to the gate's list. The OI reads nothing from `config/schedule.yaml` at runtime.

**Rationale**: the behavior spec requires an even, documented rule "not left to run order". A stateless rule needs no table, which would otherwise be a write beyond `reports` (Constitution III).

**Alternatives considered**:
- **The calendar date as the run index:** weekends and holidays skip indices, so coverage would be uneven for some values of `B`.
- **A "last scanned" table:** a new write and a new grant.
- **Reading `config/schedule.yaml` at runtime:** it would duplicate the orchestrator's parser, or import a sibling.

**Edge**: when the scan list is no bigger than `slice_size`, `B = 1` and every run fetches the whole list.

## O4. Eligibility: the gate's rule on the reference job's derivation

**Decision**: for each fetched name, `screen.py`:
1. builds the row exactly as the reference-data job would, by calling `reference.normalize.normalize(symbol, listing, profile, quote, metrics, now)`. That fixes the type, exchange, market cap, 10-day average dollar volume × previous close, and previous close as share price, all rounded down. Any `Failure` it returns is a skip, under the job's own reason name;
2. applies the universe rule from `config/risk.yaml` (`risk.config.load_config(...).universe`). The listing must be `common_stock` on `rules.US_LISTED_MICS`, and the three floors use the gate's comparisons and the gate's rule names (`universe_listing`, `universe_market_cap`, `universe_dollar_volume`, `universe_share_price`).

The gate's own check, `risk/gate.py:_universe_stop`, is private, and the gate is **not touched** by this feature. The OI has its own five-line copy of the check. A Hypothesis property test asserts that it agrees with `gate._universe_stop` on generated rows and configs, so the two can't drift without a test failing.

**Rationale**: a name the OI flags but the gate rejects wastes a PM decision. Using the same derivation and the same comparisons keeps them aligned.

**Note for the owner**: the OI loads the whole of `config/risk.yaml` with the gate's strict loader, so it refuses to start on a bad file, just as the gate does. It uses only `universe`, and it never logs or sends any other value. Constitution II forbids the **PM** from reading this file. The OI's behavior spec explicitly calls for its universe floors.

## O5. Freshness, completeness and plausibility

**Decision**: a name is skipped (never sent to the model) when any of these holds:
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
- `invalid_size`: not above 0, or above 100;
- `duplicate_symbol`: a second proposal for the same symbol in this run;
- `already_open`: a backstop, since open names were never shortlisted.

An answer that isn't JSON, or isn't the top-level shape, is `unusable_answer` (a failure).

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

**Decision**: the orchestrator's timeout for the OI is 10 minutes (600 s). The OI's config loader refuses any combination whose worst case doesn't fit:

`(3 + 3 × slice_size) / finnhub_calls_per_minute × 60 + model.timeout_seconds + 60 ≤ 600`

Here 60 s is the margin for startup, the symbol list's slow path and the write. With the defaults (60 names, 30 calls a minute, 120 s for the model), that's 366 + 120 + 60 = 546 s.

At runtime, a deadline of `start + 600 − model.timeout_seconds − 60` s also stops fetching, even under 429s or slow responses. Unfetched names count as `not_fetched`, and the run carries on with what it has (FR-003, FR-018). A unit test asserts that the 600 equals `config/schedule.yaml`'s `opportunistic_identifier.timeout_minutes`.

## O11. Pacing and a shared Finnhub account

**Decision**: `finnhub_calls_per_minute` defaults to 30, with the same pacer pattern as Research and feature 004. A 429 backs off and continues until the deadline.

**Owner decision needed** (plan, "Things flagged"): if the OI shares one Finnhub account with the reference-data job (30 a minute) and the PM, their combined pace can exceed the free tier's commonly quoted 60 a minute while an OI run overlaps a PM run. ADR 0016 §5 leaves the account choice to you.

## O12. Exit codes, failures and quiet runs

**Decision**: the same exit codes as Research, so the orchestrator and the owner read them the same way:

| Code | Meaning |
|---|---|
| 0 | Reports written, or a quiet `no_action` (`empty_scan_universe`, `empty_shortlist`, `nothing_argued`, `all_dropped`), or outside the window with nothing done |
| 1 | A failure `no_action` was written, or the close passed mid-run (`window_closed`) |
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
