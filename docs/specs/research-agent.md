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

Built by [`specs/007-research-agent`](../../specs/007-research-agent/spec.md).

- A read-only, separately-scoped Finnhub credential
  ([ADR 0008](../adr/0008-dashboard-stack-and-research-provider.md)) for
  general market news and per-symbol company news for an owner-maintained
  watchlist (`config/research.yaml`, which ships empty), plus the day's list of
  US-listed symbols. Kept separate from market-data and broker credentials so a
  compromised or rate-limited news source can't touch trading access. No prices
  ([ADR 0016](../adr/0016-market-data-for-the-llm-agents.md)). Alpha Vantage's
  `NEWS_SENTIMENT` is a documented candidate secondary signal (a pre-computed
  per-article sentiment score to feed `conviction`) if Finnhub's coverage proves
  too thin — not yet wired up.
- One model call per run, to the provider and model in `config/research.yaml`:
  Qwen `qwen3.7-plus` by default, or an Anthropic Sonnet model
  ([ADR 0018](../adr/0018-qwen-as-a-model-provider.md)). No automatic failover
  between providers.
- Its own still-open `reports` rows, to avoid repeating a thesis already made.

## Outputs

One `reports` row per symbol it argues, or a single `no_action` row when a run
argues nothing, every proposal was dropped, or the run failed (see
`docs/specs/data-model.md`). A run's rows are written together or not at all,
and expire at that trading day's close.

- **Direction**: `buy` or `sell` only. Research can't see holdings, so "hold"
  would carry a meaningless target; neutral or mixed news gets no report.
- **Suggested size**: a target weight of equity, the same meaning as the PM's
  `size_pct`. A sell may suggest 0, meaning a full exit.
- **Sources**: every non-`no_action` row carries at least one structured
  source, rebuilt from the article fetched in that run, never from the model's
  text — a thesis with no citable source is not a valid report. Each source is
  marked `primary` (the article names the company) or `secondary` (it's only
  tagged with the symbol, or from its company-news feed); a report resting only
  on secondary sources is allowed, and the PM weighs it accordingly.
- **Rationale**: model-written text, capped in length. It is untrusted: the PM
  and the dashboard treat it as data, never as instructions or markup.

The PM reads Research's reports from the start: as one of the original
analysts, it skips incubation
([ADR 0017](../adr/0017-original-analysts-skip-incubation.md)).

## Cadence

Daily at 08:30 ET, before the open, started by the orchestrator. No intraday or
news-triggered runs.

## Edge cases

- **Everything the model says is checked before it's written.** A proposal is
  dropped, with its reason logged, when its symbol is malformed or not
  US-listed; its direction isn't buy or sell; its conviction isn't 1–5; its
  size is out of range; it cites no article, or one not fetched in this run;
  **none of its cited articles is about the company** — tagged with the
  symbol or from its company-news feed, naming the company in its headline or
  summary, or giving the ticker as `$SYM`, `(SYM)` or `EXCHANGE: SYM` (so an
  injected article can only push a company it names or is filed under); it repeats a symbol already proposed in the run; or
  it matches a still-open report's symbol and direction. If nothing valid is
  left, one `no_action` row says how many proposals were dropped and why.
- **News source unavailable**: if every news fetch fails, or the news key is
  rejected, write a `no_action` row naming the failure rather than silently
  skipping the run — a silent agent and a broken agent must be distinguishable
  in the journal. If only some news is missing, carry on with what arrived, and
  every row names what was missing.
- **Model failures** (rejected key, rejected request, unavailable, refused,
  truncated, or an unusable answer), and any unexpected error, likewise become
  a `no_action` row naming the failure.
- **Slow news**: fetching stops at a deadline, so the run always finishes inside
  the orchestrator's timeout; unfetched feeds count as missing.
- **Same symbol and direction as an already-open report**: not written again; a
  changed direction is.
- **Conflicting sources** (some bullish, some bearish on the same name):
  reflect the disagreement in `rationale_md` and let `conviction` be lower, or
  make no proposal, rather than picking a side arbitrarily.

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
