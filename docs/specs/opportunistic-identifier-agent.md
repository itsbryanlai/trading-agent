# Opportunistic Identifier agent

## Purpose

Actively scans the eligible equity universe for names that look undervalued
relative to where they might go — the "keep checking the market" role from
the original design conversation. Responsible for *finding* an opportunity
and arguing it, not for deciding whether to act — same division of labor as
Research, see [ADR 0002](../adr/0002-pm-synthesizes-rather-than-analysts-deciding.md).

Not responsible for: news/sentiment sourcing (Research's job), sizing
decisions that stick, or placing any order.

## Inputs

Built by [`specs/011-opportunistic-identifier`](../../specs/011-opportunistic-identifier/spec.md).

- **The owner's scan list**: `scan_universe` in `config/opportunistic_identifier.yaml`,
  which ships empty and is changed only through code review. Only US-listed common
  equities (no share-class tickers) fit it; at most 1000 names.
- **Market data**, from the agent's own read-only Finnhub key
  ([ADR 0016](../adr/0016-market-data-for-the-llm-agents.md)): the day's symbol list, then
  each fetched name's quote, profile and fundamentals. The key is separate from the other
  components' keys, but the Finnhub account is shared, so the agent paces itself at 20 calls
  a minute.
- **The universe rules** in `config/risk.yaml` (market-cap floor, minimum average daily
  dollar volume, share-price floor, US-listed common equity only), applied with the Risk
  Gate's own comparisons on the reference-data job's own derivation, so it doesn't flag a
  name the gate would reject. It reads only the universe section.
- **Its own open reports**, to leave out names it has already flagged.
- **One model call per run**, to the provider and model in
  `config/opportunistic_identifier.yaml`: Qwen `qwen3.7-plus` by default, or an Anthropic
  Sonnet model ([ADR 0018](../adr/0018-qwen-as-a-model-provider.md)). No automatic failover.

## How a run works

Design A: plain code screens, and the model reviews a short list.

1. **A slice of the scan list**: a stateless rotation over the trading day's hourly slots
   (10:00-15:00 ET), 40 names a run by default, so every name is seen on a documented, even
   schedule.
2. **Screening, in code**: a name is skipped (never sent to the model) when its data is
   stale, incomplete or implausible. A quote is stale unless its own trade time is from
   today's session and within 15 minutes of the fetch.
3. **Ranking, in code**: names with a still-open report are left out first. The rest are
   ranked twice, by today's move and by their distance below the 52-week high (largest fall
   first), and the two ranks are averaged. The 20 lowest averages form the short list.
4. **One model call** over that short list and its fundamentals, asking which falls look
   like undervaluation. Buy only: the agent never proposes a sale.
5. **Checks, in code**: every proposal must name a short-listed symbol, be a buy, and have a
   conviction of 1-5 and a size above 0 and at most 100. Anything else is dropped and logged.
   A report's sources are built by code from what was fetched, never from the model's text.

## Outputs

One `reports` row per valid proposal, a buy with a conviction, a target weight the PM isn't
bound by, and three sources (see `docs/specs/data-model.md`). Rows expire at the close of
the trading day and are written together or not at all.

A run with nothing to propose writes one `no_action` row naming why, with its counts (names
in the slice, skipped by reason, short-listed, proposals dropped by reason): a quiet cycle
is still a logged data point. A `no_action` row doesn't wake the PM
([`specs/011-opportunistic-identifier`](../../specs/011-opportunistic-identifier/spec.md)
FR-023), so six quiet runs cost no PM runs. A failure writes a `no_action` row naming its
category.

## Cadence

Started by the orchestrator every 60 minutes from 10:00 to 15:00 ET on trading days, with a
15-minute timeout (`config/schedule.yaml`). It ships disabled and is enabled by a separate
change, after the owner's `--check` and `--dry-run` runs have confirmed the data and measured
the model's token use. Hourly polling is day-trading pace, explicitly not HFT.

## Edge cases

- **Universe too large to fully re-scan every cycle**: the stateless rotation above. A list of
  about 240 names is covered every trading day at the defaults.
- **A symbol already flagged and still open**: left out before ranking, so the agent never
  re-emits a duplicate row.
- **Data gap for a symbol** (missing fundamentals, halted trading, a stale quote): skipped
  for that cycle rather than flagged on incomplete data, and counted by reason in the run's
  log and any `no_action` row, so a gap that recurs is visible without per-cycle noise.

## Interfaces

- Writes only to `reports` rows where `agent = 'opportunistic_identifier'`.
- Never reads or writes `decisions`, `risk_verdicts`, `orders`, `positions`, `account_snapshots`
  or `journal`. Its database role can't, and an integration test checks it.
- Only variables named `OPPORTUNISTIC_IDENTIFIER_*`; no broker credential.

## Non-goals

- Does not evaluate news/sentiment — a name can be flagged purely on
  valuation/technical grounds with no news angle at all.
- Does not know current portfolio positions or cash — same independence
  argument as Research: its job is to say what the market looks like, not to
  reason about what's already held.
