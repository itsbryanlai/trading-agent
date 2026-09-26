# Research agent

## Purpose

Sources news and sentiment on US equities and turns it into structured
proposals for the Portfolio Manager to weigh. Responsible for *finding and
arguing a thesis*, not for deciding whether to act on it —
[ADR 0002](../adr/0002-pm-synthesizes-rather-than-analysts-deciding.md).

Not responsible for: sizing decisions that stick (its `suggested_size_pct` is
a guess the PM isn't bound by), technical/valuation screening (that's the
Opportunistic Identifier's job), or placing any order.

## Inputs

- A read-only, separately-scoped Finnhub credential
  ([ADR 0008](../adr/0008-dashboard-stack-and-research-provider.md)) for
  per-symbol company news. Kept separate from market-data and broker
  credentials so a compromised or rate-limited news source can't touch
  trading access. Alpha Vantage's `NEWS_SENTIMENT` is a documented candidate
  secondary signal (a pre-computed per-article sentiment score to feed
  `conviction`) if Finnhub's coverage proves too thin — not yet wired up.
- Its own prior `reports` rows (recent history, to avoid repeating a thesis
  already made and not yet expired).

## Outputs

One `reports` row per run (see `docs/specs/data-model.md`), including runs
that surface nothing (`direction = 'no_action'`). Every non-`no_action` row
must carry at least one structured `sources` entry — a thesis with no citable
source is not a valid report.

## Cadence

~Daily (pre-market), plus triggered runs when the news-data source surfaces
something time-sensitive. Exact trigger mechanism is an implementation detail
the orchestrator's schedule config owns.

## Edge cases

- **News source unavailable**: log a `no_action` row noting the failure in
  `rationale_md` rather than silently skipping the run — a silent agent and a
  broken agent must be distinguishable in the journal.
- **Same symbol as an already-open report from a prior run**: only emit a new
  row if the thesis has materially changed; otherwise let the existing row
  stand until it expires.
- **Conflicting sources** (some bullish, some bearish on the same name):
  reflect the disagreement in `rationale_md` and let `conviction` be lower
  rather than picking a side arbitrarily.

## Interfaces

- Writes only to `reports` rows where `agent = 'research'`
  ([ADR 0004](../adr/0004-shared-postgres-role-scoped-credentials.md)).
- Never reads or writes `decisions`, `risk_verdicts`, `orders`, or `positions`.

## Non-goals

- Does not evaluate valuation/fundamentals for undervaluation — that's the
  Opportunistic Identifier.
- Does not know the current portfolio's positions or cash — the PM is the
  only agent that reasons with that context, to keep Research's proposals
  independent of what's already held (avoiding a different kind of bias:
  proposing based on "what would help my existing position" rather than the
  news itself).
