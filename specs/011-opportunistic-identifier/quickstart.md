# Quickstart: Opportunistic Identifier agent

How to check that feature 011 works, from offline tests to the owner's first real run. Commands, exit codes and output: [contracts/oi-interface.md](contracts/oi-interface.md).

## 1. Offline (anyone, no keys)

- `scripts/lint.sh`: ruff, the layering contract (with `opportunistic_identifier` in the top layer), and module sizes.
- `pytest tests/unit/opportunistic_identifier`: rotation, screening, ranking, answer checks, sources, config and budget, gate equivalence, the service with fakes, the CLI and the import guard.
- `pytest tests/unit/deploy tests/unit/orchestrator`: the schedule and deployed-shape guards with the new variables.
- `pytest tests/integration` with the test database: the write as `ta_opportunistic_identifier`, migration 0014's view, and the nine logins.

Expected: all pass, with no network access.

## 2. The owner's provider check (real Finnhub, no model, no database)

In the owner's own terminal tab, with the key read silently (`read -rs "OPPORTUNISTIC_IDENTIFIER_FINNHUB_API_KEY?Finnhub key: "`, then `export`), run `--check` on a few symbols from the scan list.

Expected: per symbol, the raw metric keys received and the derived values. This confirms:
- the metric key names in [data-model.md](data-model.md#fundamentals-sent-to-the-model);
- that the quote is live (`quote_time` within minutes during the session);
- that eligibility matches what the reference-data job records for the same symbols.

## 3. The owner's real dry run (token measurement, SC-005)

During a session, with the Finnhub key, the DashScope key and the Qwen base URL read the same way, and the scan list filled in, run `--dry-run`. The database login is optional.

Expected: a `slice`, the `skip`s, a `shortlist` of up to 20, any `would_write` rows and drops, and a `summary` with `input_tokens` and `output_tokens`. Nothing is written. Compare the tokens with research O7's estimate (~6k in, ≤3k out) before confirming the model.

## 4. Enabling (a separate one-line PR, after steps 2 and 3)

1. The owner runs the storage login command for `ta_opportunistic_identifier_login`, and sets the five `OPPORTUNISTIC_IDENTIFIER_*` variables on the orchestrator service.
2. The owner applies migration 0014 (open the proxy, migrate, close it), as for every release with a migration.
3. A PR sets `opportunistic_identifier.enabled: true`, then goes through the usual release to `release/prod`.

Expected on the next trading day: six `orchestrator_runs` rows for `opportunistic_identifier`, at least one `reports` row per run, and event-driven PM runs only after runs that argued something.
