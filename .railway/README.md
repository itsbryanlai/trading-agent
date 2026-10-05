# .railway

`railway.ts` describes the Railway project as code: a managed Postgres and four services (`orchestrator`, `risk-gate`, `reference-data`, `execution`), all built from the `release/prod` branch ([ADR 0021](../docs/adr/0021-railway-deployment-as-code-observe-only-first.md)).

- **Values are never written into it.** Every variable is `preserve()`, which keeps whatever value is already set on Railway. Set the values in Railway; this file holds names only.
- **Plan before apply.** From this repository, linked to the Railway project, run `railway config plan` to preview the change (values are hidden), then `railway config apply`. Only the owner runs these.
- **The shape is guarded.** `tests/unit/deploy/test_deployed_shape.py` reads `railway.ts` as text and fails if a service holds a variable the layout contract doesn't allow.
- **Dev-only.** `package.json` pins the `railway` SDK for the CLI to evaluate `railway.ts`. Nothing here is deployed.

First deploy, releases, switch-on and switch-off steps: [`docs/operations/deployment.md`](../docs/operations/deployment.md).
