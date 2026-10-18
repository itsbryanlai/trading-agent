# Data model: The Assistant

The Assistant has **no table, column, view, grant or migration**. It reads through its existing role and writes nothing (FR-009).

## Read (all already granted to `ta_assistant`)

Per `specs/001-data-model/contracts/role-grants.md`, through the menu entries in contracts/query-menu.md:
- `positions`, `account_snapshots`, `system_state_effective`;
- `decisions`, `decision_reports`, `risk_verdicts`;
- `reports`, `reports_with_status`;
- `orders`, `execution_refusals`, `in_flight_orders`;
- `stop_loss_triggers`, `journal`, `orchestrator_runs`, `instrument_reference`.

## Read from files

The `settings` allowlist (contracts/query-menu.md), from `config/*.yaml` in the deployed checkout.

## New login

`ta_assistant_login`, a member of `ta_assistant`, created by the existing logins command (the eleventh row in `storage/logins.py`). It becomes `ASSISTANT_DATABASE_URL`.

## In memory only

| Entity | Fields | Lifetime and rules |
|---|---|---|
| `Update` | update_id, kind (`message` or `edited_message`), chat id, chat type, date, text or none | one poll batch. Only the authorised private chat's updates are kept past classification (A5) |
| `Inbound` | one of: `TextOnly`, `Command`, `ActionRequest(matched_word)`, `Question(text)` | one message (A6). A `Question` the plan call flags becomes a `model_flag` refusal (A8) |
| `QueryChoice` | name, symbol, agent, from_date, to_date, section | one question. It is either `Valid` (with UTC bounds) or `Dropped(reason)` (A10) |
| `QueryResult` | name, parameters, rows (dicts, timestamps in both zones), cut | one question. Rows are capped, and untrusted fields are cut (A11) |
| `Exchange` | asked_at, question, reply | in the `Memory` deque: at most `memory.exchanges`, cleared after `memory.idle_minutes` of quiet and on restart. Refusals and fixed replies are not stored (A13) |
| `Outcome` | kind, queries, dropped, cut, tokens per call, links removed, message count | one log line (contracts/assistant-interface.md) |

## State transitions

- **Starting**: `starting` → `draining` (A4) → `polling`.
- **Running**: in `polling`, each authorised message goes `classified` → `answered` (replies sent) → `confirmed` (the next poll's offset).
- **Stopping**:
  - SIGTERM: `polling` → `stopping` after the current message → exit 0;
  - repeated database failures: `polling` → exit 3.
