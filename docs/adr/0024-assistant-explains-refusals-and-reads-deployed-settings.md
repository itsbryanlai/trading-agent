# 0024. The Assistant's model explains code's refusals, and the Assistant reads a fixed list of deployed settings

Status: accepted

Accepted by the owner on 2026-10-17. Refines [0023](0023-assistant-runs-its-own-service-and-reads-through-a-query-menu.md) §4 and §10. The owner chose both decisions during `/speckit-clarify` for `specs/013-assistant` on 2026-10-17.

## Context

0023 §10 gave a request to pause, resume or trade a fixed reply written in code. During clarify the owner chose to keep the decision in code, but wanted the reply written the way an agent reports a tool's result: why the request was refused, and what to do next. A fixed reply can't adapt to the message. The owner also pointed out that a refusal on a keyword match has to say which keywords trigger it, because nobody remembers the exact list.

0023 §4 limited reads to database queries. The owner also wants answers to questions like "what's the max position size?", "when does Research run?" or "which model does the PM use?". Those values live in the repository's configuration files, not in the database. That is a new data source.

## Decision

1. **Code decides what is refused.** A message whose first word is on a configured list of action words is refused before any query or model call. The default list is pause, resume, unpause, stop, halt, buy, sell, close, cancel, trade and liquidate, matched ignoring case. Nothing changes. A message that only contains such a word later on ("why did the PM sell MU?") is an ordinary question. The model never decides whether a message is an action request.
2. **The model writes the explanation, after a fixed first line.** The reply starts with a line written in code saying nothing was done, because the Assistant cannot take actions. One model call then writes the rest from the refusal's outcome, the way an agent reads a tool's result. That call is given:
   - the owner's message;
   - the word matched, and the full list of action words;
   - where the real control lives: the dashboard's pause control, and the deployment runbook until the dashboard exists.

   It is given no credential, no other configuration value, no query result and no stored model text, so nothing written by another model can steer that reply. If the call fails, the fixed line is sent with the word list and the controls' location, written in code. `/help` lists the action words.
3. **A settings entry joins the query menu.** It reads the configuration files deployed with the service, and exposes only a fixed list of non-secret settings, kept in code:
   - the risk limits and universe rules in `config/risk.yaml`;
   - for each scheduled agent, whether it is enabled, and its times and intervals from `config/schedule.yaml`;
   - each LLM agent's model provider and model name.

   Variable names (`env:` lists), every environment value and every credential stay out. The settings entry follows the same rules as the database queries: the model chooses it, code runs it, and its results are data in the prompt.
4. **Deployed values are what it reports.** The service reads the files in its own deployment, so its answer shows what was released to `release/prod`, and it says so.

## Alternatives considered

- **A refusal written entirely in code** (0023 §10 as written). Not chosen: it can't explain the refusal in terms of the owner's own message or suggest how to rephrase it.
- **The model deciding what is an action request.** Rejected by the owner: the boundary should be decided by code, which the tests can check exactly.
- **Matching an action word anywhere in the message.** Rejected: it would refuse ordinary questions like "why did we buy NVDA?" or "is trading paused?".
- **Giving the explanation call the conversation or query results.** Not chosen: stored model text could then steer the one reply that is about the system's controls.
- **No settings in 013.** Rejected by the owner.
- **Exposing whole configuration files.** Not chosen: a fixed list keeps variable names and any future sensitive key out by default. Each addition is a reviewed code change.

## Consequences

- **An action request now costs one model call.** The refusal itself still happens in code, before the model call.
- **The action list is configuration**, changed through review. A word added to it refuses every message that starts with it.
- **The risk limits reach the Assistant's model provider** when they are asked about. They aren't secrets, and they never reach the Portfolio Manager through this path: the Assistant's output goes only to the owner.
- **Settings answers can lag `main`** until a release reaches `release/prod`. That is intended: they describe what is running.
- **No new credential, grant, migration or service.** The settings come from files already in the deployed image.
