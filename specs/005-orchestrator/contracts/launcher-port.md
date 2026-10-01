# Contract: Launcher Port

The only way the orchestrator starts and stops agents (research O1, O3). There is one real implementation (`trading_agent.orchestrator.launcher.SubprocessLauncher`) and one fake (`tests/fakes/launcher.py`).

| Call | Returns | Notes |
|---|---|---|
| `start(module, env)` | `Handle`, carrying `pgid` | Raises `LaunchFailed(error_type)` if the process can't start. `env` is the complete environment: nothing else is inherited |
| `poll(handle)` | `None` while running, else the exit status | Never blocks. Once the agent has exited, it kills anything left in its process group (adversarial review M1) |
| `stop(handle)` | the exit status | SIGTERM to the process group, then SIGKILL after 10 s. Blocks for at most about 10 s |

| `stop_group(pgid, module)` | `True` if it stopped a live group running `module`, `False` if the group was gone or runs something else | Used at startup to reap orphans after a crash (research O12). Checks the leader's command line before signalling |

The fake records every start with its module, environment names and injected clock time. It lets a test finish, fail or hang any handle on demand.
