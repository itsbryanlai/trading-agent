# Contract: Launcher Port

The only way the orchestrator starts and stops agents (research O1, O3). There is one real implementation (`trading_agent.orchestrator.launcher.SubprocessLauncher`) and one fake (`tests/fakes/launcher.py`).

| Call | Returns | Notes |
|---|---|---|
| `start(module, env)` | `Handle` | Raises `LaunchFailed(error_type)` if the process can't start. `env` is the complete environment: nothing else is inherited |
| `poll(handle)` | `None` while running, else the exit status | Never blocks |
| `stop(handle)` | the exit status | SIGTERM to the process group, then SIGKILL after 10 s. Blocks for at most about 10 s |

The fake records every start with its module, environment names and injected clock time. It lets a test finish, fail or hang any handle on demand.
