# Contract: the `run_while_paused` setting

`config/schedule.yaml`, under `portfolio_manager`:

```yaml
portfolio_manager:
  run_while_paused: true   # observe-only (ADR 0021); set false at switch-on
```

- **Required**, like every key in the file. A missing key, or a non-boolean value, makes the loader refuse to start (`ScheduleConfigError`, exit 2), naming the key.
- **`false`**: today's behaviour, unchanged. A known pause blocks every PM run (morning session and event-driven) with the reason "trading paused".
- **`true`**: a known `trading_paused = true` does not block PM runs. Every other rule still applies: spacing, report wait, cutoff, catch-up and no backfill.
- **Unknown flag** (`trading_paused` unreadable, `None`): blocks PM runs whatever the setting says (fail closed, review L2). If that happens when the morning session is due, the planner records the skip, and that day's morning run is lost even with the setting true (existing behaviour, `planner.py`). Event-driven runs resume once the flag reads again.
- Analyst runs are unaffected in every case.
- Each PM run started while paused is recorded as usual. Its record shows nothing special, but the orchestrator logs one line at startup when the setting is `true`: `portfolio_manager runs while paused (observe-only, ADR 0021)`.

## Tests

- **Loader**: the key is required and must be a boolean.
- **Planner**, a paused day with the setting `false`: PM runs are blocked as today. This is the existing test kept unchanged.
- **Planner**, a paused day with the setting `true`: the morning session and event-driven runs start on the same schedule as an unpaused day.
- **Planner**, an unknown flag with the setting `true`: blocked, and an unknown flag at the morning session records the skip and claims the slot (pins the existing behaviour, analyze N4).
- **Hypothesis property**: with the setting `true`, the PM plan for a paused day equals the plan for the same day unpaused.
