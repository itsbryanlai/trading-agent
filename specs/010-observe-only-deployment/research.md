# Research: Observe-Only Deployment

Feature: [spec.md](spec.md). Each item: decision, rationale, alternatives. Revised after `/speckit-analyze` (C1–C11). R0 records why the design changed.

## R0. Observing needs Execution running (analyze C1)

**Finding**: Execution is the only writer of `account_snapshots` and `positions`:
- the pre-open snapshot, a snapshot every 30 minutes (ADR 0014), and one before each buy (`execution/service.py`);
- the Portfolio Manager fails a run with `no_account_snapshot` when none exists (`portfolio_manager/service.py`, `_run`);
- the Risk Gate rejects without today's snapshot and the daily baseline.

With Execution undeployed, observation would produce no decisions and no verdicts.

**Decision**: Deploy Execution with trading paused, and add one reviewed orchestrator setting so the Portfolio Manager runs while paused. Execution already:
- takes its snapshots whatever the pause says;
- would refuse an approved buy as final `trading_paused` while paused (spec 003 FR-018). In practice none reaches it, because the gate itself rejects every buy as `trading_paused` first (`risk/gate.py`, `_account_stop`; analyze N1). Every buy verdict while observing is therefore that rejection: the owner sees the PM's real decisions, but not the gate's unpaused judgment (owner accepted, Clarify; a read-only replay is a possible later feature). No approval lingers in `in_flight_orders`;
- places sells only for held positions and stop-loss exits. A flat account (R9) means there is nothing to sell.

**Alternatives**:
- A "no-submit" mode in Execution: new order logic, the riskiest place for a bug.
- A separate snapshot-only process with broker keys: a second holder of broker credentials, which Constitution III forbids.
- A synthetic snapshot seeded by hand: decisions rest on made-up equity and need a daily manual step.

## R1. Railway configuration: infrastructure as code, not `railway.json`

**Decision**: Define the Railway project in one `.railway/railway.ts` (TypeScript, generally available; the owner chose this over the beta Python form), applied by the owner with `railway config plan` and then `railway config apply`. Its `package.json` lives inside `.railway/`, not at the repository root.

**Rationale**: Railway's docs (fetched 2026-10-05) say Config as Code (`railway.json` / `railway.toml`) is deprecated. New services cannot opt into it, and existing files stop being read on 2026-12-01. Infrastructure as code describes every service in one file and holds variable names as `preserve()`, so values stay on Railway and never in the file. It is applied only by an explicit owner command. Keeping `package.json` under `.railway/` stops Railpack from detecting the repository as a Node project.

**Alternatives**: `railway.json` per service: deprecated, refused for new services. `.railway/railway.py`: beta, and Railway says "helper names may change". Dashboard-only configuration: no reviewable record of what is deployed.

## R2. Build: Railpack, Python 3.12, editable install, repository root as the working directory

**Decision**: Pin Python with `.python-version` (`3.12`). Install with a `requirements.txt` holding `-e .`. Every start command runs from the repository root, which is Railpack's application directory.

**Rationale** (corrected, analyze C4 and C5):
- Railpack defaults to Python 3.13.2 unless a version file pins it. CI and local tests run 3.12.
- Research, the Portfolio Manager, the orchestrator and the reference-data job find their config at `Path(__file__).parents[3]`, the repository root. That holds for an editable install and fails for a normal `pip install .` into site-packages.
- The Risk Gate and Execution read `Path("config/risk.yaml")` relative to the working directory, so their start commands must run from the repository root. The rehearsal checks this (T023).
- The orchestrator's launcher does pass `PYTHONPATH` to agents (`BASE_ENVIRONMENT`, ADR 0015 §2). That's not the reason for the editable install; the config paths are.

**Alternatives**: a custom build command per service: four copies of the same thing. Changing the config loaders to use package data: a behaviour change to five components for a deployment concern.

## R3. One service per process

**Decision**: Four services, `orchestrator`, `risk-gate`, `reference-data` and `execution`, all from the same repository. Each has its own start command and only its own variables; the orchestrator's also carries its agents' variables (ADR 0015). There is also the managed `postgres`. All run one replica.

**Rationale**: Clarify Q1. A service's variables are visible to every process in it. Each process already holds a single-instance lock, so a second replica would refuse to start rather than double-run.

## R4. Start commands and restarts

**Decision**: The existing entry points:
- `python -m trading_agent.orchestrator`
- `python -m trading_agent.risk`
- `python -m trading_agent.reference`
- `python -m trading_agent.execution`

Restarts use Railway's default on-failure policy.

**Rationale**: Each already exits 2 when it refuses to start (missing or bad variable, named in the log, value never printed) and 3 on a lost database, so the platform restarts it (ADR 0013). Execution exits 2 when it isn't on the paper account. FR-006 and FR-007 need no new code.

## R5. Database setup from the owner's machine, then close the public address

**Decision**: The owner enables Railway Postgres's public TCP proxy, then runs the existing `python -m trading_agent.storage.migrate` with `ADMIN_DATABASE_URL` (the proxied address) exported in their shell only. They then run the login command (R6) and set the pause (R8), and disable the proxy again. Each later release with a migration repeats the open, migrate, close sequence. No service has a pre-deploy step.

**Rationale**: Clarify Q2 and analyze C6. The runner is already idempotent. The superuser listening on a public address is exposure worth closing between uses. Railway's newest Postgres image may be newer than CI's 16, so the rehearsal applies the migrations to a container of the same major version first.

## R6. Login command

**Decision**: A new storage-layer command, `python -m trading_agent.storage.logins`, run by the owner with `ADMIN_DATABASE_URL`.
- It creates one login role per component group role (orchestrator, research, portfolio manager, risk gate, reference data, execution), plus `ta_owner_read_login` (in `ta_dashboard`) and `ta_owner_control_login` (in `ta_dashboard_control`).
- Passwords are random, from `secrets.token_urlsafe(32)`. Each connection string is printed once.
- Names follow `ta_<component>_login`, the existing quickstart convention.
- An existing login is reported "exists, unchanged". A password changes only with `--reset <name>`.
- Printed strings use the host the owner gives (`--service-host`, the database's private network address).

**Rationale**: Clarify Q3 and Q4. It sits in `storage` beside `migrate`, the other admin-credential command, with no upward import. Both owner logins reuse existing roles, so there are no new grants.

## R7. Approvals made while observing never become orders

**Decision**: Clear the pause only after the close and before the next open.

**Rationale**: Verified in code (analyze confirmed it):
- While paused, the gate rejects every buy, and Execution would refuse one anyway.
- The only window is a decision evaluated in the last pass before the pause is cleared. Clearing after the close removes it: `submittable` requires the approval's own trading day with the market open, `expiry_check` refuses the day's approvals from the close, and `lapse_today` marks them expired once `MAYBE_PLACED_WAIT` has passed (analyze C11), and the gate rejects with `market_closed` outside hours, so no approval exists until the next session.

## R8. The pause, the observe setting, switch-on and switch-off

**Decision**:
- **Observe setting**: a new boolean `portfolio_manager.run_while_paused` in `config/schedule.yaml`, default `false`. The loader requires it, like every key. When `true`, the planner doesn't block PM runs for a known `paused = true`. An unreadable flag (`None`) still blocks (fail closed). Analyst runs are unchanged.
- **Setup**: right after the login command, the owner runs `UPDATE system_state SET trading_paused = true, updated_at = now()` as `ta_owner_control_login`, before any service exists.
- **Switch on**, in this order (analyze N3): first, while still paused, a reviewed change sets `run_while_paused: false` (and updates the guard test), released through `release/prod`. Then, after the close and before the next open, clear the pause with the same login. A pause at any later time stops the PM again (spec 005 behaviour). Doing it the other way round would leave a window where a re-pause doesn't stop the PM's sell decisions.
- **Switch off**:
  - Pausing stops buys, while sells and stop-loss exits continue.
  - Removing Execution requires being after the close with no positions, no open orders and `in_flight_orders` empty (analyze C3). Then delete the service from `.railway/railway.ts` and apply.

**Rationale**: the pause is the constitution's manual toggle (IV), already enforced by Execution for buys. The setting is a schedule change, not order logic, but it reverses a spec 005 behaviour, so ADR 0021 records it and spec 005's pause section is updated to reference it.

## R9. The paper account must be flat before Execution first starts

**Decision**: The runbook's first-deploy order is:
1. migrate, logins, pause;
2. the owner closes every position and cancels every open order in Alpaca's own interface (an owner action, never a component's);
3. confirm Alpaca shows zero positions and zero open orders;
4. only then deploy.

Right after the first `railway config apply`, before the next open, a **pre-open check** as `ta_owner_read_login` confirms that `trading_paused` is true and the `positions` table is empty (Execution mirrors broker positions at startup). If either fails, remove Execution before the open (analyze N2, N6). A held position would trade through PM sell decisions (the gate's `_sell` doesn't check the pause) and stop-loss exits.

**Rationale**: The owner confirmed the account currently holds positions. Execution places stop-loss exits and approved sells while paused, so held positions could trade during observation.

## R10. Deploying from `release/prod`

**Decision**: Each service's source is `github("itsbryanlai/trading-agent", { branch: "release/prod" })`. Deploying is a pull request from `main` into `release/prod` (CI runs on pull requests), then a merge. A release that changes `.railway/` also needs `railway config apply`. A release with a migration needs R5's open, migrate, close first. The runbook's release checklist fixes that order.

## R11. A guard test for the deployed shape

**Decision**: An offline test reads `.railway/railway.ts` as text and fails if:
- `ALPACA_` or `EXECUTION_` names appear outside the `execution` service;
- `ADMIN_DATABASE_URL` appears;
- the database's own connection variables are referenced;
- a value isn't `preserve()`;
- a service's variable names differ from the contract;
- a GitHub source isn't on `release/prod`.

It also checks that `config/schedule.yaml` has `run_while_paused: true`, so observing can't silently stop, and that every declared name is in `.env.example`.

## R12. The pre-open and post-deploy checks

**Decision**: `docs/operations/observe-queries.sql` includes a "pre-open" block (paused is true, `positions` is empty; run right after the first deploy) and a "first trading day" block, run as `ta_owner_read_login`:
- `system_state.trading_paused` is true;
- `positions` is empty;
- at least one account snapshot exists today;
- every decision has a verdict;
- every buy verdict is a `trading_paused` rejection;
- `orders` is empty.

The runbook says to run it after the first trading day (SC-002, US3 scenario 4; analyze C7).
