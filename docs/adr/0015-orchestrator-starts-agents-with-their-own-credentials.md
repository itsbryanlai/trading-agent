# 0015. The orchestrator starts each agent as a process, passing it only its own credentials

Status: accepted

## Context

[0003](0003-orchestrator-is-a-scheduler-not-an-authority.md) made the orchestrator the one scheduler for the LLM agents: Research, the Opportunistic Identifier and the Portfolio Manager. [0011](0011-event-driven-portfolio-manager-runs.md) added event-driven PM runs and a narrow read of report times. Neither ADR said how the orchestrator starts an agent, or where each agent's credentials live. Those credentials are its model-provider key, any data key, and its own database login.

[0013](0013-deterministic-services-run-their-own-loops.md) considered "the orchestrator spawns and supervises separate processes" for Execution and the gate's trigger runner, and chose separate platform services instead. That was because both of those processes' credentials would otherwise sit in the orchestrator's environment. The LLM agents raise the same question.

Specifying the orchestrator (`specs/005-orchestrator`, Clarifications 2026-09-30), the owner chose this shape. An `/speckit-analyze` review pointed out that it is a change to the system's shape, which Constitution V says needs an ADR.

## Decision

1. **Each agent runs as its own child process**, `python -m trading_agent.<agent>`, started by the orchestrator in its own process group, with a per-agent timeout.
2. **Every agent's credentials are set on the orchestrator's platform service.** The orchestrator passes each agent **only** the variables listed for it in `config/schedule.yaml`, plus a fixed non-secret base (`PATH`, `HOME`, `LANG`, `LC_ALL`, `TZ`, `PYTHONPATH`).
3. **Every listed name must start with the agent's own prefix**: `RESEARCH_`, `OPPORTUNISTIC_IDENTIFIER_` or `PORTFOLIO_MANAGER_`. The orchestrator refuses to start otherwise. So configuration can never hand an agent a broker credential, another component's database login, or another agent's key.
4. **The orchestrator never reads, logs or records those values.** It only copies names into the child's environment. Its own credential is `ORCHESTRATOR_DATABASE_URL` alone.
5. **Broker credentials are never set on this service.** They stay with Execution alone (Constitution I, III).

## Alternatives considered

- **A platform service per agent**, triggered remotely by the orchestrator. The orchestrator would then hold no agent secrets. Not chosen for now: it means three more services, plus a remote-trigger mechanism, for a paper-trading system. Revisit if an agent ever needs a credential more sensitive than a model or data key and its own database login.
- **Agents fetch their own secrets from a secrets store at startup.** Not chosen: a new piece of infrastructure and a new dependency, for the same reason.
- **Agents run inside the orchestrator's process** (imported and called). Rejected: then every agent's code could read every other agent's credentials, and a hung agent couldn't be stopped without stopping the scheduler.

## Consequences

- **A shared environment.** The orchestrator's service environment holds the three agents' model and data keys and their database logins. A compromise of the orchestrator process, or of that service's configuration, exposes all three. It still exposes no broker credential, and no Risk Gate, Execution or reference-data login, because those live on other services (0013).
- **Credential naming is fixed.** Each agent feature names its variables with its own prefix, for example `RESEARCH_ANTHROPIC_API_KEY`, or the Opportunistic Identifier's model key as `OPPORTUNISTIC_IDENTIFIER_…`.
- **The orchestrator manages processes.** It supervises child processes: timeouts, stopping process groups on its own shutdown, and cleaning up orphans after a crash. The same duty was declined for Execution in 0013, and is accepted here because the agents are the orchestrator's own responsibility (0003).
