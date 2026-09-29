# Reference-data job

## Purpose

Records, once per symbol per trading day, the facts the Risk Gate's universe
check needs: security type, exchange, market cap, average daily dollar volume
and share price. Without today's row for a symbol, the gate rejects any buy of
it ([ADR 0010](../adr/0010-stop-loss-monitor-and-universe-reference-data.md) §3).

Deterministic code, no model call. It records facts and judges nothing: whether
a symbol passes the universe floors is the gate's decision, against
`config/risk.yaml`, which this job never reads.

Not responsible for: deciding what to trade, universe thresholds, live quotes
for trading, backfilling past days.

## Inputs

- The symbols the system touches: held positions, and symbols named in
  reports that are still active or were written since the previous session's
  open, and in decisions since that open. Read through a symbols-only view.
- A seed list in `config/reference_data.yaml`, fetched every trading day.
  Changed only through code review.
- A read-only market-data provider (Finnhub's free tier), with a key only this
  job holds. The key cannot trade.

## Outputs

One `instrument_reference` row per symbol per trading day, never changed once
written and never written for any other day. History is kept.

How values are derived:
- Average daily dollar volume = the provider's 10-day average volume × the
  previous session's closing price.
- Share price = the previous session's closing price, chosen by the quote's own time.
  A quote older than the previous session (e.g. a halted symbol) fails closed.
- Market cap must be reported in US dollars; values are rounded down, never up.
- Security types the provider doesn't clearly label as common stock, ETF or ADR
  become `other`.
- Exchange codes are recorded at the exchange level (Nasdaq tiers become
  `XNAS`); codes it can't map are recorded as sent, so the gate rejects them.

## Cadence

Its own process with its own loop
([ADR 0013](../adr/0013-deterministic-services-run-their-own-loops.md)). On
XNYS trading days, from 08:00 ET until the close:
- The main run is the first check after 08:00. A late start catches up at once.
- Checks every minute pick up newly named symbols and retry failed ones.

This goes beyond ADR 0010's "once per trading day": each symbol is still
recorded once a day, but symbols named during the day are fetched the same day.
Nothing is fetched on non-trading days or after the close.

## Edge cases and failures

Fail closed, per symbol. A missing row costs a skipped buy; a wrong row costs
real exposure. Any of the following writes nothing for that symbol today:
- a provider error, or a 403 for that one symbol;
- a stale quote, or a market cap reported in another currency;
- a symbol listed twice inconsistently;
- a share-class ticker such as BRK.B (the provider was seen to return the other
  class's volume);
- a missing or zero value;
- a market cap above $20 trillion (a unit error);
- a dollar volume above the market cap;
- a value that doesn't fit its column;
- an insert error.

After a failure, the symbol is logged, backed off (5, 10, 20, then every
30 minutes) and retried. Yesterday's row is never used in its place.

Other failures:
- **Rate limit:** the job slows down and carries on next minute.
- **Key rejected at startup:** the process refuses to start. A provider outage at
  startup doesn't stop it; the first run retries.
- **Key rejected later:** one error per run, and a retry 15 minutes later.
- **Lost database connection:** the process exits so the platform restarts it.
- **Implausible tickers:** skipped and logged safely (quoted), never sent to the provider.

Sells and stop-loss exits never depend on this data, so no failure here can
block an exit.

At the open, the job logs a warning naming every candidate still without data.

## Interfaces

- Read by: the Risk Gate (universe check), the Assistant and the dashboard.
- Writes: `instrument_reference` only, insert-only (role `ta_reference_data`).
- Runs as `python -m trading_agent.reference`, with only its own two environment
  variables. `--check SYMBOL...` prints what it would record without touching
  the database. The owner runs this once before deploying, to confirm the
  provider's units and labels.

## Non-goals

No UI for the seed list, no intraday refresh of a recorded row, no second
provider, and no universe judgement.

Details: [`specs/004-reference-data`](../../specs/004-reference-data/spec.md).
