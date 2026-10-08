# Contract: `python -m trading_agent.journal`

One run, then exit. Started by Railway's cron on the `journal` service (research J1), or by the owner.

## Invocation

| Form | Does | Needs |
|---|---|---|
| (no arguments) | writes today's row if today is a session and it has closed (J3) | `JOURNAL_DATABASE_URL`, `JOURNAL_FINNHUB_API_KEY` |
| `--replace` | as a plain run, but replaces today's row if it already exists (FR-003) | both |
| `--dry-run` | everything except the write; prints the would-be row as JSON lines | both |
| `--check SYMBOL [SYMBOL …]` | fetches each quote; prints `c`, `t`, today's open and close, and whether J2 accepts it; writes nothing | `JOURNAL_FINNHUB_API_KEY` only |

Any other arguments, or a symbol not matching `^[A-Z][A-Z0-9.\-]{0,9}$`: exit 2. At most 20 symbols per `--check`.

## Environment

Only `JOURNAL_*` names (ADR 0015's convention, ADR 0022). A missing one is named, never echoed. Values are never logged.

| Variable | Use |
|---|---|
| `JOURNAL_DATABASE_URL` | the `ta_journal_login` connection string |
| `JOURNAL_FINNHUB_API_KEY` | read-only market data |

## Configuration: `config/journal.yaml`

All keys are required, and unknown keys are refused (exit 2). It's changed only through code review.

| Key | Default | Bounds |
|---|---|---|
| `holding_sessions` | 5 | 1–60 |
| `finnhub_calls_per_minute` | 20 | 1–300 |
| `fetch_deadline_seconds` | 480 | 30–540 |
| `close_grace_minutes` | 5 | 0–30 |

## Exit codes

Research J12: 0 wrote or nothing to do; 1 named failure, nothing written; 2 refused to start; 3 database; 4 crashed.

## Logs

One line per event, at INFO unless noted:
- **Start:** `journal: start trading_day=… summary_version=0.1 schema_version=1`.
- **Nothing to do:** `journal: nothing to do: not_a_session|before_close|already_written`.
- **Missed sessions:** `journal: missed sessions: …` (WARNING).
- **Each unpriced symbol:** `journal: unpriced SYMBOL: stale|after_close|no_price|not_permitted|rate_limited|unavailable|deadline` (WARNING). A malformed symbol is never logged as text: `journal: unpriced malformed symbols: N` (WARNING).
- **Done:** `journal: wrote trading_day=… agents=N symbols_priced=P/Q sessions_covered=S`.
- **Failure:** `journal: failed: <reason>` (ERROR). Exceptions are logged by type only.

Railway labels these stderr lines "error"; read the text.
