# Journal writer

## Purpose

Writes one `journal` row per trading day, after the close: a short, fixed-format
summary of the day and, per analyst agent, a hypothetical book that answers "how
is each agent performing over time" apart from the real blended portfolio
([ADR 0022](../adr/0022-journal-writer-runs-after-the-close-with-its-own-finnhub-key.md)).

Deterministic code, no model call. It measures and judges nothing: the Risk Gate
and Execution never read `journal`, so attribution is never a feedback input
([ADR 0002](../adr/0002-pm-synthesizes-rather-than-analysts-deciding.md)).

Not responsible for: deciding or sizing trades, backfilling missed days, or
writing any narrative with a model (that needs its own ADR).

## Inputs

- The day's rows: account snapshots, reports, decisions with the reports they
  cite, risk verdicts, stop-loss triggers, orders and Execution's refusals.
  Read-only, through the `ta_journal` role.
- The previous `journal` row, whose attribution carries each book's state, so no
  other table is needed.
- Closing prices, one read-only `/quote` per symbol in a book, from Finnhub with
  a key only this job holds. The key cannot trade.
- `config/journal.yaml`: the holding limit, the call pacing, the fetch deadline
  and the closing-auction grace. Changed only through code review.

## Outputs

One row per session, written once (`--replace` rewrites it):
- `equity_open` and `equity_close` from account snapshots. The close is the last
  snapshot Execution recorded, which can be up to about 30 minutes before the
  bell; the summary says "last recorded" and the row stores its time.
- `summary_md`, from a fixed template (`SUMMARY_VERSION`, see
  [versioning](../policy/versioning.md)), at most 2,000 characters. It holds
  numbers, dates, closed-set codes and well-formed tickers only. It carries no
  agent, broker or model text, and no attribution.
- `per_agent_attribution`, with a `schema_version`.

How a book works:
- One book per analyst agent, kept as a return index starting at 100, with no
  dollar amount. Weights are percentages of the book.
- A buy or hold report sets the symbol's target weight to its suggested size; a
  sell lowers it to its own figure (0 is a full exit); `no_action` is ignored.
  Reports apply in the order written, taking effect at that day's close.
- Long-only. Weights above 100% in total are scaled down proportionally. No Risk
  Gate limit applies.
- A holding exits after 5 sessions without a fresh buy or hold report on it
  (`holding_sessions`, configuration), so a missed run never extends a holding.
- Each book is valued at the closes from its stored reference prices. A held
  symbol with no usable close carries its last price and is noted; a new target
  with none is skipped.
- Per agent per day, usage counts sit alongside: reports written, reports the PM
  cited, cited decisions the Risk Gate approved, and those filled.

## Cadence

Its own Railway service on a cron schedule: at 22:30 UTC (after the close in both
summer and winter time) and again at 00:30 UTC, the same New York evening, then
it exits, never restarted. The second start is a retry slot: it does nothing if
the first wrote. It writes only when today, New York time, is a session and the
close has passed. It
runs whether or not trading is paused: during observe-only, the books are the only
performance signal.

## Edge cases and failures

Fail whole, never write half a row. Nothing is written, and the exit code names
the reason, when:
- there is no account snapshot today;
- the market-data key is rejected, or a needed symbol set has no price at all;
- the previous row is dated after today, or its attribution has a schema version
  the code doesn't know;
- the database is unreachable or a read or write fails.

Softer cases are recorded in the row and carried on:
- a quote not stamped inside today's session counts as no price for that symbol,
  so an after-hours print is never used as the close;
- a rate limit or outage is retried a few times, then the symbol goes unpriced;
- missed sessions stay missing; the next row lists them and its books cover
  all the sessions since the last valued close;
- a report written after the close lands in the next day's window, and is counted
  as late if its support session was earlier.

## Interfaces

- Read by: the Portfolio Manager (the last five summaries), the Assistant, the
  dashboard and the next journal run. Never by the Risk Gate or Execution.
- Writes: `journal` only (role `ta_journal`, login `ta_journal_login`).
- Runs as `python -m trading_agent.journal`, with only `JOURNAL_DATABASE_URL` and
  `JOURNAL_FINNHUB_API_KEY`. `--dry-run` prints the would-be row without writing.
  `--check SYMBOL ...` prints each quote and its time against the close, using
  only the key; the owner runs it after a close, before release.

## Non-goals

No backfill, no model-written narrative, no per-trade attribution of real profit
and loss, no input to risk or sizing.

Details: [`specs/012-journal-writer`](../../specs/012-journal-writer/spec.md).
