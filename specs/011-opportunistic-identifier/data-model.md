# Data model: Opportunistic Identifier agent

No new table, column or grant. The OI writes `reports` rows as `ta_opportunistic_identifier`. It uses the existing `SELECT, INSERT`, and row-level security pins `agent = 'opportunistic_identifier'` (migration 0002). One view changes (migration 0014). Everything else is in memory for one run.

## `reports` rows the OI writes

| Column | Proposal row | `no_action` row |
|---|---|---|
| `agent` | `opportunistic_identifier` (fixed in the INSERT; row-level security enforces it) | same |
| `symbol` | a shortlisted symbol | NULL |
| `direction` | `buy` | `no_action` |
| `conviction` | 1–5 | NULL |
| `suggested_size_pct` | above 0, at most 100: a target weight, as for Research | NULL |
| `sources` | three code-built sources (research O9) | `[]` |
| `rationale_md` | the model's rationale, cut to `rationale_max_chars` | the reason and the run's counts (research O12) |
| `expires_at` | the close of `generated_at`'s trading day | same |

`generated_at` takes the database default. A run's rows are inserted in one transaction (FR-015).

**Still-open reports** (read before ranking): `SELECT symbol FROM reports WHERE agent = 'opportunistic_identifier' AND direction <> 'no_action' AND expires_at > now`.

## Migration `0014_latest_argued_report.sql`

`CREATE OR REPLACE VIEW latest_report_time`, now excluding `direction = 'no_action'` (research O13). It keeps the same name, the same single column and the same grants (`ta_orchestrator`, `ta_assistant`, `ta_dashboard`). The view runs with its owner's rights, as before.

## In-memory entities (one run)

| Entity | Fields | Rules |
|---|---|---|
| `ScanSlice` | `run_index`, `batch`, `batches`, `symbols` | from `rotation.slice_for(universe, today, now, slots, slice_size)`; pure (research O3) |
| `Listing` | `symbol`, `type`, `mic`, `description` | from the US symbol list; `description` (the company name) cut to 100 characters |
| `Fundamentals` | the metric keys below, each `Decimal` or None | from `/stock/metric` |
| `NameData` | `listing`, `profile` (`market_cap_millions`, `currency`, `industry`), `quote` (`current`, `previous_close`, `timestamp`), `fundamentals`, `fetched_at` | one per fetched name |
| `Skip` | `symbol`, `reason` | a reason from the contract's closed set |
| `Candidate` | `symbol`, `move_today`, `below_high`, `rank_move`, `rank_high`, `score`, `data` | eligible, complete, not already open |
| `Shortlist` | up to `shortlist_size` candidates, in score order | only these symbols may be written |
| `Proposal` / `Drop` | as checked by `answer.check` | research O8 |
| `RunCounts` | `in_slice`, `fetched`, `skipped{reason: n}`, `already_open`, `eligible`, `shortlisted`, `proposed`, `written`, `dropped{reason: n}`, `input_tokens`, `output_tokens` | logged; copied into a `no_action` rationale |

## Fundamentals sent to the model

One entry per shortlisted name. Numbers are sent as JSON numbers, and missing values as `null`. The Finnhub metric key each comes from is in brackets. Key names beyond `10DayAverageTradingVolume` and `52WeekHigh` are confirmed by the owner's `--check` run.

| Field | Source |
|---|---|
| `symbol`, `name`, `industry` | symbol list `description`; profile `finnhubIndustry` (untrusted text, ≤100 characters) |
| `price`, `previous_close`, `quote_time` | quote `c`, `pc`, `t` |
| `move_today_pct`, `below_52w_high_pct` | computed (research O5) |
| `high_52w`, `low_52w` | [`52WeekHigh`], [`52WeekLow`] |
| `market_cap_usd`, `avg_daily_dollar_volume_usd` | from `reference.normalize` (research O4) |
| `pe_ttm`, `pb`, `ps_ttm` | [`peTTM`], [`pbQuarterly`], [`psTTM`] |
| `gross_margin_ttm`, `operating_margin_ttm`, `net_margin_ttm`, `roe_ttm` | [`grossMarginTTM`], [`operatingMarginTTM`], [`netProfitMarginTTM`], [`roeTTM`] |
| `revenue_growth_ttm_yoy`, `eps_growth_ttm_yoy` | [`revenueGrowthTTMYoy`], [`epsGrowthTTMYoy`] |
| `debt_to_equity`, `dividend_yield`, `beta` | [`totalDebt/totalEquityQuarterly`], [`currentDividendYieldTTM`], [`beta`] |

Required for a name to be eligible: `price`, `previous_close`, a fresh `quote_time`, `high_52w`, everything `reference.normalize` needs, and at least one of `pe_ttm` or `pb` (research O5).
