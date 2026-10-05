# 0021. Railway deployment as code, observe-only first

Status: accepted

Accepted by the owner on 2026-10-05, with the plan for `specs/010-observe-only-deployment`. Every decision below was made by the owner during `/speckit-clarify` and `/speckit-analyze` (research R0-R12).

## Context

Nothing is deployed yet. The first deployment should show the owner the Portfolio Manager's real decisions and the gate's verdicts, without any order reaching the broker.

Three things forced a decision:

- Railway's Config as Code (`railway.json` / `railway.toml`) is deprecated: new services cannot opt into it, and existing files stop being read on 2026-12-01. The constitution's deployment bullet names `railway.json`.
- A service's variables are visible to every process in it, so processes with different credentials must not share a service (Constitution III).
- Execution is the only writer of `account_snapshots` and `positions`. The Portfolio Manager fails a run with `no_account_snapshot` without one, and the gate rejects without today's snapshot. With Execution undeployed there would be no decisions and no verdicts. Spec 005, though, says no Portfolio Manager run starts while trading is paused.

## Decision

1. **Railway infrastructure as code.** The project is described in one TypeScript file, `.railway/railway.ts`, applied by the owner with `railway config plan` and `railway config apply`. Variable values are `preserve()`d: they stay on Railway, never in the file. Its `package.json` sits under `.railway/` so the repository is not detected as a Node project. `railway.json` is not used.
2. **One service per process.** Four services, `orchestrator`, `risk-gate`, `reference-data` and `execution`, plus the managed `postgres`, one replica each. Each holds only its own variables. The orchestrator also carries its agents' variables, as ADR 0015 requires. Broker keys exist only on `execution`.
3. **Build and start.** Railpack, Python pinned to 3.12 by `.python-version`, an editable install through `requirements.txt` (`-e .`) so config paths resolved from the repository root keep working. Each service starts the existing entry point from the repository root; Railway's default on-failure restart applies.
4. **Setup runs from the owner's machine.** The owner opens the database's public TCP proxy, runs the migration runner and a new storage-layer login command with the admin credential exported in their shell only, then closes the proxy. The admin credential is in no service. Each release with a migration repeats the open, migrate, close sequence.
5. **Deploys only from `release/prod`.** Every service's source is that branch. Merging `main` into `release/prod` is the deploy action; merging to `main` alone deploys nothing.
6. **Observe-only means** Execution deployed, trading paused before any service starts, and a flat paper account before Execution's first start. The owner closes every position and cancels every open order in the broker's own interface. A pre-open check, run as the owner's read-only login, confirms the pause is set and `positions` is empty.
7. **A new setting, `portfolio_manager.run_while_paused`,** in `config/schedule.yaml`, required like every key. When true, the orchestrator does not block Portfolio Manager runs for a known `trading_paused = true`. This reverses spec 005's "no PM run starts while paused" for observation. An unreadable or missing flag still blocks (fail closed). Analyst runs are unchanged. It is a schedule change, not order logic. It ships as `true`.
8. **No buy is approved while paused.** The gate rejects every buy while paused: `market_closed`, then `decision_stale`, then `trading_paused`, all before any sizing, universe or cash rule. The owner accepted this: observation shows the Portfolio Manager's real decisions and a verdict for each, never the gate's unpaused judgment. Nothing lingers in `in_flight_orders`.
9. **Switch-on after the close, in two steps.** First, while still paused, a reviewed change sets `run_while_paused: false` (and updates its guard test), released through `release/prod`. Then, after the close and before the next open, the owner clears the pause. Approvals made while observing therefore can never become orders.
10. **Switch-off has two forms.** Pausing stops buys; sells and stop-loss exits continue. Removing Execution requires being after the close with no positions, no open orders and `in_flight_orders` empty; then the service is deleted from `.railway/railway.ts` and applied.
11. **The constitution's deployment bullet is amended** (1.1.0 to 1.1.1, PATCH) to name infrastructure as code and one worker service per process. No principle changes.

## Alternatives considered

- **Leave Execution undeployed for the first deployment** (the first answer in clarify). Produces no snapshots, so no decisions and no verdicts.
- **A "no-submit" mode in Execution.** New order logic, in the riskiest component.
- **A separate snapshot-only process with broker keys.** A second holder of broker credentials, which Constitution III forbids.
- **A synthetic account snapshot seeded by hand.** Decisions would rest on made-up equity and need a manual step every day.
- **`railway.json` per service.** Deprecated, refused for new services, and unread from 2026-12-01.
- **`.railway/railway.py`.** The Python form is beta, and Railway says its helper names may change. The owner chose the generally available TypeScript form.
- **Dashboard-only configuration.** No reviewable record of what is deployed.
- **A shared service for several processes.** One service's variables are visible to every process in it, which would put broker keys next to the agents' keys.
- **A pre-deploy migration step in a service.** It would put the admin credential into a hosted service.
- **Auto-deploy from `main`.** Every merge would deploy.

## Consequences

- **The owner can read the Portfolio Manager's real decisions and a verdict for each, with no approved buy.** What the gate would have decided unpaused is not shown; a read-only replay is a possible later feature.
- **Reviewable, explicit infrastructure.** One file records what is deployed, and only an owner command applies it. A guard test checks it offline: broker names only on `execution`, no admin credential, every value `preserve()`d, `release/prod` as the source, and `run_while_paused: true` in the schedule.
- **A TypeScript file and an npm dev dependency in a Python repository**, evaluated on the owner's machine and never deployed.
- **Spec 005's pause behaviour has an exception.** While the setting is true, a pause does not stop the Portfolio Manager. If the setting is left on after switch-on, a re-pause would stop buys but not the Portfolio Manager's sell decisions, which is why it is turned off before unpausing.
- **Execution would place stop-loss exits and approved sells for held positions even while paused**, so a flat account is a hard precondition, not a courtesy.
- **Releases with a migration or a `.railway/` change take extra owner steps**, listed in the runbook's release checklist.
- **No risk limit, sizing rule or order logic changes.** The daily-loss breaker is untouched.
