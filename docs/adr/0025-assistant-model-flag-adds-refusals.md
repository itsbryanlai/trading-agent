# 0025. The Assistant's plan call can flag an action request as a second layer, and the default action words drop "stop" and "close"

Status: accepted

Accepted by the owner on 2026-10-18. Supersedes [0024](0024-assistant-explains-refusals-and-reads-deployed-settings.md) §1's sentence "The model never decides whether a message is an action request", and amends the default list in the same paragraph. The rest of 0024 stands. The owner chose both changes on 2026-10-18, after `/speckit-analyze` for `specs/013-assistant`.

## Context

0024 §1 has code refuse a message whose first word is on a configured list of action words. Two gaps showed up in review:

- **Ordinary questions refused.** "Stop-loss triggers this week?" and "Close price of MU?" start with `stop` and `close`, two words on the default list. They would be refused, although the Assistant has a `stop_losses` query to answer the first.
- **Polite requests missed.** "Please pause trading", "can you sell MU?" and "get rid of everything" don't start with an action word, so they reach the model as ordinary questions. Nothing can change either way, because the Assistant has no write grant, no write path in code and no broker key. A misread could still produce a misleading reply such as "Done, trading is paused".

## Decision

1. **The keyword check stays first, in code, as in 0024 §1.** A message whose first word is on the list is refused before any query or model call.
2. **A second layer: the plan call flags action requests.** The plan call's answer gains `action_request` (true or false). When it is true, code ignores the chosen queries, runs none, and sends the same refusal as a keyword match:
   - the fixed first line, written in code;
   - then the refusal call's explanation, from a document that records the trigger as `model_flag` and has no matched word.

   The flag can only add refusals. It can never stop a keyword refusal, because the keyword check runs before the plan call.
3. **The default list drops `stop` and `close`.** It becomes pause, resume, unpause, halt, buy, sell, cancel, trade and liquidate. "Stop trading" and "close my position" are covered by layer 2. The list stays configuration, changed through review.

## Alternatives considered

- **Keywords only** (0024 as written). Not chosen: requests phrased politely reach the answer call, with only the prompt's instructions in the way of a misleading reply.
- **Scanning replies for claims of having acted.** Rejected: it is brittle, and would also catch true answers such as "the PM sold MU on Tuesday".
- **The model deciding alone, with no keyword check.** Rejected in 0024: the boundary that tests can check exactly stays in code.
- **Keeping `stop` and `close` on the list.** Not chosen: they refuse common questions, and layer 2 catches the requests they were meant for.

## Consequences

- **A flagged message costs two model calls** (plan, then refusal) and reads no data.
- **A question the model wrongly flags is refused.** The owner rephrases it. The fixed first line makes the refusal unmistakable.
- **Refusal logs say which layer fired**: `trigger=keyword word=<w>` or `trigger=model_flag`.
- **No new credential, grant, data flow or service.**
