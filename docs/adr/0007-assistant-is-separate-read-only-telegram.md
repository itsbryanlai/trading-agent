# 0007. The Assistant is a separate agent, read-only, reachable over Telegram

Status: accepted

## Context

The user needs to ask questions about portfolio state, agent activity, and
reports without waiting for the dashboard. The question is whether that's a
new capability bolted onto the orchestrator, or its own agent — and if its
own, how much it's allowed to do.

## Decision

The Assistant is a fourth LLM agent, entirely separate from the orchestrator
(see ADR 0003 — the orchestrator has no data access or LLM call, so a
conversational Q&A capability could not live there without contradicting that
ADR). Its database role is **read-only across every table**: reports, PM
decisions, risk verdicts, orders, positions, journal. It has no write grants
anywhere and cannot trigger, influence, or veto any trade.

It is reachable via a Telegram bot scoped to a single authorized chat ID, using
its own bot token, kept separate from the Alpaca/Anthropic credentials — same
credential-separation principle as everything else in this system.

Manual pause/resume of trading (the one control the dashboard exposes) is a
plain deterministic toggle the orchestrator checks before running the PM, not
a capability of the Assistant. Routing "pause trading" through an LLM's
interpretation of a chat message adds a misread-intent failure mode (an
accidental halt or resume of real trading) for no benefit over a button.

## Alternatives considered

- Fold Q&A into the orchestrator. Rejected outright by ADR 0003's own
  boundary — the orchestrator would need trading-data read access and an LLM
  call it's specifically designed not to have.
- Let the Assistant also execute the pause/resume toggle via chat. Rejected
  for now (see above); could be revisited later as a narrow, explicit
  exception if a plain button proves insufficient — not as a default capability.

## Consequences

- A prompt-injected or simply wrong Assistant response can mislead the user,
  but cannot itself move money, change a position, or alter risk config — its
  blast radius is bounded by its database role, the same enforcement
  mechanism ADR 0004 established for every other component.
- The Telegram surface only ever needs one more credential type
  (`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`) and no new grant categories
  beyond "read-only."
