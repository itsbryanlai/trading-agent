# .railway

`railway.ts` describes the Railway project as code: a managed Postgres and five services (`orchestrator`, `risk-gate`, `reference-data`, `execution`, `journal`), all built from the `release/prod` branch ([ADR 0021](../docs/adr/0021-railway-deployment-as-code-observe-only-first.md)).

- **Values are never written into it.** Every variable is `preserve()`, which keeps whatever value is already set on Railway. Set the values in Railway; this file holds names only.
- **Plan before apply.** From this repository, linked to the Railway project, run `railway config plan` to preview the change (values are hidden), then `railway config apply`. Only the owner runs these.
- **The shape is guarded.** `tests/unit/deploy/test_deployed_shape.py` reads `railway.ts` as text and fails if a service holds a variable the layout contract doesn't allow.
- **One service is a cron job.** `journal` runs once a weekday at 22:30 UTC and is never restarted ([ADR 0022](../docs/adr/0022-journal-writer-runs-after-the-close-with-its-own-finnhub-key.md)); the guard test allows a cron schedule on it alone.
- **Dev-only.** `package.json` pins the `railway` SDK for the CLI to evaluate `railway.ts`. Nothing here is deployed.

First deploy, releases, switch-on and switch-off steps: [`docs/operations/deployment.md`](../docs/operations/deployment.md).
