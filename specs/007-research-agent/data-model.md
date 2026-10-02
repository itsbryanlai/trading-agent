# Data model: Research agent

Research adds no table and no grant. It writes rows to the existing `reports` table ([`docs/specs/data-model.md`](../../docs/specs/data-model.md), migration 0002) as `ta_research`. Migration 0011 changes one check constraint.

## `reports` rows written by Research

| Column | Value |
|---|---|
| `agent` | `'research'` (row-level security enforces it) |
| `generated_at` | database default `now()` |
| `symbol` | the checked ticker; `NULL` for `no_action` |
| `direction` | `buy` or `sell`; or `no_action` (never `hold`, see the spec's Clarifications) |
| `conviction` | 1–5; `NULL` for `no_action` |
| `suggested_size_pct` | target weight of equity, rounded down to 3 places. Buy: above 0, at most 100. Sell: 0 to 100, where 0 is a full exit. `NULL` for `no_action` |
| `sources` | JSON array of `{title, url, publisher, published_at, relevance}`, copied from the fetched articles (FR-008); `relevance` is `primary` (names the company) or `secondary` (only tagged or from its feed); `[]` for `no_action` |
| `rationale_md` | the model's rationale, cut to `rationale_max_chars`, plus a "Missing news" line when some news is missing. For `no_action`, a fixed sentence (research R8) |
| `expires_at` | `calendar.close_time(today)`: that day's close, early closes included |

A run writes either:
- one or more `buy` or `sell` rows; or
- exactly one `no_action` row: nothing to argue, everything dropped, or a failure.

It writes them in one transaction.

## Migration 0011: `reports_suggested_size_range`

Before (0002):

```text
no_action → suggested_size_pct IS NULL
otherwise → suggested_size_pct > 0 AND <= 100    (a NULL passes: CHECK accepts null)
```

After (0011):

```text
no_action → suggested_size_pct IS NULL
sell      → suggested_size_pct IS NOT NULL AND >= 0 AND <= 100
buy, hold → suggested_size_pct IS NOT NULL AND > 0 AND <= 100
```

- **Loosened:** a sell may now suggest 0, meaning a full exit (spec Clarifications).
- **Tightened:** a null size on an actionable row was accidentally allowed, and now isn't (research R13). Both changes apply to both analysts' rows.
- **Implementation:** the migration drops and re-adds the named constraint. Existing rows are checked when it's re-added. No rows exist yet outside tests.

## In-memory entities (never stored)

**Article** (`selection.Article`):
- `id`: `A1`… per run;
- `title`, `url`, `publisher`, `published_at` (aware datetime), `related` (tuple of tickers) and `summary` (cut to `article_summary_max_chars`);
- `feed`: `general` or the watchlist symbol.

**ModelInput**: `system` (the fixed prompt plus the schema), `user` (the JSON document from research R7), `article_ids` (the set given to the model) and `input_chars`.

**Proposal**: one item of the model's answer as received. It becomes a **CheckedReport** (`symbol`, `direction`, `conviction`, `size` (Decimal), `rationale`, `sources`) only if every check in research R6 passes.

**Drop**: `(index, symbol or None, reason)`. `reason` is one of the closed set in research R6.

**RunOutcome**:
- `rows`: the CheckedReports, or one no_action;
- `drops`;
- `missing`: the feeds that failed;
- `failure`: a category, or none;
- `input_tokens`, `output_tokens`.

`__main__` maps it to an exit code (research R9).

## Reads

| What | How | Grant |
|---|---|---|
| Its own still-open reports | `SELECT symbol, direction FROM reports WHERE agent = 'research' AND expires_at > now() AND direction <> 'no_action'` | existing `SELECT` on `reports` |

Nothing else is read from the database: no positions, cash, decisions, verdicts or orders (FR-001). An integration test asserts `ta_research` still can't read them.
