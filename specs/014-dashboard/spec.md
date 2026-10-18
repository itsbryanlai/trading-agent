# Feature Specification: The Dashboard

**Feature Branch**: `014-dashboard`

**Created**: 2026-10-18

**Status**: Draft

**Input**: User description: "Feature 014, short name "dashboard". Source behavior: docs/specs/ui-dashboard.md, with the decisions in docs/adr/0026-dashboard-public-service-with-owner-sign-in.md (accepted). The owner's private web dashboard for the paper-trading system: sign-in, read-only views (positions and equity with an equity curve; order history traced to verdicts and decisions; agent activity; the journal with per-agent attribution; system state with an observe-only banner), one guarded pause/resume toggle, untrusted text rendered safely, a health check and a pre-release check, and the Assistant's control text updated to name the dashboard as built."

## Clarifications

### Session 2026-10-18 (before specify)

All recorded in [ADR 0026](../../docs/adr/0026-dashboard-public-service-with-owner-sign-in.md), accepted the same day. The owner chose every answer.

- Q: Who can reach it, and how? → A: A public address, with sign-in in the dashboard itself: one owner password, stored only as a hash; a signed session lasting about 12 hours; slowed repeated failures; a cross-site request check on every write. Rejected: a second factor (for now), an identity proxy, an IP allowlist.
- Q: Which database access? → A: Two new logins in one service, matching the grants contract: a read-only one for every page, and the control one opened only for the toggle's single write. The owner's own logins stay the owner's. Rejected: two services; reusing the owner's logins.
- Q: How is the toggle guarded? → A: A confirmation page before any write. Pausing is always allowed. Resuming is refused while the deployed schedule still says the Portfolio Manager runs while paused (ADR 0021 §9's order), and warned about, but allowed, while the market is open. One log line per toggle; no audit table.
- Q: Where does the equity curve come from, and how fresh are pages? → A: The recorded account snapshots, every one for a week or less and the last of each trading day beyond that. Pages refresh when reloaded, and show when they were read and how old their data is. Times in Asia/Singapore with ET alongside. No scripts on any page.
- Q: How is model- and broker-written text shown? → A: As Markdown without raw HTML; links kept only for `http` and `https`; everything else escaped; a page policy that runs no script and loads nothing from elsewhere.
- Q: Health and release checks? → A: An unauthenticated health check that shows no data, and an owner-run pre-release check, every line of which must read `ok`.
- Q: The Assistant's text? → A: It names "the dashboard's pause control" without "(not built yet)", and keeps the runbook as its second control.

### Session 2026-10-18 (clarify)

- Q: Where does "paused since" come from, given the Risk Gate also sets `system_state.updated_at` each trading day (baseline, halt)? → A: A new column, `trading_paused_changed_at`, stamped by a database trigger whenever `trading_paused` actually changes, by any path (the dashboard or the runbook's manual `UPDATE`); no role gains a write grant. It is empty until the first change after the migration, shown as "start time not recorded". This adds a migration, which ADR 0026 ruled out, so it is recorded in ADR 0027 before `/speckit-plan`. Rejected: no "since when"; showing `updated_at` labelled as the state's last change.
- Q: Can a session be revoked? → A: Yes. The dashboard keeps its live sessions in its own memory; the cookie carries only a random session ID, still signed with the session secret. Signing out deletes the session on the server, so a copied cookie stops working at once. A restart or redeploy ends every session. Nothing is written to the database or the container's disk. Rejected: signed cookies alone (a copied cookie works until it expires); a "sign out everywhere" button.
- Q: How are repeated failed sign-ins slowed? → A: Two delays, both in memory: a site-wide one (1 s, 2 s, 4 s … capped at 30 s, reset by a successful sign-in) and, on top of it, a per-address one on the same curve for the address the failures came from. A long password backs both: the command that makes the hash refuses one shorter than 16 characters. Nobody can lock the owner out; the worst a stranger causes is a 30-second wait for the owner on a fresh address. Rejected: per-address only (evaded by rotating addresses); a lockout (lets anyone lock the owner out).
- Q: Does agent activity include the orchestrator's runs, which `docs/specs/ui-dashboard.md` doesn't name? → A: Yes, every run, inline with the reports and decisions: agent, why it was due, slot, start and finish, outcome, with failed, timed-out and skipped runs marked and a filter for them. `docs/specs/ui-dashboard.md` gains the same line. Rejected: only the problem runs; leaving runs out.
- Q: Does the journal show performance over time? → A: Yes. Above the day rows, a comparison chart: one line per analyst from its stored `index`, and an account line compounded from each day's `account.return`, each rebased to 100 at its own first point in the range, with missed sessions marked; the same ranges as the equity curve. Beside it, each analyst's usage totals over the range (reports written, argued, `no_action`, cited, decisions citing them, approved, filled), summed from each row's `usage`. Rejected: day rows only; the chart without usage totals.

### Session 2026-10-18 (after `/speckit-plan`)

- Q: Can a sustained attacker still hold off the owner's sign-in under the two delays? → A: Yes, accepted by the owner (research D7). To cap guessing, an attempt arriving before the delay has passed is answered at once ("try again in N seconds") without being checked; a scripted attacker firing the moment each wait ends wins most races for the next slot, so while the attack lasts the owner's sign-in can be held off. It ends when the attack stops, and there is still no fixed lockout. Signed-in sessions, the runbook's manual `UPDATE` and the failure log lines are the fallbacks. This corrects the clarify answer's "the worst a stranger causes is a 30-second wait". Rejected: sleeping before checking (parallel requests defeat the cap); a second factor now (ADR 0026 deferred it).

### Session 2026-10-18 (after `/speckit-analyze`)

- Q: Is the dashboard's own copy of `schedule.yaml` enough to allow a resume, when the orchestrator may still run the old setting? → A: No. Resume also needs the newest `orchestrator_runs` row for the Portfolio Manager to be `skipped` with detail `trading paused`, which only happens once the orchestrator honours `run_while_paused: false` ([ADR 0028](../../docs/adr/0028-dashboard-resume-needs-the-orchestrator-to-have-skipped-a-pm-run.md), analyze S4). Switch-on can slip a day. Rejected: the file check plus a reminder to read the orchestrator's log.
- Q: Does allowing a resume during market hours change ADR 0021 §9? → A: No. The runbook still says resume after the close; the dashboard's warning makes a session-time resume a fallback (ADR 0028 §4, analyze S5). Rejected: refusing resume while the market is open.

## User Scenarios & Testing *(mandatory)*

The only user is **the owner**, who wants to open one page, from a phone or a laptop, and see what the system holds, what each agent has said and decided, what the gate and the broker did with it, and how each analyst would have done alone. This serves the system's purpose: watching each agent's performance over time with minimal intervention.

The dashboard is a window with exactly one lever: the pause/resume toggle. It cannot enter, change, approve or size a trade, cannot change a risk setting, and makes no model call ([ADR 0007](../../docs/adr/0007-assistant-is-separate-read-only-telegram.md), Constitution IV and VII, `docs/specs/ui-dashboard.md`).

### User Story 1 - Sign in and see the system at a glance (Priority: P1)

The owner opens the dashboard's address, signs in with their password, and lands on an overview: whether trading is paused and since when, whether the daily-loss halt is active for the current session, the observe-only banner when it applies, the current positions, cash and equity from the latest account snapshot with its age, and the equity curve.

**Why this priority**: The at-a-glance state is the reason to open the dashboard, and nothing else is reachable without signing in.

**Independent Test**: With seeded positions, snapshots and system state, sign in and check every figure on the overview against the seeded rows; without a session, every page except sign-in and the health check redirects to sign-in.

**Acceptance Scenarios**:

1. **Given** no session, **When** the owner opens any page, **Then** they are sent to the sign-in page, and no data is shown.
2. **Given** the sign-in page, **When** the owner enters the right password, **Then** they land on the overview; **When** they enter a wrong one, **Then** they see a generic "sign-in failed" message, and the next attempt is not checked until a delay has passed, up to 30 s site-wide plus up to 30 s more from the same address; an attempt before then is told how long to wait.
3. **Given** trading is paused and the deployed schedule lets the Portfolio Manager run while paused, **When** any page loads, **Then** a banner says trading is paused, since when, that the Portfolio Manager still runs, and that the gate rejects every buy.
4. **Given** positions and snapshots exist, **When** the overview loads, **Then** each position shows symbol, quantity, average entry price and cost; cash and equity show the latest snapshot's values with its time and age; and the curve plots the snapshots for the chosen range.
5. **Given** the halt was triggered for today's trading day, **When** the overview loads, **Then** it says new orders are halted for the rest of the session and clear at the next trading day.
6. **Given** a session older than about 12 hours, **When** the owner loads a page, **Then** they are sent to sign in again.
7. **Given** the owner signed out, **When** a request arrives with that session's old cookie, **Then** it is treated as having no session.

---

### User Story 2 - Pause and resume trading safely (Priority: P1)

The owner presses the one control, sees a confirmation page stating the current state and what the change will do, and confirms. A pause always goes through. A resume is refused, with the reason and where to look, while the deployed schedule still runs the Portfolio Manager while paused; and it carries a warning while the market is open.

**Why this priority**: It is the only manual control the design calls for (Constitution IV), and the two-step switch-on (ADR 0021 §9) depends on it not being pressed out of order.

**Independent Test**: Against a database with both logins, pause and resume through the pages and check `system_state` after each step; with the deployed setting true, check that resuming is refused and nothing changes.

**Acceptance Scenarios**:

1. **Given** trading is running, **When** the owner presses "Pause trading" and confirms, **Then** `trading_paused` becomes true, `updated_at` is set, the page shows the new state, and one log line records a pause and its time.
2. **Given** trading is paused, **When** the owner presses "Resume trading", **Then** a confirmation page shows how long it has been paused and that new buys become possible; nothing is written until they confirm.
3. **Given** the deployed schedule has `portfolio_manager.run_while_paused: true`, **When** the owner tries to resume, **Then** it is refused before any write: the page says step 1 of switching on is not released, and points to the deployment runbook's section on switching trading on.
3a. **Given** the deployed setting is false but the Portfolio Manager's newest run is not `skipped` with `trading paused` (the orchestrator hasn't honoured it yet), **When** the owner tries to resume, **Then** it is refused before any write, saying the orchestrator hasn't yet skipped a PM run for the pause.
4. **Given** that setting is false and the market is open, **When** the owner opens the resume confirmation, **Then** it warns that resuming during the session lets the next approved buy be submitted, and still allows confirming.
5. **Given** the confirmation page was opened while paused, **When** the state changes elsewhere before the owner confirms, **Then** confirming changes nothing and shows the current state.
6. **Given** a write request with a missing or wrong cross-site check, or no session, **When** it arrives, **Then** it is refused and nothing is written.

---

### User Story 3 - Trace every order back to its cause (Priority: P2)

The owner opens order history and sees every risk verdict, newest first: for each, the decision or stop-loss trigger behind it, the verdict and its rule when rejected, and what happened next: the order with its status, prices and fills, or Execution's refusal with its reason.

**Why this priority**: Rejected, trimmed and refused outcomes are as informative as fills, and the spec asks for them to be visible, not just filled orders.

**Independent Test**: Seed a rejected decision, an approved-and-filled one, an approved-and-refused one and a stop-loss exit, and check each appears once, traced to its cause, with the right outcome.

**Acceptance Scenarios**:

1. **Given** a decision rejected by `trading_paused`, **When** order history loads, **Then** it shows the decision (symbol, direction, target weight, time), the verdict "rejected" and the rule, and no order.
2. **Given** an approved verdict that became an order, **When** order history loads, **Then** it shows the order's status, limit price for a buy, fill quantity and price, and the broker's reason when rejected.
3. **Given** an approved verdict Execution refused, **When** order history loads, **Then** it shows the refusal's named reason instead of an order.
4. **Given** a stop-loss exit, **When** order history loads, **Then** it shows the trigger (price seen and when) in place of a decision.
5. **Given** more than one page of rows, **When** the owner filters by symbol or date, **Then** only matching rows show, 50 to a page.

---

### User Story 4 - Read what each agent said and decided (Priority: P2)

The owner opens agent activity and sees every report from both analysts, including `no_action` runs, and every Portfolio Manager decision with the reports it drew on, newest first, each rendered readably with its sources. The orchestrator's runs show whether a run failed, timed out or was skipped.

**Why this priority**: Watching each agent over time is the system's purpose, and the reasoning is where its behavior shows.

**Independent Test**: Seed reports, decisions with cited reports and runs, including rationale text containing HTML, a script, a `javascript:` link and an image, and check what renders and what does not.

**Acceptance Scenarios**:

1. **Given** reports from both analysts, **When** activity loads, **Then** each shows agent, time, symbol, direction, conviction, suggested size, its sources (title, publisher, date, relevance, link) and its rationale.
2. **Given** a decision citing two reports, **When** activity loads, **Then** the decision shows its reasoning, the quote it used and its time, and links to the two reports it drew on.
3. **Given** a rationale containing `<script>`, raw HTML, a `javascript:` or `data:` link, or an image, **When** it renders, **Then** the HTML shows as text, the non-http(s) link shows as plain text, the image is not loaded, and no script runs.
4. **Given** long text, **When** a list loads, **Then** each entry shows a short opening with the rest expandable without leaving the page.
5. **Given** filters for agent, symbol and date, **When** the owner applies them, **Then** only matching rows show.
6. **Given** a Research run that timed out with no report, **When** activity loads, **Then** the run shows with its slot, times and "timed out", marked, and the filter for problem runs shows it.

---

### User Story 5 - Read the journal and per-agent attribution (Priority: P3)

The owner opens the journal and sees each trading day's summary and attribution: the account's return next to each analyst's own book (its index, day return and holdings), with sessions missed and symbols carried at their last price called out.

Above the rows, a chart compares each analyst's book with the account over the chosen range, and each analyst's usage totals show how much of its work the PM used.

**Why this priority**: Attribution answers "how is each agent doing", but it is daily and rests on the views above.

**Independent Test**: Seed journal rows with attribution, including a missed session and an unpriced holding, and check every figure shown against the row.

**Acceptance Scenarios**:

1. **Given** journal rows, **When** the journal loads, **Then** each day shows opening and closing equity, the account's return, the summary, and per agent its index, day return and holdings with weights.
2. **Given** a row with missed sessions or unpriced holdings, **When** it renders, **Then** those are named on that day.
3. **Given** a row whose attribution has a schema version the dashboard does not know, **When** it renders, **Then** the summary still shows and the attribution says it cannot be displayed, without failing the page.
4. **Given** a month of journal rows, **When** the owner picks the 1-month range, **Then** the chart shows a line per analyst and one for the account, each starting at 100, and the usage totals equal the sums of those rows' counts.
5. **Given** an analyst whose book started mid-range, **When** the chart renders, **Then** its line starts at 100 on its `started_on` day, while the others start on the range's first day.

---

### User Story 6 - Release and check it safely (Priority: P3)

Before a release, the owner runs a check that confirms each credential works and is what it should be, without writing anything. After release, the platform's health check sees the service is up, and the Assistant names the dashboard as the place for the pause control.

**Why this priority**: It protects the release, not the daily use.

**Independent Test**: Run the check with good values, then with each one wrong in turn, and check each line; hit the health check with the database up and down.

**Acceptance Scenarios**:

1. **Given** correct values, **When** the owner runs the check, **Then** every line reads `ok`: the read login, the control login, the password hash, the session secret and the deployed setting; nothing is written.
2. **Given** the read value pointing at a login that can write, or the control value at a login with more than its two columns, **When** the check runs, **Then** that line is not `ok` and says which role it found.
3. **Given** the database is reachable, **When** the health check is requested, **Then** it answers `ok` with no other data; **When** it is not, **Then** it answers unavailable.
4. **Given** the dashboard is released, **When** the owner asks the Assistant to pause trading, **Then** the refusal names "the dashboard's pause control" and the runbook sections, with no "(not built yet)".

### Edge Cases

- **No data yet**: no snapshot, positions, reports, decisions or journal rows. Each view says so plainly; the overview shows "no account snapshot yet" in place of cash and equity.
- **One snapshot, or a range with none**: the curve shows the single point, or says there is no data for that range.
- **Stale data**: the latest snapshot is hours old (overnight, a weekend, Execution down). Its age is shown; the dashboard never presents an old figure as current.
- **The deployed setting is missing or unreadable**: resuming is refused, with that reason (fail closed); pausing still works; the banner says the setting could not be read.
- **Market hours cannot be determined**: the resume confirmation shows the market-hours warning anyway.
- **The control login fails, or its write fails**: the page says nothing was changed and why in general terms; the read pages keep working.
- **The read login fails**: pages show a database-unavailable page with no error text; the health check answers unavailable.
- **The toggle pressed twice, or two tabs**: the write applies only if the state is still what the confirmation page showed; otherwise nothing changes.
- **Pause pressed while already paused** (or resume while running): there is no such button; only the opposite action is offered.
- **The pause time was never recorded** (no change since the migration): "paused since" reads "start time not recorded"; nothing is inferred from `updated_at`.
- **The halt is active**: resuming the manual pause does not clear the halt, and the page says new orders stay halted until the next trading day.
- **The service restarts or is redeployed**: every session ends; the next page load asks the owner to sign in again.
- **Signed out, then an old cookie is replayed**: it is refused like no session at all.
- **Someone guesses passwords all day from many addresses**: at most one guess is checked per site-wide wait (up to 30 s), about 2,880 a day. Without such an attacker the owner waits at most 30 s; with one firing the moment each wait ends, the owner's attempts mostly get "try again in N seconds" until the attack stops (accepted, research D7). There is no fixed lockout; signed-in sessions keep working. A restart resets both delays.
- **Session expires on the confirmation page**: confirming sends the owner to sign in, and nothing is written.
- **The orchestrator is stopped, disabled or still on the old setting**: no PM run has been skipped for the pause, so resume is refused (fail closed); pausing still works.
- **Unknown symbol or date in a filter, or a page past the end**: an empty list, not an error.
- **Broker reasons and refusal details**: shown escaped as plain text.
- **Daylight-saving changes**: times are converted per moment, so ET is right on both sides of a change.

## Requirements *(mandatory)*

### Functional Requirements

**Access**

- **FR-001**: The dashboard MUST require a signed-in session for every page and action except the sign-in page and the health check.
- **FR-002**: Sign-in MUST check the owner's password against a stored hash only; the password itself MUST never be stored, logged or shown.
- **FR-003**: A session MUST expire about 12 hours after sign-in, and MUST be carried only in a cookie that page scripts cannot read, is sent only over HTTPS, and is not sent on requests from other sites. The dashboard MUST keep live sessions in its own memory, with the cookie carrying only a random session ID; signing out MUST delete the session on the server, after which that cookie MUST be refused. A restart MUST end every session.
- **FR-004**: Each failed sign-in MUST lengthen two delays held in memory: a site-wide one and one for the client's address (as the platform reports it), each doubling from 1 s up to 30 s, the two added together. An attempt arriving before its delay has passed MUST be answered at once with how long to wait, without checking the password or counting as a failure. A successful sign-in MUST reset the site-wide delay and that address's. Once the delay has passed, an attempt MUST always be checked (no fixed lockout), and the failure message MUST NOT say which part was wrong.
- **FR-004a**: The command that makes the password hash MUST refuse a password shorter than 16 characters, read it without echoing it, and print only the hash.
- **FR-005**: Every request that writes MUST carry a cross-site check value tied to the session, and MUST be refused without one.

**Views**

- **FR-006**: The overview MUST show the pause state and since when (from `trading_paused_changed_at`, or "start time not recorded" while it is empty; never from `updated_at`), whether the daily-loss halt is active for the current trading day, positions (symbol, quantity, average entry price, cost), and cash and equity from the latest account snapshot with its time and age.
- **FR-007**: The equity curve MUST plot recorded account snapshots: every snapshot for a range of a week or less, and the last snapshot of each trading day for longer ranges, with at least 1 day, 1 week, 1 month and all-time ranges.
- **FR-008**: Order history MUST list every risk verdict with its cause (decision or stop-loss trigger), its verdict and rejection rule, and its outcome (the order with status, prices, fills and broker reason, or Execution's refusal with its reason, or none).
- **FR-009**: Agent activity MUST list every report from both analysts, including `no_action` runs, with their sources; every Portfolio Manager decision with the reports it drew on; and every orchestrator run (agent, why it was due, slot, start and finish, outcome, and its detail escaped as plain text), with failed, timed-out and skipped runs marked and filterable.
- **FR-010**: The journal MUST show each trading day's equity, summary and per-agent attribution as defined by `specs/012-journal-writer/contracts/attribution.md`, and MUST show a row with an unknown schema version without failing.
- **FR-010a**: Above the day rows, the journal MUST show a comparison chart for the chosen range (the equity curve's ranges): one line per analyst from its stored `index`, and an account line compounded from each day's `account.return`, each rebased to 100 at its own first point in the range, with missed sessions marked. Rows with an unknown schema version, or a null return, MUST leave a gap rather than a guessed value.
- **FR-010b**: Beside the chart, the journal MUST show each analyst's usage totals over the range (written, argued, `no_action`, cited, decisions citing them, approved, filled), summed from each row's `usage`.
- **FR-011**: While trading is paused, every page MUST show a banner saying so, since when, whether the deployed schedule lets the Portfolio Manager run while paused, and that the gate rejects every buy.
- **FR-012**: Lists MUST show newest first, 50 rows a page, filterable by date range (the journal by its chart range), and by symbol and agent where they apply; long text MUST be collapsed with the rest expandable on the same page.
- **FR-013**: Every time MUST be shown in Asia/Singapore with ET alongside; every page MUST show when it was read.
- **FR-014**: Pages MUST work without running any script.

**Untrusted text**

- **FR-015**: Report rationales, decision reasoning and journal summaries MUST be rendered as Markdown with raw HTML shown as text; a link MUST be kept only if it is `http` or `https`, otherwise shown as plain text; images MUST NOT be loaded. Source titles MUST be escaped plain text, linked only when their URL is `http(s)`.
- **FR-016**: Broker reasons, refusal details and every other stored value MUST be shown escaped as plain text.
- **FR-017**: Every response MUST tell the browser to run no script, load nothing from another origin, submit forms only to the dashboard, and refuse to be shown inside another site's frame.

**The toggle**

- **FR-018**: The only write the dashboard can make MUST be setting `system_state.trading_paused` and `updated_at`, through the control login, opened for that one write and closed after it. Only the toggle's confirmation may use that login.
- **FR-019**: Pressing the toggle MUST show a confirmation page stating the current state, since when, and what the change does; only confirming MUST write.
- **FR-020**: Pausing MUST always be allowed.
- **FR-021**: Resuming MUST be refused before any write while the deployed `config/schedule.yaml` has `portfolio_manager.run_while_paused: true`, or when that setting cannot be read, with the reason and a pointer to the deployment runbook's switching-on section.
- **FR-021a**: Resuming MUST also be refused before any write unless the newest `orchestrator_runs` row for `portfolio_manager` has outcome `skipped` and detail exactly `trading paused` (ADR 0028), checked on the confirmation page and again at the write.
- **FR-022**: The resume confirmation MUST warn when the market is open, or when that cannot be determined, and MUST still allow confirming.
- **FR-023**: Confirming MUST change the state only if it is still what the confirmation page showed; otherwise nothing MUST change and the current state MUST be shown.
- **FR-024**: Each toggle MUST write one log line with its direction and time, and nothing else about the request.
- **FR-025**: Resuming MUST NOT clear or alter the daily-loss halt, and the page MUST say so while the halt is active.

**Boundaries**

- **FR-026**: The dashboard MUST NOT offer any way to enter, modify, approve, size or cancel a trade, or to change any risk, schedule or model setting.
- **FR-027**: The dashboard MUST make no model call and hold no broker, model or market-data credential. It MUST read, besides the database, only the one deployed setting in FR-021.
- **FR-028**: Credentials MUST be only the four `DASHBOARD_`-prefixed values: the read login, the control login, the password hash and the session secret. None may appear in a page, a log line or an error.
- **FR-029**: Logs MUST NOT contain stored model text, passwords, session or cross-site values, or database error text.

**Health, release and the Assistant**

- **FR-030**: An unauthenticated health check MUST answer `ok` when the read login can query the database and unavailable otherwise, with no other data.
- **FR-031**: An owner-run check MUST print one line per item, each `ok` or the reason it is not: the read login is a member of only the read role and can query; the control login is a member of only the control role; the password hash is well formed; the session secret is long enough; the deployed setting is readable. It MUST write nothing and print no credential.
- **FR-032**: The Assistant's refusals MUST name "the dashboard's pause control" without "(not built yet)", and keep the runbook's switching-on and switching-off sections as the second control; neither may contain a file name, domain or `@name`.
- **FR-033**: The deployment description MUST give the dashboard its own always-on service holding only its four variables, and the only service with a declared public address; the deployed-shape guard MUST enforce this.

### Key Entities

- **Owner session**: a random ID in the browser's cookie, and, in the dashboard's memory only, its sign-in time, expiry and cross-site check value. Deleted on sign-out, expiry or restart; nothing is stored in the database or on disk.
- **Pause state**: `system_state.trading_paused` and `updated_at`, the only data the dashboard writes, and `trading_paused_changed_at`, set by the database itself whenever `trading_paused` changes (empty until the first change after it is added).
- **Deployed run-while-paused setting**: `portfolio_manager.run_while_paused` from the schedule deployed with the service; read only.
- **Read data**: positions, account snapshots, reports and their sources, decisions and the reports they cite, risk verdicts, stop-loss triggers, orders, Execution's refusals, orchestrator runs, journal rows and their attribution, and the computed halt state, all through the read-only login.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After signing in, the owner can tell whether trading is paused, whether the halt is active, and the current equity and its age within 10 seconds of opening the dashboard on a phone.
- **SC-002**: 100% of seeded risk verdicts appear in order history exactly once, traced to their decision or trigger and their outcome.
- **SC-003**: 0 of a fixed set of hostile texts (script tags, event-handler attributes, `javascript:` and `data:` links, images, raw HTML) produces a script, an outside request or live HTML on any page.
- **SC-004**: 0 writes reach the database except `trading_paused` and `updated_at` through the control login; every other attempt by the dashboard's logins is refused by the database.
- **SC-005**: With the deployed setting true, 100% of resume attempts are refused with nothing written; 100% of pause attempts succeed.
- **SC-006**: Every page except sign-in and the health check returns no data without a session, in 100% of checked routes.
- **SC-007**: A typical page loads in under 2 seconds with a year of rows.
- **SC-008**: The owner can complete the release steps (logins, values, check, first sign-in) from the runbook alone, with every check line reading `ok` before release.

## Assumptions

- One user, the owner. No accounts, roles or invitations.
- The owner signs in from a recent mobile or desktop browser over HTTPS on the platform's generated address. A custom domain is out of scope.
- The dashboard has no live prices: positions show cost from average entry price, and equity comes from the latest snapshot. Market value per position is out of scope.
- The orchestrator's runs are in agent activity because failed, timed-out and skipped runs explain missing reports; `docs/specs/ui-dashboard.md` is updated to say so (clarify, 2026-10-18).
- Ending every session at once means restarting the service; there is no "sign out everywhere" button.
- The runbook's manual `UPDATE` as the owner's control login stays valid, and bypasses the toggle's guard; the runbook will name the dashboard as the preferred path.
- One migration adds `trading_paused_changed_at` and its trigger ([ADR 0027](../../docs/adr/0027-pause-change-time-recorded-by-the-database.md)); the release runs the migrate step in the runbook's release checklist.
- Switching on is unchanged: this feature does not change `run_while_paused`, the pause or any risk setting.
- Depends on: the existing tables, views and grants (`specs/001-data-model`); the journal's attribution contract (`specs/012-journal-writer`); the logins command (`specs/010-observe-only-deployment`); the Assistant's fixed replies (`specs/013-assistant`).
