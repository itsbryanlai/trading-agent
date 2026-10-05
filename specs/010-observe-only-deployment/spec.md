# Feature Specification: Observe-Only Deployment

**Feature Branch**: `010-observe-only-deployment`

**Created**: 2026-10-05

**Status**: Draft

**Input**: User description: "Observe-only deployment of the trading-agent system to Railway (feature 010). The owner wants the whole system running so they can watch real PM decisions and Risk Gate verdicts without any order reaching the broker: the service layout, migrations and per-component database roles on a fresh database, per-service variables (named only), how each service starts, how trading is kept off, and how the owner later switches it on."

## Clarifications

### Session 2026-10-05

- Q: Should the first deployment leave Execution undeployed, or deploy it with trading paused? → A (revised after analysis): Deploy Execution with trading paused, and let the Portfolio Manager run while paused through a reviewed orchestrator setting. Execution is the only writer of account snapshots, and without one the Portfolio Manager and the Risk Gate refuse to work. Leaving Execution undeployed (the first answer) would have produced no decisions and no verdicts. While paused, Execution keeps recording account snapshots and refuses every approved buy as "trading paused".
- Q: The paper account currently holds positions. What happens to them? → A: The owner closes every position and cancels every open order in the broker's own interface before Execution first starts. A flat account is a precondition of the deployment. Execution would otherwise place stop-loss sells for positions it finds, even while paused.
- Q: Should the orchestrator, the Risk Gate's loop and the reference-data job each be their own service, or share one? → A: One service per process, each holding only its own variables, because a service's variables are visible to every process in it. Execution becomes a fourth service.
- Q: Where should the database setup step run? → A: From the owner's own machine against the database's public address, with the admin credential only in the owner's shell. It never enters any hosted service.
- Q: How should each component's database login be created? → A: A repo command the owner runs locally creates every component's login with generated passwords, and prints each connection string once.
- Q: Until the dashboard exists, how does the owner read decisions and verdicts? → A: Through a personal login in the existing read-only dashboard role, plus a documented set of read queries. No new role.
- Q: Should merges redeploy automatically, or should each deploy be manual? → A: Services auto-deploy only from a dedicated `release/prod` branch. Merging `main` into `release/prod` is the deploy action, and merging to `main` alone deploys nothing.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The system runs unattended and no order can reach the broker (Priority: P1)

The owner deploys the system for the first time, to a hosting project that holds nothing yet, with trading paused. Research runs on its schedule. The Portfolio Manager runs when reports arrive. The Risk Gate turns each decision into a verdict. The reference data is kept fresh. Execution records the account's state but refuses every approved buy, and the account holds nothing it could sell.

**Why this priority**: This is the point of the feature. Everything else supports it, and the "no order can be placed" property is a safety property (Constitution I, III, VI), so it comes first.

**Independent Test**: Deploy to an empty project with the paper account flat and trading paused. Wait through one trading day, then read the stored reports, decisions, verdicts and Execution's refusals. Confirm that no order exists, every approved buy was refused as "trading paused", and broker credentials exist only on Execution's service.

**Acceptance Scenarios**:

1. **Given** an empty hosting project, a flat paper account and trading paused, **When** the owner follows the deployment steps, **Then** every in-scope component is running, each connected to the shared database as its own restricted role.
2. **Given** the deployment is running on a trading day, **When** Research and the Portfolio Manager run on schedule, **Then** reports, decisions and account snapshots appear in the shared database, and each decision is followed by a risk verdict.
3. **Given** the Risk Gate approved a buy while paused, **When** Execution processes it, **Then** no order is placed and the approval is refused as "trading paused".
4. **Given** the deployment is running, **When** the owner inspects every deployed service's environment, **Then** broker keys and Execution's database login appear only on Execution's service, and the admin credential appears nowhere.
5. **Given** the deployment is running, **When** the owner reads the orders table, **Then** it is empty.

---

### User Story 2 - A fresh database is built correctly and safely (Priority: P1)

The owner starts from an empty shared database. One deliberate setup step creates the schema and the per-component roles. Each component then gets a login in its own role and nothing more, and trading is paused before any service starts.

**Why this priority**: The database grants are the real permission boundary (Constitution III). A deployment with a wrong grant is a worse outcome than no deployment, and nothing can start before this step is done.

**Independent Test**: Run the setup step against an empty database, run it again, then connect as each component's login and confirm it can read and write exactly what its spec allows.

**Acceptance Scenarios**:

1. **Given** an empty database, **When** the owner runs the setup step with the admin credential, **Then** every migration is applied once and the group roles exist.
2. **Given** the setup has run, **When** it runs again, **Then** nothing changes and it succeeds.
3. **Given** a component's login was created, **When** the component connects, **Then** it holds only that component's permissions, and a component without a login cannot connect.
4. **Given** the setup has run, **When** the owner sets trading paused with their control login, **Then** the flag reads paused before any service starts.
5. **Given** the admin credential, **When** any service starts, **Then** that service is never handed the admin credential.

---

### User Story 3 - The owner can tell the system is healthy (Priority: P2)

Once deployed, the owner can find out that each service is up, that the scheduled agents are running, and why a run did not start. This shouldn't require reading source code, or connecting to the database by hand more than necessary.

**Why this priority**: Observation is the purpose of the deployment. A deployment that silently does nothing looks the same as one that is working. The analysis that found the missing account snapshot shows how easily that happens.

**Independent Test**: Break one service on purpose (a missing variable, a failing agent). Confirm the owner learns of it from the service's logs and the stored run records within one scheduling interval.

**Acceptance Scenarios**:

1. **Given** a service has a missing or malformed variable, **When** it starts, **Then** it exits with a message naming the missing variable (never its value), and does not run in a degraded mode.
2. **Given** the services are running, **When** the owner reads the logs and the orchestrator's run records, **Then** each agent run, skip and failure shows with its reason.
3. **Given** a service crashes, **When** the hosting platform restarts it, **Then** it resumes without double-running a slot that already ran (existing restart behaviour of the components).
4. **Given** the first trading day after deployment, **When** the owner runs the documented post-deploy check, **Then** it confirms account snapshots, decisions, verdicts and "trading paused" refusals exist, and that orders are empty.

---

### User Story 4 - Trading is switched on later, as one deliberate step (Priority: P2)

After watching decisions and verdicts for as long as they like, the owner turns trading on by clearing the pause, after the close. Nothing about the observation period can trigger orders by itself, and nothing done earlier has to be undone first.

**Why this priority**: The switch-on is where real consequences begin, so the procedure must be written down and unambiguous. It comes after the observe-only deployment itself works.

**Independent Test**: In a rehearsal against a test database and a stand-in broker, confirm that approvals made while paused never become orders after the pause is cleared, and that Execution refuses a non-paper endpoint.

**Acceptance Scenarios**:

1. **Given** approvals made while paused, **When** the owner clears the pause, **Then** none of them becomes an order: each was already refused as "trading paused", or lapses at the close.
2. **Given** Execution starts with a non-paper broker address, **When** it starts, **Then** it refuses to start.
3. **Given** trading is on and the owner wants to stop new buying, **When** they set the pause, **Then** no new buy is placed, and existing positions keep their stop-loss exits and approved sells.
4. **Given** trading is on and the owner wants to remove Execution entirely, **When** the documented preconditions hold (after the close, no positions, no open orders, nothing in flight), **Then** removing the service places nothing and leaves nothing unprotected.

---

### Edge Cases

- A component's variable is missing or the database login is wrong: the component refuses to start with a clear message and no partial work.
- The setup step runs against a database that already has some migrations applied: only the missing ones are applied.
- A restart happens during a scheduled run: the orchestrator marks the run interrupted and never starts a slot twice.
- The reference-data job has not yet run today when the first decision arrives: the gate rejects the buy for lack of reference data, which is normal and must read clearly in the verdict.
- The first deployment happens outside market hours or on a weekend: services start and wait, without errors. The Portfolio Manager has no snapshot until Execution's first pre-open snapshot, so it records `no_account_snapshot` failures until then.
- The paper account isn't flat when Execution first starts: Execution could place stop-loss sells for those positions. The runbook puts "flatten the account" before Execution's first start, and the post-deploy check confirms zero positions.
- Trading is not yet paused when Execution first starts: Execution could place approved buys. The setup sets the pause before any service exists, and the post-deploy check confirms it.
- The pause flag can't be read: the orchestrator treats it as paused (existing fail-closed behaviour), and Execution's own read governs buys.
- Two model or data providers share one account's rate limit: the documented variables make the sharing visible.
- A release includes a new migration but the owner merges to `release/prod` before applying it: the affected service's start or first query fails loudly, and no data is written against the wrong schema. The release steps put the setup first to prevent this.
- A change merged to `main` is not live until it reaches `release/prod`, so `main` can run ahead of production.
- The owner redeploys a service with a changed schedule or risk setting: the change took a code review first (existing rule), and nothing in the deployment bypasses it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The deployment MUST consist of a managed shared database and four services:
  - the orchestrator, together with the agents it starts (Research and the Portfolio Manager);
  - the Risk Gate's loop;
  - the reference-data job;
  - Execution.

  The Opportunistic Identifier, journal writer, Assistant and dashboard are not part of it.
- **FR-002**: Each component MUST be its own separately deployed service, holding only its own variables. The one exception is the orchestrator's service, which also carries the variables of the agents it starts (ADR 0015) and nothing else, because a service's variables are visible to every process in it. Each service MUST run its own process with its own database login, and the orchestrator MUST start each agent with only that agent's own variables (ADR 0013, ADR 0015).
- **FR-003**: A single, documented setup step MUST bring an empty database to the current schema, and MUST be safe to run repeatedly. The owner runs it from their own machine, with the admin credential held only in their shell. It is never stored in any hosted service and never run on component startup.
- **FR-004a**: A command the owner runs locally, with the admin credential in their shell, MUST create one login per component role (orchestrator, Research, Portfolio Manager, Risk Gate, reference data and Execution), each with a generated strong password.
  - It MUST print each connection string once, store none of them, and never print the admin credential.
  - Re-running it MUST NOT silently change an existing login's password.
- **FR-004b**: The same command MUST also create two personal owner logins in existing roles:
  - a read login in the read-only dashboard role, which can read every table and write nothing;
  - a control login in the dashboard's pause-toggle role, which can change only the pause flag.

  The deployment documentation MUST include read queries for the day's reports, decisions, verdicts with their reasons, account snapshots, Execution's refusals and orchestrator run records. No new database role is introduced.
- **FR-004**: The setup MUST end with every in-scope component able to connect as its own role, each holding exactly the permissions its spec grants, and no component holding the admin credential or any other component's login (except as FR-002 allows for the orchestrator's agents).
- **FR-005**: Every service's required variables MUST be listed by name, per service, in the repository's example configuration, with no value committed anywhere in the repository, and the example MUST stay current with the deployment.
- **FR-006**: A service MUST refuse to start, naming the missing or malformed variable and never printing its value, rather than run with reduced behaviour.
- **FR-007**: Each service MUST have a documented, single start command, and the platform MUST restart a crashed service automatically.
- **FR-007a**: Every service MUST deploy automatically from the `release/prod` branch and from no other branch. Merging `main` into `release/prod` is the owner's deploy action. The documented release steps MUST have the owner apply any pending database setup before that merge, so code never runs against a schema it doesn't match.
- **FR-008**: Trading MUST be kept off by the pause flag, set to paused during setup before any service starts, and by a flat paper account (no positions, no open orders) before Execution first starts.
  - The owner flattens the account in the broker's own interface. No component does it.
  - Broker credentials MUST exist only on Execution's service.
- **FR-009**: While paused, the Portfolio Manager MUST still be started on its normal schedule, so that real decisions and verdicts accumulate for the owner to read. This MUST be a reviewed orchestrator setting, off by default.
  - When the setting is off, the existing behaviour (no Portfolio Manager runs while paused) is unchanged.
  - When the pause flag can't be read, the orchestrator MUST still treat it as paused and start no Portfolio Manager run.
- **FR-010**: Switching trading on MUST be one explicit, documented owner action: clearing the pause with the owner's control login. It MUST NOT be reachable from any agent, the Assistant or any natural-language interface (Constitution IV, VII). A follow-up reviewed change turns the FR-009 setting off, so that a later pause stops the Portfolio Manager again.
- **FR-011**: Approvals produced while paused MUST NOT become orders after the pause is cleared. Execution refuses each paused buy as final. The switch-on happens only after the close, so that nothing approved earlier that day is still pending.
- **FR-012**: The documented switch-on MUST state the check that the broker address is the paper endpoint, and Execution MUST refuse to start otherwise (Constitution VI).
- **FR-013**: A documented switch-off MUST exist in two forms.
  - Pausing stops new buys, while approved sells and stop-loss exits continue, so open positions stay protected.
  - Removing Execution entirely is allowed only after the close, with no positions, no open orders and nothing in flight.
- **FR-014**: The deployment MUST NOT change any risk limit, position-sizing rule or order logic. The only schedule-related change is the FR-009 setting. Any other such change is a separate, flagged change.
- **FR-015**: The deployment steps MUST name which actions the owner performs and which the repository supplies, so no secret passes through the repository, a commit, or chat. Owner actions are:
  - running the setup and login commands;
  - setting the pause;
  - flattening the paper account;
  - pasting each connection string and secret into its service;
  - merging to `release/prod`.
- **FR-016**: The database's public address MUST be reachable only while the owner needs it (setup and releases with migrations). The documented steps MUST close it afterwards.

### Key Entities

- **Service**: one deployed process, with its own start command, variables and database login.
- **Component login**: a database login in exactly one component's role, created once by the owner.
- **Admin credential**: held by the owner and used only by the setup and login commands.
- **Trading switch**: the pause flag, changed only by the owner's control login.
- **Observe setting**: the reviewed orchestrator setting that lets the Portfolio Manager run while paused.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From an empty hosting project and a documented list of secrets, the owner reaches a running observe-only deployment in one sitting, without editing source code.
- **SC-002**: After a full trading day: at least one account snapshot exists, 100% of Portfolio Manager decisions have a risk verdict, every approved buy has a "trading paused" refusal, and zero orders exist.
- **SC-003**: Broker credentials and Execution's login are on exactly one service (Execution). No service holds the admin credential. No service holds another component's login, except the orchestrator's agents' logins on the orchestrator's service.
- **SC-004**: Running the setup step twice on the same database applies each migration exactly once.
- **SC-005**: A missing variable is reported by name within one start attempt, for every service.
- **SC-006**: After the switch-on, the first order placed comes only from a verdict approved after the pause was cleared.

## Assumptions

- The owner creates the hosting project and the database service, runs the setup and login commands, sets the pause, flattens the paper account, and enters every secret into its service. This feature supplies the commands and steps. It does not create accounts, enter secrets or trade.
- The paper account is used by this system only once deployed. No other system trades it.
- The Risk Gate's loop and Execution already exist as separate runnable processes (ADR 0013; features 002, 003 and 009), and reference data as a runnable job (feature 004). This feature adds how they are deployed, and one orchestrator setting.
- Research and the Portfolio Manager are already enabled in the orchestrator's schedule and use the Qwen provider by default, with the owner's accepted exposure of portfolio state to that provider.
- The pause flag's dashboard is not built, so the owner changes it with their control login until then.
- Out of scope: the Opportunistic Identifier, the journal writer, the Assistant, the dashboard, branch protection, and any change to risk limits or order logic.
