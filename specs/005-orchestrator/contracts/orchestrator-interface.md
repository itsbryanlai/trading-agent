# Contract: Orchestrator Interface

## Process

| Command | Needs | Does |
|---|---|---|
| `python -m trading_agent.orchestrator` | `ORCHESTRATOR_DATABASE_URL` (a `ta_orchestrator` login), `config/schedule.yaml`, and the variables listed for enabled agents | The startup sequence (O12), then a tick every 30 s |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Clean stop (tests: `max_ticks` reached) |
| 2 | Refused to start: missing variable, bad config (including the prefix rule), or another instance holds the lock |
| 3 | Database unreachable, or the connection was lost |

An agent failing never ends the orchestrator.

## Configuration: `config/schedule.yaml`

```yaml
research:
  enabled: false
  module: trading_agent.research
  env: []                      # names only; each must start with RESEARCH_
  timeout_minutes: 15
  daily_at: "08:30"            # ET
  interval_minutes: null       # optional intraday runs; null = off
opportunistic_identifier:
  enabled: false
  module: trading_agent.opportunistic_identifier
  env: []                      # OPPORTUNISTIC_IDENTIFIER_*
  timeout_minutes: 10
  window_start: "10:00"
  window_end: "15:00"
  interval_minutes: 60
portfolio_manager:
  enabled: false
  module: trading_agent.portfolio_manager
  env: []                      # PORTFOLIO_MANAGER_*
  timeout_minutes: 10
  morning_session: "10:00"
  min_spacing_minutes: 30      # >= 30 (ADR 0011)
  report_wait_minutes: 5
  last_start: "15:30"          # <= 15:30 (ADR 0011)
  before_close_minutes: 30     # >= 30; the cutoff is the earlier of this and last_start
```

Every key is required and unknown keys are rejected. The bounds are in research O13. The file is changed only through code review (FR-015).

## Agent contract (for the agent features)

- **Entry point**: `python -m trading_agent.<agent>`, exiting with 0 on success and non-zero on failure.
- **Environment**: the agent receives only its listed `<PREFIX>*` variables plus `PATH`, `HOME`, `LANG`, `LC_ALL`, `TZ` and `PYTHONPATH`.
- **Stopping**: it may be sent SIGTERM at its timeout, and SIGKILL 10 s later. It must be safe to stop at any point, and safe to run again. For the PM, that safety comes from target weights plus the gate (ADR 0011).
- **Logs**: it writes its own logs to stdout and stderr.

## Run outcomes and skip reasons

| Outcome | When |
|---|---|
| `succeeded` | Exit status 0 |
| `failed` | Non-zero exit (`detail` holds the exit status), or the launch failed (`detail` holds the error type) |
| `timed_out` | Stopped at the timeout |
| `interrupted` | Still `running` when the orchestrator restarted |
| `skipped` | `detail`: `trading paused`, `pause flag unreadable`, or `previous run in progress` |

## Log lines

| Level | When | Content |
|---|---|---|
| INFO | Start and finish | `orchestrator: <agent> <reason> started` / `… finished: <outcome> (<detail>)` |
| WARNING | Skip, failure or timeout | `orchestrator: <agent> <reason> <outcome>: <detail>` |
| WARNING | A PM run is due but paused, once per pause episode | `orchestrator: portfolio_manager due but trading paused` |
| ERROR | The report-time view or the pause flag is unreadable | `orchestrator: cannot read <what>: <error type>` |
| CRITICAL | Refused to start, or lost the database | The reason. Never a variable's value or the connection string |
