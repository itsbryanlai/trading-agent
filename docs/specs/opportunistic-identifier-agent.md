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

- Market data (price, volume, fundamentals) across the eligible universe
  defined in `config/risk.yaml` (market-cap floor, minimum average daily
  dollar volume, US-listed common equities only — no OTC, no penny stocks, no
  leveraged/inverse ETFs, no options).
- Its own prior `reports` rows, to avoid re-flagging a name whose report is
  still open and unexpired.

## Outputs

One `reports` row per scan cycle per symbol worth flagging (see
`docs/specs/data-model.md`), plus a `no_action` row per cycle where nothing
cleared the bar — same completeness requirement as Research: a quiet cycle is
still a logged data point.

## Cadence

Intraday polling at a fixed interval (minutes, not seconds or continuous
streaming — day-trading pace, explicitly not HFT; exact interval is an
orchestrator config value, not a design decision baked into this spec).

## Edge cases

- **Universe too large to fully re-scan every cycle**: define a deterministic
  scan order/batching rule (e.g. by sector or by staleness of last scan) so
  coverage is even over time rather than always favoring the same subset —
  the specific rule is an implementation detail, but it must exist and be
  documented in code, not left to run order.
- **A symbol already flagged and still open**: don't re-emit a duplicate row;
  only emit a fresh one if the valuation thesis has materially changed
  (e.g. a large intraday move invalidates the original undervaluation case).
- **Data gap for a symbol** (missing fundamentals, halted trading): skip it
  for that cycle rather than flagging on incomplete data; note the gap only
  if it recurs across many cycles (worth surfacing as a data-quality issue,
  not per-cycle noise).

## Interfaces

- Writes only to `reports` rows where `agent = 'opportunistic_identifier'`.
- Never reads or writes `decisions`, `risk_verdicts`, `orders`, or `positions`.

## Non-goals

- Does not evaluate news/sentiment — a name can be flagged purely on
  valuation/technical grounds with no news angle at all.
- Does not know current portfolio positions or cash — same independence
  argument as Research: its job is to say what the market looks like, not to
  reason about what's already held.
