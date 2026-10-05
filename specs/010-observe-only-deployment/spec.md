# Feature Specification: Observe-Only Deployment

**Feature Branch**: `010-observe-only-deployment`

**Created**: 2026-10-05

**Status**: Draft

**Input**: User description: "Observe-only deployment of the trading-agent system to Railway (feature 010). The owner wants the whole system running so they can watch real PM decisions and Risk Gate verdicts without any order reaching the broker: the service layout, migrations and per-component database roles on a fresh database, per-service variables (named only), how each service starts, how trading is kept off, and how the owner later switches it on."

## Clarifications

### Session 2026-10-05

- Q: Should the first deployment leave Execution undeployed, or deploy it with trading paused? → A: [NEEDS CLARIFICATION: see FR-008. The pause flag stops the orchestrator from starting the Portfolio Manager (spec 005), so a paused deployment would produce no decisions to observe. The default below is to leave Execution undeployed.]

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The system runs unattended and no order can reach the broker (Priority: P1)

The owner deploys the system for the first time, to a hosting project that holds nothing yet. When the work is done, Research runs on its schedule, the Portfolio Manager runs when reports arrive, the Risk Gate turns each decision into a verdict, and the reference data is kept fresh. Nothing in the deployment holds broker credentials or can place an order.

**Why this priority**: This is the point of the feature. Everything else supports it, and the "no order can be placed" property is a safety property (Constitution I, III, VI), so it comes first.

**Independent Test**: Deploy to an empty project, wait through one trading day, then read the stored reports, decisions and verdicts. Confirm that no order or broker refusal exists and that no deployed service has broker credentials in its environment.

**Acceptance Scenarios**:

1. **Given** an empty hosting project, **When** the owner follows the deployment steps, **Then** every in-scope component is running, each connected to the shared database as its own restricted role.
2. **Given** the deployment is running on a trading day, **When** Research and the Portfolio Manager run on schedule, **Then** reports and decisions appear in the shared database, and each decision is followed by a risk verdict.
3. **Given** the deployment is running, **When** the owner inspects every deployed service's environment, **Then** no broker key, and no database login for the order-placing component, is present anywhere.
4. **Given** the deployment is running, **When** the owner reads the orders table, **Then** it is empty.

---

### User Story 2 - A fresh database is built correctly and safely (Priority: P1)

The owner starts from an empty shared database. One deliberate setup step creates the schema and the per-component roles; each component then gets a login in its own role and nothing more.

**Why this priority**: The database grants are the real permission boundary (Constitution III). A deployment with a wrong grant is a worse outcome than no deployment, and nothing can start before this step is done.

**Independent Test**: Run the setup step against an empty database, run it again, then connect as each component's login and confirm it can read and write exactly what its spec allows.

**Acceptance Scenarios**:

1. **Given** an empty database, **When** the owner runs the setup step with the admin credential, **Then** every migration is applied once and the group roles exist.
2. **Given** the setup has run, **When** it runs again, **Then** nothing changes and it succeeds.
3. **Given** a component's login was created, **When** the component connects, **Then** it holds only that component's permissions, and a component without a login cannot connect.
4. **Given** the admin credential, **When** any service starts, **Then** that service is never handed the admin credential.

---

### User Story 3 - The owner can tell the system is healthy (Priority: P2)

Once deployed, the owner can find out that each service is up, that the scheduled agents are running, and why a run did not start, without reading source code or connecting to the database by hand more than they must.

**Why this priority**: Observation is the purpose of the deployment. A deployment that silently does nothing looks the same as one that is working.

**Independent Test**: Break one service on purpose (a missing variable, a failing agent). Confirm the owner learns of it from the service's logs and the stored run records within one scheduling interval.

**Acceptance Scenarios**:

1. **Given** a service has a missing or malformed variable, **When** it starts, **Then** it exits with a message naming the missing variable (never its value), and does not run in a degraded mode.
2. **Given** the services are running, **When** the owner reads the logs and the orchestrator's run records, **Then** each agent run, skip and failure shows with its reason.
3. **Given** a service crashes, **When** the hosting platform restarts it, **Then** it resumes without double-running a slot that already ran (existing restart behaviour of the components).

---

### User Story 4 - Trading is switched on later, as one deliberate step (Priority: P2)

After watching decisions and verdicts for as long as they like, the owner turns trading on by explicitly adding the order-placing component to the deployment. Nothing about the observation period can trigger orders by itself, and nothing the owner did earlier has to be undone first.

**Why this priority**: The switch-on is where real consequences begin, so the procedure must be written down and unambiguous, but it comes after the observe-only deployment itself works.

**Independent Test**: Follow the switch-on steps against a test project with a stand-in for the broker. Confirm that only verdicts approved after switch-on are acted on, and that the order-placing component refuses a non-paper endpoint.

**Acceptance Scenarios**:

1. **Given** approved verdicts accumulated during observation, **When** the owner turns trading on, **Then** none of those old approvals is turned into an order (each has expired, and the order-placing component refuses it as expired).
2. **Given** the owner turns trading on, **When** the order-placing component starts with a non-paper broker address, **Then** it refuses to start.
3. **Given** the owner wants to stop again, **When** they follow the documented switch-off step, **Then** no new order is placed, and open positions keep their own stop-losses.

---

### Edge Cases

- A component's variable is missing or the database login is wrong: the component refuses to start with a clear message and no partial work.
- The setup step runs against a database that already has some migrations applied: only the missing ones are applied.
- A restart happens during a scheduled run: the orchestrator marks the run interrupted and never starts a slot twice.
- The reference-data job has not yet run today when the first decision arrives: the gate rejects the buy for lack of reference data, which is normal and must read clearly in the verdict.
- The first deployment happens outside market hours or on a weekend: services start and wait, without errors.
- Two model or data providers share one account's rate limit: the documented variables make the sharing visible.
- The owner redeploys a service with a changed schedule or risk setting: the change took a code review first (existing rule), and nothing in the deployment bypasses it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The deployment MUST consist of a managed shared database, one service running the orchestrator together with the agents it starts (Research and the Portfolio Manager), one service running the Risk Gate's own loop, and one service running the reference-data job. The Opportunistic Identifier, journal writer, Assistant and dashboard are not part of it.
- **FR-002**: Each service MUST run as its own process with its own database login, and the orchestrator MUST start each agent with only that agent's own variables (ADR 0013, ADR 0015).
- **FR-003**: A single, documented setup step, run only with the admin credential and never on component startup, MUST bring an empty database to the current schema, and MUST be safe to run repeatedly.
- **FR-004**: The setup MUST end with every in-scope component able to connect as its own role, each holding exactly the permissions its spec grants, and no component holding the admin credential or any other component's login.
- **FR-005**: Every service's required variables MUST be listed by name, per service, in the repository's example configuration, with no value committed anywhere in the repository, and the example MUST stay current with the deployment.
- **FR-006**: A service MUST refuse to start, naming the missing or malformed variable and never printing its value, rather than run with reduced behaviour.
- **FR-007**: Each service MUST have a documented, single start command, and the platform MUST restart a crashed service automatically.
- **FR-008**: In the observe-only deployment no component that holds broker credentials, and no component that can submit an order, MUST be deployed, and no broker credential MUST exist in any service's environment. [NEEDS CLARIFICATION: confirm leaving Execution undeployed, rather than deploying it with trading paused. The pause flag stops the Portfolio Manager from running (spec 005), so the paused option yields no decisions to observe.]
- **FR-009**: With trading off, the Portfolio Manager and the Risk Gate MUST still run, so that real decisions and verdicts accumulate for the owner to read.
- **FR-010**: Switching trading on MUST be one explicit, documented owner action (adding the order-placing component with its own database login and the paper broker keys), and MUST NOT be reachable from any agent, the Assistant or any natural-language interface (Constitution IV, VII).
- **FR-011**: Approvals produced while trading was off MUST NOT become orders when trading is later switched on. The existing approval expiry covers this, and the plan MUST verify it rather than assume it.
- **FR-012**: The documented switch-on MUST state the check that the broker address is the paper endpoint, and the order-placing component MUST refuse to start otherwise (Constitution VI).
- **FR-013**: A documented switch-off MUST exist that stops new orders without force-liquidating positions, consistent with the existing breaker and stop-loss behaviour.
- **FR-014**: The deployment MUST NOT change any risk limit, position-sizing rule, schedule, or order logic. Any such change is a separate, flagged change.
- **FR-015**: The deployment steps MUST name which actions the owner performs by hand (creating logins, entering secrets) and which the repository supplies, so no secret passes through the repository, a commit, or chat.

### Key Entities

- **Service**: one deployed process, with its own start command, variables and database login.
- **Component login**: a database login in exactly one component's role, created once by the owner.
- **Admin credential**: held by the owner and used only by the setup step.
- **Trading switch**: the owner's action of adding the order-placing component, and its reverse.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From an empty hosting project and a documented list of secrets, the owner reaches a running observe-only deployment in one sitting, without editing source code.
- **SC-002**: After a full trading day, 100% of Portfolio Manager decisions have a risk verdict, and zero orders and zero broker calls exist.
- **SC-003**: Zero deployed services have a broker credential, the admin credential, or another component's login in their environment.
- **SC-004**: Running the setup step twice on the same database applies each migration exactly once.
- **SC-005**: A missing variable is reported by name within one start attempt, for every service.
- **SC-006**: Following the switch-on steps, the first order placed comes only from a verdict approved after switch-on.

## Assumptions

- The owner creates the hosting project, the database service, the component logins and every secret by hand. This feature does not create accounts or enter secrets.
- The Risk Gate's loop and Execution already exist as separate runnable processes (ADR 0013, features 002, 003 and 009), and reference data as a runnable job (feature 004). This feature adds how they are deployed, not new behaviour.
- Research and the Portfolio Manager are already enabled in the orchestrator's schedule and use the Qwen provider by default, with the owner's accepted exposure of portfolio state to that provider.
- Execution's absence means approved verdicts are never submitted. This is the intended observe-only effect.
- The pause flag remains available for use after switch-on. Its dashboard is not built, so until then the owner changes it by hand.
- Out of scope: the Opportunistic Identifier, the journal writer, the Assistant, the dashboard, branch protection, and any change to risk limits or schedules.
