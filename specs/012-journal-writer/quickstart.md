# Quickstart: Journal writer

How to prove the feature works, from tests to the first scheduled run. Commands run from the repository root. The owner does every secret step in their own terminal.

## 1. Tests

The touched modules, while building (CLAUDE.md, two tiers):

```bash
python -m pytest tests/unit/journal -q
```

```bash
scripts/lint.sh
```

The full unit and integration suites run once, before pushing. Integration tests need `TEST_DATABASE_URL`, pointing at a throwaway Postgres, never the owner's port 5433 container.

Expected: the book arithmetic matches hand-computed fixtures over several sessions (SC-002); no summary contains planted rationale, reasoning, source or broker text (SC-003); a repeated run leaves one identical row (SC-004); the login can write only `journal` and can't read `system_state`.

## 2. The login and the key (owner)

1. Create `ta_journal_login` with the logins command, as for the others (`docs/operations/deployment.md`, open the proxy, run, close it).
2. Get a read-only Finnhub key for the journal, or reuse the shared account's (ADR 0016 §5).
3. Set `JOURNAL_DATABASE_URL` and `JOURNAL_FINNHUB_API_KEY` on Railway's `journal` service after step 5 creates it, and locally for steps 3–4 with `read -rs`.

## 3. Check the quote after a close (owner, ADR 0022's open item)

After 16:05 ET on a session day, with symbols of your choosing:

```bash
python -m trading_agent.journal --check AAPL MSFT
```

Expected: each line shows `t` inside today's session and `accepted: true`. If `t` is after the close (after-hours prices), J2 refuses those quotes, and the scheduled run would fail as `no_prices`. Stop and tell me.

## 4. Dry run against production (owner)

The same evening, with `JOURNAL_DATABASE_URL` pointing at production through the proxy:

```bash
python -m trading_agent.journal --dry-run
```

Expected: the would-be row. The equity is flat (observe-only); there's a book per agent with today's reports; the summary is under 2,000 characters and holds no agent text. Nothing is written.

## 5. Deploy

Release through `release/prod` as usual. There's no migration. Then:

```bash
railway config plan
```

Expected: one new service, `journal`, with a cron schedule `30 22 * * 1-5`, restart policy `NEVER`, and only the two `JOURNAL_*` variables. Then `railway config apply`, and set the two values.

## 6. The first scheduled run

The next session evening, Railway's `journal` service logs `journal: wrote trading_day=…`. The row is visible to the owner's read login:

```sql
SELECT trading_day, equity_open, equity_close, length(summary_md), per_agent_attribution->'agents' FROM journal ORDER BY trading_day DESC LIMIT 1;
```

The PM's next run includes it in its input's `journal` list (visible in a PM `--dry-run`).
