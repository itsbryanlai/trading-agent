# Contract: fixed replies

These replies are written in code, with no model call (spec Key Entities). They live in `replies.py`, are plain text, and are sent with link previews off. `{…}` marks a value filled in by code. These are the intended texts. Tests check each reply's distinguishing phrase, not its exact wording.

| Id | When | Text |
|---|---|---|
| R-HELP | `/start`, `/help`, any other `/command` | I answer questions about this paper-trading system: positions and cash, the PM's decisions and the Risk Gate's verdicts, orders and fills, the analysts' reports, the journal and each agent's book, agent runs, and the system's settings. I can't take any action. Messages starting with these words are refused: {action_words}. To ask about one, phrase it as a question, e.g. "why did the PM sell MU?". Times are shown in {time_zone} with New York time (ET). |
| R-TEXT-ONLY | an edited message, or one without text | I only answer new text messages. Please send your question again as a new message. |
| R-REFUSED-LINE | the first line of every action refusal | Nothing was done: I can't take actions. |
| R-REFUSED-FALLBACK | the refusal's model call failed | R-REFUSED-LINE, then: Messages starting with {matched_word} are refused, as are: {action_words}. To pause or resume trading use {controls}. To ask about it, phrase it as a question, e.g. "why did the PM sell MU?". |
| R-SKIPPED | at start, when messages from the authorised chat were waiting (A4) | I was offline and skipped {n} message(s) you sent meanwhile. Please send again anything you still want answered. |
| R-TOO-MUCH | the answer document would exceed `max_input_chars` | That needs more data than I can read at once. Please narrow it: one symbol, one agent, or a shorter period. |
| R-DATABASE | the database could not be read (A15) | I can't read the data right now. Please try again in a few minutes. |
| R-MODEL-FAILED | a model call raised, or its answer failed validation | I couldn't answer that: the model {category}. Please try again. `{category}` is one of: was unavailable, refused, rejected the request, was cut off, gave an unusable answer. |

`{controls}` is the same fixed list as the refusal call's `controls` (contracts/model-io.md). It changes when the dashboard ships.
