# Feature Specification: Orchestrator

**Feature Branch**: `005-orchestrator`

**Created**: 2026-09-30

**Status**: Draft

**Input**: User description: "Feature 005: the orchestrator (docs/specs/orchestrator.md, ADR 0003, ADR 0011, ADR 0013). A scheduler with no decision authority that starts the LLM agents (Research, Opportunistic Identifier, Portfolio Manager) on their cadences; Execution, the gate's trigger runner and the reference-data job run their own loops and are not scheduled by it. Owner decisions: (1) Each agent is started as its own process (python -m trading_agent.<agent>) with only that agent's environment variables and a per-agent timeout; agents not yet built are disabled in config; tests use fake agent commands, never model calls. (2) The orchestrator records its own runs in an orchestrator-owned table (agent, start, end, outcome) so "last PM run" survives restarts, and reads only the latest report's creation time through a single-value view, never report contents; plus trading_paused. New grants for ta_orchestrator in a new migration. (3) Schedule defaults (config values): Research daily pre-market 08:30 ET; PM morning session 10:00 ET; Opportunistic Identifier every 60 minutes, 10:00-15:00 ET; timeouts Research 15 min, OI 10 min, PM 10 min; PM event-driven runs >=30 min apart and none after 15:30 ET (ADR 0011). (4) Research runs daily pre-market only; the config supports an optional intraday interval for Research, off by default; news-triggered Research is decided in the Research feature. (5) Event-driven PM runs start 5 minutes after the newest report (so the reference-data job can record new symbols), respect the 30-minute spacing and 15:30 cutoff, and only begin after the morning session. trading_paused skips PM invocations only. Only XNYS trading days. Owner preferences recorded for later features (not this one): the Opportunistic Identifier uses design A (deterministic code pre-screens the universe, one LLM call reviews a shortlist of ~20 names); model choice open, Haiku 4.5 preferred so far."

## Clarifications

### Session 2026-09-30

- Q: Where do the agents' credentials live, given the orchestrator starts every agent? → A: On the one worker service. The orchestrator passes each agent only the variable names listed for it in configuration, refuses at startup any configuration that would pass a broker credential or another component's database login, and never reads the values itself.
- Q: Do the reports a failed or timed-out PM run was looking at still count as new? → A: Yes. The failed run counts toward the 30-minute spacing, so there's no retry loop, but not toward "reports considered". The next allowed run (at most one every 30 minutes, never after the cutoff) retries them.
- Q: When does the PM stop running on early-close days? → A: 30 minutes before that day's close (15:30 normally, 12:30 on a 13:00 close), keeping ADR 0011's purpose. The Opportunistic Identifier's last hourly slot follows the same rule.
- Q: What does the orchestrator do after downtime that missed the morning's Research run or PM morning session? → A: It runs each once, Research first, if still before the PM cutoff. The morning session starts after Research finishes or times out. The Opportunistic Identifier runs for the current slot only; missed slots are never backfilled.
- Q: If trading is paused when the morning session is due and resumed later that day, how does the PM catch up? → A: The morning session's slot counts as done once it has been run or skipped for the pause. After resuming, the normal event-driven rule sees the unconsidered pre-open reports and starts one PM run (before the cutoff). There is no special resume logic.

### Session 2026-09-30 (after `/speckit-analyze`)

- Q: What happens to running agents when the orchestrator stops, crashes or is redeployed? → A: On a normal stop, including the platform's SIGTERM during a redeploy, it stops every running agent's process group before exiting. Each run records its process-group id. At startup, before marking unfinished runs as interrupted, it stops any recorded group that is still alive and still running that agent. An agent never outlives its timeout unsupervised, and never overlaps a new run of itself.
- Q: Which comes first, recording a run or starting its process? → A: Recording. The run's record claims its slot first, so a restart can never start the same slot again. If the process then fails to start, the record becomes `failed`.
- Q: When is a run a catch-up rather than an on-time run? → A: A daily slot (Research's pre-market run, the morning session) started more than one tick (about 30 seconds) after its time, because the orchestrator wasn't running then, is recorded as a catch-up. Every slot has one key, so it can be claimed once whatever the reason recorded. A morning session held back while Research is still running is still the morning session, not a catch-up.
- Q: Where do the agents' credentials live? (recorded as an ADR) → A: See [ADR 0015](../../docs/adr/0015-orchestrator-starts-agents-with-their-own-credentials.md), which records the first clarification above as a decision about the system's shape.

## User Scenarios & Testing *(mandatory)*

The "users" of the orchestrator are the system's owner, who wants the analysts and the Portfolio Manager to run on a predictable cadence without supervising them, and the three LLM agents it starts: Research, the Opportunistic Identifier and the Portfolio Manager (PM).

The orchestrator is a scheduler and nothing more ([ADR 0003](../../docs/adr/0003-orchestrator-is-a-scheduler-not-an-authority.md)):
- It decides *when* an agent runs, never *what* it concludes.
- It makes no model call and reads no report contents, decisions, verdicts, orders or positions.
- A bug in it can misfire a schedule, but can never place, approve or block a trade. Every order still passes the Risk Gate, and only Execution reaches the broker.

Execution, the Risk Gate's trigger runner and the reference-data job run their own loops ([ADR 0013](../../docs/adr/0013-deterministic-services-run-their-own-loops.md)). The orchestrator does not start or watch them.

None of the three agents exists yet: each is a later feature. This feature ships the scheduler with every agent disabled in configuration. Each agent feature then turns its own entry on. Until then, everything is exercised with stand-in agents that make no model calls.

### User Story 1 - The day's scheduled runs happen on time (Priority: P1)

On each XNYS trading day:
- Research runs once before the open (08:30 ET by default).
- The PM holds its morning session after the open (10:00 ET by default), considering everything open, chiefly Research's pre-open reports.
- The Opportunistic Identifier runs every hour from 10:00 to 15:00 ET by default.

On non-trading days nothing runs.

**Why this priority**: This is the orchestrator's whole job. Without it, no analysis or decision happens without manual intervention.

**Independent Test**: With stand-in agents that record when they were started, drive the orchestrator through one trading day on a test clock. Each enabled agent starts at its scheduled times and at no others. A weekend or holiday starts nothing.

**Acceptance Scenarios**:

1. **Given** a trading day and all three agents enabled, **When** the clock passes 08:30, 10:00 and each hour to 15:00 ET, **Then** Research starts once at 08:30, the PM's morning session starts once at 10:00, and the Opportunistic Identifier starts at 10:00, 11:00, …, 15:00.
2. **Given** a weekend or an exchange holiday, **When** the day passes, **Then** no agent is started.
3. **Given** an agent that is disabled in configuration, **When** its scheduled time passes, **Then** it is not started, and nothing else is affected.

---

### User Story 2 - The PM runs again when there is something new, but not too often (Priority: P1)

After the morning session, when an analyst writes a new report, the PM runs again to consider it along with everything else open ([ADR 0011](../../docs/adr/0011-event-driven-portfolio-manager-runs.md)). These event-driven runs:
- wait 5 minutes after the newest report, so the reference-data job can record data for any newly named symbol;
- are at least 30 minutes apart;
- never start after 15:30 ET.

A burst of reports is handled in one run.

**Why this priority**: Without it, anything the Opportunistic Identifier finds after the morning session expires unused. That was the reason for ADR 0011.

**Independent Test**: On a test clock, after the morning session, record reports at chosen times. PM runs start only when a report is newer than the last PM run. Each run starts no earlier than 5 minutes after the newest report and at least 30 minutes after the previous PM run, and none starts after 15:30 ET.

**Acceptance Scenarios**:

1. **Given** the morning session started at 10:00 and a report written at 11:02, **When** the clock reaches 11:07, **Then** one event-driven PM run starts.
2. **Given** a PM run started at 11:07 and reports written at 11:10, 11:20 and 11:25, **When** the clock reaches 11:37 (30 minutes after the last run, and more than 5 minutes after the newest report), **Then** exactly one PM run starts, covering all three.
3. **Given** a report written at 15:28, **When** the clock passes 15:30, **Then** no PM run starts that day.
4. **Given** a report written at 09:45, before the morning session, **When** the morning session runs at 10:00, **Then** no separate event-driven run is started for that report.
5. **Given** no report newer than the last PM run, **When** time passes, **Then** no event-driven PM run starts.

---

### User Story 3 - A pause stops the PM, not the analysts (Priority: P1)

When the owner pauses trading, the orchestrator skips PM runs for as long as the pause lasts. Research and the Opportunistic Identifier keep running, because they only write reports. When the pause is lifted before 15:30 ET, the PM runs once if there is anything it hasn't considered.

**Why this priority**: The pause is the owner's one manual control (Constitution IV). Starting the PM while paused would produce decisions that can't be traded, and waste model calls.

**Independent Test**: With trading paused on a test clock, the morning session and event-driven PM runs are skipped and logged, while analyst runs continue. After the pause is lifted, one PM run starts if a report is newer than the last PM run.

**Acceptance Scenarios**:

1. **Given** trading is paused at 10:00, **When** the morning session is due, **Then** the PM is not started, the skip is logged, and Research and the Opportunistic Identifier run as scheduled.
2. **Given** trading was paused all morning and is resumed at 13:00, **When** the orchestrator next checks, **Then** one PM run starts, because the pre-open reports have not been considered.
3. **Given** the pause flag can't be read, **When** a PM run is due, **Then** the PM is not started, and the failure is logged. This fails closed.

---

### User Story 4 - Failures and restarts don't break the schedule (Priority: P2)

An agent run that fails, hangs or times out is logged and recorded, and the rest of the schedule carries on. A restart of the orchestrator works out from recorded runs and the calendar what has already happened today. It neither repeats a run nor skips one.

**Why this priority**: The system runs unattended (ADR 0006). One bad agent run, or a redeploy, must not stop the others or cause duplicate model spending.

**Independent Test**: With stand-in agents that fail, hang past their timeout, or succeed, the orchestrator records each outcome, stops a hung run at its timeout, and keeps the other agents on schedule. Restarting the orchestrator mid-day on the same recorded state starts no duplicate runs and catches up anything missed.

**Acceptance Scenarios**:

1. **Given** the Opportunistic Identifier fails at 11:00, **When** 12:00 arrives, **Then** it runs again at 12:00 as scheduled, with no retry loop in between.
2. **Given** a PM run exceeds its timeout, **When** the timeout passes, **Then** the run is stopped, recorded as timed out, and the next PM run obeys the 30-minute spacing measured from that run's start.
3. **Given** the orchestrator restarts at 10:20 after the morning session was recorded at 10:00, **When** it resumes, **Then** it does not start a second morning session.
4. **Given** the orchestrator was down from 08:00 to 11:15, **When** it starts, **Then** it runs Research and the PM's morning session once each (both were missed), and runs the Opportunistic Identifier for the current hour only, without backfilling 10:00.
5. **Given** an agent's previous run is still going when its next slot arrives, **When** the slot passes, **Then** no second copy starts, and the skip is logged.

---

### User Story 5 - The owner can see what ran (Priority: P3)

Every start, finish, skip and failure is recorded with the agent, the reason and the times, so the owner (and later the Assistant and the dashboard) can see what the orchestrator did and why.

**Why this priority**: Operational visibility. It adds nothing to trading behaviour, but it explains a quiet day.

**Independent Test**: After a simulated day with successes, failures, timeouts and skips, the recorded runs and log lines account for every scheduled slot.

**Acceptance Scenarios**:

1. **Given** a day with a failed run and a paused morning session, **When** the owner reads the run records, **Then** each shows the agent, why it was due, when it started and ended, and its outcome, and the skipped morning session shows why.

---

### Edge Cases

- **Early-close days**: the PM cutoff becomes 30 minutes before that day's close (12:30 ET on a 13:00 close), and the Opportunistic Identifier's last hourly slot is the last one at least 30 minutes before the close. The morning session is unchanged. (Clarifications.) Because the cutoff is always at least 30 minutes before the close, the 15:30 ceiling in FR-015 still holds on every day.
- **A report written exactly at the cutoff or in the 5-minute wait window before it**: no run starts after the cutoff, so it waits until the next trading day's morning session.
- **A failed or timed-out PM run**: it counts toward the 30-minute spacing, but the reports it saw count as not yet considered, so the next allowed run picks them up. This avoids both a retry loop and silently dropping reports. (Clarifications.) An interrupted run (the orchestrator stopped mid-run) is treated the same way.
- **A report written while a PM run is in progress**: it counts as new and triggers the next run under the normal rules.
- **The report-time view can't be read**: no event-driven PM run starts until it can. Scheduled runs still happen. The failure is logged.
- **Clock and time zones**: every time judgement (trading day, open, close, early close) comes from the shared exchange calendar in ET, never the host's local time.
- **Daylight-saving changes**: the schedule stays in ET wall-clock time (08:30 ET is 08:30 ET in both summer and winter).
- **Two orchestrator processes at once** (redeploy overlap): only one runs. The second waits, then refuses to start.
- **Lost database connection**: the orchestrator exits so the platform restarts it (ADR 0013 §5). On restart it works out the day's state again.
- **An agent command that doesn't exist or can't start**: recorded as a failed run, then the schedule continues.
- **Agent output**: an agent's own logs go to the platform's logs as they are. The orchestrator records exit status and times, not what the agent concluded.
- **Credentials in agent configuration**: an agent may be given only its own named variables. Configuration that would pass a broker credential or another component's database login to an agent is rejected at startup.

## Requirements *(mandatory)*

### Functional Requirements

**Scope and authority**

- **FR-001**: The orchestrator MUST schedule only Research, the Opportunistic Identifier and the Portfolio Manager. It MUST NOT start, stop or watch Execution, the Risk Gate's trigger runner or the reference-data job.
- **FR-002**: The orchestrator MUST NOT make a model call, read report contents, or read decisions, verdicts, orders, positions or account data. It MUST NOT write to any table except its own run records.
- **FR-003**: The orchestrator MUST NOT alter, approve, block or retry an agent's output. Its only actions are starting an agent, stopping one that exceeds its timeout, and recording what happened.

**Starting agents**

- **FR-004**: Each agent MUST be started as its own separate process with a configured command, given only the environment variables configured for that agent. It MUST NOT receive the orchestrator's own database login.
- **FR-005**: Each agent MUST have a configured timeout. A run still going at its timeout MUST be stopped (its whole process group) and recorded as timed out.
- **FR-005a**: When the orchestrator stops normally, including on the platform's SIGTERM, it MUST stop every running agent's process group and record those runs as interrupted before exiting. Each run MUST record its process-group id, so that a later startup can stop an agent left running by a crash (FR-023).
- **FR-006**: At most one run of the same agent MUST be in progress at a time. A slot that arrives while the previous run is still going MUST be skipped and logged. Different agents MAY run at the same time.
- **FR-007**: An agent can be enabled or disabled in configuration. A disabled agent MUST never be started. This feature ships with all three disabled, because none exists yet.
- **FR-008**: The orchestrator MUST refuse to start if any agent's configuration would pass it a broker credential or another component's database login.
- **FR-008a**: The orchestrator MUST NOT read, log or record the value of any variable it passes to an agent. It only copies the listed names from its own environment into that agent's. A listed variable that is missing is passed as absent, and the agent reports its own error.

**Schedule**

- **FR-009**: Runs MUST happen only on XNYS trading days, with every time judgement taken from the shared exchange calendar in ET.
- **FR-010**: Research MUST run once a day at its configured pre-market time (default 08:30 ET). The configuration MUST support an optional intraday interval for Research, off by default.
- **FR-011**: The Opportunistic Identifier MUST run at its configured interval within its configured window (default every 60 minutes, 10:00–15:00 ET). The last slot MUST be at least 30 minutes before the close on early-close days.
- **FR-012**: The PM's morning session MUST run once a day at its configured time after the open (default 10:00 ET).
- **FR-013**: Once the morning session's slot is done that day (run, or skipped for the pause; Clarifications), an event-driven PM run MUST start when all of the following hold:
  - (a) a report exists that is newer than the start of the last *successful* PM run;
  - (b) at least 5 minutes have passed since the newest report;
  - (c) at least 30 minutes have passed since the start of the last PM run of any outcome;
  - (d) the time is before the PM cutoff: 15:30 ET, or 30 minutes before the close on early-close days;
  - (e) trading is not paused.
- **FR-014**: A report written before that day's morning session MUST NOT trigger a separate event-driven run; the morning session covers it.
- **FR-015**: The schedule values (times, intervals, window, timeouts, the 5-minute wait, the 30-minute spacing, the cutoff) MUST come from version-controlled configuration, changed only through code review. They MUST NOT be configurable to values looser than [ADR 0011](../../docs/adr/0011-event-driven-portfolio-manager-runs.md) or this spec: spacing at least 30 minutes, cutoff no later than 15:30 ET and at least 30 minutes before the close, and the report wait at least 5 minutes. The morning session must be at or after the open and before the cutoff, and Research's daily time before the open.

**Pause**

- **FR-016**: Before starting any PM run, the orchestrator MUST read the pause flag. If it is set, or can't be read, the PM MUST NOT be started, and the skip MUST be recorded with its reason.
- **FR-017**: The pause MUST NOT affect Research or the Opportunistic Identifier.
- **FR-018**: A morning session skipped because of the pause MUST be recorded as skipped, and MUST NOT be run later as a "morning session". Its slot counts as done, so the event-driven rule (FR-013) covers the reports it would have considered once the pause is lifted.

**Failures and restarts**

- **FR-019**: An agent run that exits unsuccessfully, can't be started, or times out MUST be recorded with its outcome, and MUST NOT be retried before its next scheduled slot (or, for the PM, before the event-driven rule allows).
- **FR-020**: On startup, the orchestrator MUST work out the day's state from its recorded runs, the latest report time and the calendar:
  - It MUST NOT repeat a run already recorded for a slot.
  - It MUST run Research and the PM's morning session once each if they were missed and it is still before the PM cutoff (Clarifications). A slot counts as missed if no run or skip is recorded for it today.
  - It MUST run the Opportunistic Identifier, and any optional intraday Research slot, for the current slot only, never backfilling earlier ones.
  - A caught-up morning session MUST start only after a caught-up Research run has finished (or timed out), so the PM sees Research's report.
- **FR-021**: Only one orchestrator instance MUST run at a time.
- **FR-022**: On a lost database connection the orchestrator MUST exit so the platform restarts it. A failing agent MUST NOT stop the orchestrator.
- **FR-023**: If the orchestrator stops while an agent run is in progress, the next startup MUST first stop that run's process group if it is still alive and still running that agent, then record the run as interrupted. It MUST NOT be left looking as if it is still running.
- **FR-023a**: The orchestrator MUST record a run before starting its process, so the record claims the slot. A process that then fails to start MUST be recorded as failed. Every slot MUST be claimable only once per day, whatever reason its record gives.

**Records and access**

- **FR-024**: The orchestrator MUST record every run and every skipped slot: the agent, the reason it was due (scheduled, morning session, event-driven, catch-up), the start and end times, and the outcome (succeeded, failed, timed out, interrupted, or skipped with a reason). Records are kept, not deleted.
- **FR-025**: The orchestrator's database role MUST be able to read the latest report's creation time through a single-value view that exposes nothing else about reports. It MUST be able to read the pause flag, and to write only its own run records. It MUST have no other access. This amends `specs/001-data-model/contracts/role-grants.md`, and the grants-matrix test MUST match the database both ways.
- **FR-026**: The Assistant and the dashboard MUST be able to read the run records and the report-time view (Constitution VII).
- **FR-027**: The orchestrator MUST log each start, finish, skip and failure. It MUST NOT log any environment variable value.

**Tests**

- **FR-028**: No test may start a real agent or make a model call. Tests use stand-in agent commands and a test clock.

### Key Entities

- **Agent schedule entry**: one per agent. It holds the agent's command, whether it is enabled, the names of the environment variables it may receive, its timeout, and its cadence (daily time, interval and window, or morning session plus event-driven rules). It lives in version-controlled configuration.
- **Orchestrator run record**: one per run or skipped slot. It holds the agent, the reason it was due, the scheduled time, start and end times, and the outcome. Only the orchestrator writes these; the Assistant and the dashboard read them.
- **Latest report time**: a single value, the creation time of the newest report. It is the only thing the orchestrator can learn about reports.
- **Pause flag**: the existing manual `trading_paused` switch, read-only here.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a simulated trading day, every enabled agent starts at each of its scheduled times, within one minute of the scheduled time, and at no other time.
- **SC-002**: On simulated weekends and holidays, zero agents are started.
- **SC-003**: Event-driven PM runs are never less than 30 minutes apart, never start within 5 minutes of the newest report, and never start after the cutoff, across every simulated report pattern tested, including randomised ones.
- **SC-004**: While trading is paused, zero PM runs start, and analyst runs continue unchanged.
- **SC-005**: A hung agent is stopped within one minute of its timeout, and the other agents' scheduled runs in that period all start on time.
- **SC-006**: Restarting the orchestrator at any point in a simulated day produces zero duplicate runs, and misses no morning session or daily Research run that was still due.
- **SC-007**: The orchestrator's database role can do nothing beyond FR-025, verified by the grants-matrix test against the database's own permission records.

## Assumptions

- **Agent entry points**: each agent will be a runnable module, `python -m trading_agent.<agent>`, that exits with status 0 on success. Each agent feature will confirm its command and variable names and enable its entry.
- **Credentials** (Clarifications; [ADR 0015](../../docs/adr/0015-orchestrator-starts-agents-with-their-own-credentials.md)): every agent's variables are set on the one worker service. The orchestrator never reads them (FR-008a). Constitution III's separation is kept by passing each agent only its own variables and refusing broker credentials and other components' logins (FR-008). Separate services per agent, or a secrets store, were considered and not chosen for now.
- **Tick**: the orchestrator checks what is due about once a minute. That is fine for these cadences, which are measured in minutes and hours.
- **Agent timeouts**: default timeouts (Research 15 minutes, Opportunistic Identifier 10 minutes, PM 10 minutes) are configuration and can be tuned per agent later.
- **Deployment**: Railway configuration is out of scope, as for features 003 and 004. The orchestrator must be startable as its own process with only its own variables plus the agents' variables.
- **Out of scope**: the agents themselves, news-triggered Research, the Assistant, the dashboard, and alerts. Owner preferences recorded here for the Opportunistic Identifier's own feature are also not decided by this feature: design A (plain code pre-screens the universe, then one LLM call reviews a shortlist of about 20 names), and Qwen3.7-Plus as the preferred model. Using a non-Anthropic model needs its own ADR, and a constitution amendment (the constitution names the `anthropic` SDK for every LLM agent), in that feature, before any code.
