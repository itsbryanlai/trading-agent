# 0022. The journal writer is a deterministic job, run after the close, with its own Finnhub key

Status: proposed

Proposed 2026-10-08. The owner chose every decision below in the session that opened `specs/012-journal-writer`.

## Context

The `journal` table, its `ta_journal` role and its grants have existed since `specs/001-data-model`. Nothing writes it yet. `docs/specs/data-model.md` says it holds a daily narrative and per-agent attribution, "what the portfolio would look like sized purely off one agent's reports", written once a day after the close. Three things are undecided, and each changes the system's shape:

- **Prices.** Attribution must value each agent's hypothetical holdings at the close. The only prices in the database are the PM's decision quotes and Execution's fills, which cover a few symbols on a few days. The journal holds no market-data credential, and [0016](0016-market-data-for-the-llm-agents.md) gives read-only Finnhub keys to the PM and the Opportunistic Identifier only.
- **Who writes the narrative.** The PM reads the last five `summary_md` values as input (`specs/008-portfolio-manager`, research P6). A model-written summary would be one more model's text in the PM's prompt.
- **Where it runs.** The orchestrator schedules LLM agents only ([0003](0003-orchestrator-is-a-scheduler-not-an-authority.md), [0013](0013-deterministic-services-run-their-own-loops.md)). Each Railway service holds only its own credentials ([0021](0021-railway-deployment-as-code-observe-only-first.md)).

## Decision

1. **The journal writer is deterministic code.** No model call. `summary_md` comes from a fixed Markdown template built from the day's rows. Adding a model-written narrative later needs its own ADR.
2. **It is its own Railway service, `journal`, started on a cron schedule.** It runs once and exits, after the close on weekdays (about 22:30 UTC, after 16:00 ET in both summer and winter time). It asks `trading_agent.risk.calendar` whether the day was a session and does nothing if it was not. It writes one row per `trading_day` as an upsert, so a repeated or manual run is harmless.
3. **It gets its own read-only Finnhub key, `JOURNAL_FINNHUB_API_KEY`,** and a new login, `ta_journal_login`, given to the service as `JOURNAL_DATABASE_URL`. This follows 0016's reasoning: a Finnhub key cannot trade. Closing prices come from Finnhub's `/quote`. Alpaca stays Execution's alone.
4. **It runs whether or not trading is paused.** It is measurement. During observe-only, the hypothetical books are the only performance signal.
5. **No backfill.** A missed day stays missing, and the next run logs the gap. Each row carries the state the next day needs, so no new table is added.
6. **Attribution remains measurement only.** The Risk Gate and Execution never read `journal` (FR-012). This ADR changes no grant on either.

## Alternatives considered

- **The reference-data job records daily closes in a new table, and the journal reads it.** No new credential for the journal, but reference-data's job would grow beyond the universe, and the schema would gain a table and a dependency between the two jobs.
- **Use only prices already in the database.** Rejected: most symbols in a hypothetical book would have no price on most days.
- **A model-written summary (Anthropic or Qwen, [0018](0018-qwen-as-a-model-provider.md)).** Not chosen: it puts another model's text into the PM's input, costs money every day, and cannot be tested against fixed output.
- **An always-on service with a 60-second loop**, like Execution, the Risk Gate and reference-data. Not chosen: a whole day's process for one write a day.
- **Scheduled by the orchestrator.** Rejected: the orchestrator schedules LLM agents, and this job is not one.

## Consequences

- **A fifth worker service.** `.railway/railway.ts`, `tests/unit/deploy/test_deployed_shape.py` and `specs/010-observe-only-deployment/contracts/service-layout.md` gain a `journal` entry holding only `JOURNAL_*` variables.
- **Not yet verified, checked in the plan:**
  - that `railway/iac` can express a cron schedule. If it cannot, the fallback is decided by a new ADR, not quietly in code;
  - that Finnhub's `/quote` after the close returns the session's closing price.
- **Only today can be valued**, because `/quote` gives only the current and previous close. Finnhub's free tier may also offer historical candles, but reports conflict, so nothing depends on them.
- **The Finnhub account's rate limit may be shared** with the other keys (0016 §5). The journal runs after the close, when the others are mostly idle.
- **`equity_open` and `equity_close` stay flat during observe-only**, because there are no fills.
- **No constitution amendment.** Finnhub is already a named credential, and the deployment bullet already says one worker service per process.
