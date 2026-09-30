"""The orchestrator: starts the LLM agents on their cadences, and nothing else.

A scheduler with no authority (ADR 0003): it decides when Research, the
Opportunistic Identifier and the Portfolio Manager run (ADR 0011), never what they
conclude. It runs its own loop (ADR 0013) and starts each agent as a child process
with only that agent's own variables (ADR 0015). Behaviour: specs/005-orchestrator.
"""
