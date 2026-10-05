# Research: Observe-Only Deployment

Feature: [spec.md](spec.md). Each item: decision, rationale, alternatives.

## R1. Railway configuration: infrastructure as code, not `railway.json`

**Decision**: Define the Railway project in one `.railway/railway.ts` (TypeScript, generally available), applied by the owner with `railway config plan` / `railway config apply`. Its `package.json` lives inside `.railway/`, not at the repository root.

**Rationale**: Railway's docs (fetched 2026-10-05) say Config as Code (`railway.json` / `railway.toml`) is deprecated. New services cannot opt into it, and existing files stop being read on 2026-12-01. The constitution's "mirroring `trading-bot`'s `railway.json` / `railway.web.json` split" can no longer be followed literally. Infrastructure as code describes every service in one file, holds variable names with `preserve()` (values stay on Railway, never in the file), and is applied only by an explicit owner command. That command is also the switch-on mechanism (R8). Keeping `package.json` under `.railway/` stops Railpack from detecting the repository as a Node project.

**Alternatives**: `railway.json` per service: deprecated, refused for new services. `.railway/railway.py`: same graph and stays in Python, but beta, and "helper names may change". Dashboard-only configuration: no reviewable record of what is deployed.

## R2. Build: Railpack, Python 3.12, editable install

**Decision**: Pin Python with a `.python-version` file (`3.12`). Install with a `requirements.txt` holding the single line `-e .`, so the package is installed editable from the checked-out source.

**Rationale**:
- Railpack defaults to Python 3.13.2 unless a version file pins it. CI and local tests run 3.12.
- Every config loader finds `config/*.yaml` at `Path(__file__).parents[3]`, the repository root. A normal `pip install .` copies the package into site-packages, where that path points nowhere.
- The orchestrator starts each agent with `sys.executable -m <module>` and an environment holding only `PATH` and the agent's own variables (launcher.py). So `PYTHONPATH=src` cannot be relied on, and the package must import with no extra variable. An editable install does both.

**Alternatives**: `PYTHONPATH` in each service: dropped by the launcher for agents. A custom build command per service: three copies of the same thing. Changing the config loaders to use package data: a behaviour change to four components for a deployment concern.

## R3. One service per process

**Decision**: Three services, `orchestrator`, `risk-gate` and `reference-data`, each from the same repository, each with its own start command and only its own variables. Plus the managed `postgres`. All at one replica.

**Rationale**: Clarify Q1. On Railway a service's variables are visible to every process in it. Each process already holds a single-instance lock, so a second replica would refuse to start rather than double-run, but one replica avoids the noise.

## R4. Start commands and restarts

**Decision**: The start commands are the existing module entry points:
- `python -m trading_agent.orchestrator`
- `python -m trading_agent.risk`
- `python -m trading_agent.reference`

Restarts use Railway's default on-failure policy.

**Rationale**: Each already exits 2 when it refuses to start (missing or bad variable, named in the log, value never printed) and 3 on a lost database, so that the platform restarts it (ADR 0013). FR-006 and FR-007 need no new code. A refusal restarts until Railway's retry limit is reached, which is visible in the deploy logs.

## R5. Database setup from the owner's machine

**Decision**: The owner runs the existing `python -m trading_agent.storage.migrate` with `ADMIN_DATABASE_URL` set to Railway Postgres's public (proxied) address, exported in their shell only. No service has a pre-deploy step.

**Rationale**: Clarify Q2. The runner is already idempotent, and its integration suite proves it. Railway's newest Postgres image may be newer than CI's Postgres 16, so the quickstart applies the migrations to a local container of the same major version before the first real run.

**Alternatives**: a pre-deploy command (Railway supports it): would put the admin credential in a service.

## R6. Login command

**Decision**: A new storage-layer command, `python -m trading_agent.storage.logins`, run by the owner with `ADMIN_DATABASE_URL`. It creates one login role per component group role, with a 32-byte random password (`secrets.token_urlsafe`), and prints each connection string once. Login names follow `ta_<component>_login`, the existing quickstart convention.
- Components: orchestrator, research, portfolio manager, risk gate, reference data, execution.
- Owner logins: `ta_owner_read_login` in `ta_dashboard` (Clarify Q4) and `ta_owner_control_login` in `ta_dashboard_control` (R9).
- An existing login is reported "exists, unchanged" with no string. A password changes only with `--reset <name>`.
- The printed connection strings use a host the owner gives (`--service-host`), the database's private network address. Services connect privately, while the owner reaches the database through its public proxy.

**Rationale**: Clarify Q3. It belongs in storage, beside `migrate`, which also holds the admin credential and has no dependency upward. The two owner logins reuse existing roles, so no grant changes and no ADR are needed for them.

**Alternatives**: hand-written SQL: typo risk on role names. Storing the strings: a secret at rest in the repository's tooling.

## R7. Approvals made while observing never become orders

**Decision**: Turn trading on only while the market is closed, after the close and before the next open, and never mid-session. The runbook states this, and the quickstart verifies it.

**Rationale**: FR-011 told the plan to verify the assumption rather than trust it. Execution's `submittable` accepts an approval only on its own trading day while the market is open, and `lapse_today` expires today's approvals once the close has passed. An approval from an earlier day is refused as `approval_expired`, so after-hours switch-on covers every observation-period approval. A mid-session switch-on would act on approvals the gate made earlier that same day, because they are still "today's". No code change is needed. Making Execution refuse approvals older than its own start would change order logic, which needs a flag and an ADR, so it is not done here.

## R8. Switch-on and switch-off

**Decision**:
- **Switch on**: a reviewed change adds an `execution` service to `.railway/railway.ts` with `preserve()` variable names. It reaches `release/prod`. After the close, the owner sets its four variables in Railway (`ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `EXECUTION_DATABASE_URL`, and optionally `ALPACA_BASE_URL`) and runs `railway config apply`. Execution's existing startup refuses anything other than the paper account (Constitution VI).
- **Switch off with no open positions**: remove the service from the file and apply, which deletes the service and its broker keys.
- **Switch off with open positions**: set `trading_paused` with the owner control login, leaving Execution running. Execution then refuses buys while still placing sells and stop-loss exits (spec 003 FR-018).

**Rationale**: Removing Execution while holding positions would also stop their stop-loss exits, breaking FR-013 and Constitution IV. The pause flag stops the PM, which is right once trading has been live. The pause was wrong for observation only because it hid the decisions we wanted to see.

## R9. Owner control login now

**Decision**: Create `ta_owner_control_login` (in `ta_dashboard_control`, which can update only `trading_paused`) during setup, unused until it is needed.

**Rationale**: The documented switch-off must work on the day it is needed, without a new provisioning step under pressure. The dashboard that will own the toggle isn't built, and the constitution allows a plain manual toggle outside any agent's control.

## R10. Deploying from `release/prod`

**Decision**: Each service's source is `github("itsbryanlai/trading-agent", { branch: "release/prod" })`. A deploy is a merge of `main` into `release/prod` (Clarify Q5). Railway doesn't read `.railway/` during deploys, so a release that changes `.railway/` also needs `railway config apply`. A release that adds a migration needs `migrate` first. The runbook's release checklist puts migrate, then merge, then apply-if-needed, in that order.

**Rationale**: It keeps "merged" and "live" as separate decisions. `main` CI already runs on pull requests, so a pull request from `main` to `release/prod` gets the full check suite before the merge.

## R11. A guard test for the observe-only shape

**Decision**: An offline test reads `.railway/railway.ts` as text and fails if:
- it mentions an `execution` service, any `ALPACA_` or `EXECUTION_` name, or `ADMIN_DATABASE_URL`;
- it references the database's own connection variables (`.env.DATABASE_URL` and similar), which would hand a service the admin login;
- a service declares a variable outside its own allowed set.

Switch-on edits this test in the same reviewed change.

**Rationale**: It makes "no broker key, no admin credential" a mechanical check rather than a convention (SC-003). The file is TypeScript, so the test is textual. It is narrow on purpose and checks names, not semantics.
