# Implementation Plan: Research agent

**Branch**: `007-research-agent` | **Date**: 2026-10-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/007-research-agent/spec.md`

## Summary

`python -m trading_agent.research` runs once a day at 08:30 ET, started by the orchestrator. It:
1. fetches general market news and company news for the owner's watchlist from Finnhub;
2. makes one model call, to Qwen `qwen3.7-plus` by default, or to Anthropic `claude-sonnet-5-5` by configuration;
3. checks every proposal in the answer;
4. writes one `reports` row per valid proposal, or a single `no_action` row, in one transaction.

**How it's built**:
- **Pure core:** selecting articles (R4) and checking the answer (R6) are pure code. That makes "nothing invented reaches the PM" (SC-002) a provable property.
- **Two ports:** the news source and the model client, each with a fake for tests (R1, R5).
- **Migration 0011:** a sell report may suggest 0, meaning a full exit. It also closes a null-size gap (R13).
- **Orchestrator entry:** this feature enables `research` in the orchestrator's config (R15).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**:
- psycopg 3.2, PyYAML, and exchange-calendars (via `trading_agent.risk.calendar`), all existing;
- the standard library's `urllib` for Finnhub and Qwen;
- **new**: `anthropic>=1,<2`, the official SDK, used only when the provider is `anthropic` (R5).

**Storage**: the shared Postgres. Research inserts `reports` rows as `ta_research`, with no new table or grant. Migration 0011 changes one check constraint.

**Testing**: pytest with Hypothesis.
- **Offline:** fake news and model ports.
- **Adapters:** a fake `urllib` opener for Qwen and Finnhub, and an injected fake client object for the Anthropic SDK.
- **Integration:** for the write, the constraint and the grants.
- **No network:** `tests/conftest.py` blocks it.

**Target Platform**: a Linux child process of the orchestrator, on its Railway worker service (not deployed yet).

**Project Type**: an LLM agent in the existing `trading_agent` package; one run per process.

**Performance Goals**: fits the orchestrator's 15-minute timeout by construction: the config loader refuses any combination of model timeout, watchlist size and pacing whose worst case exceeds it (R11). The shipped defaults take about 11 minutes at worst. Reports land by 08:45 ET (SC-001).

**Constraints**:
- **No prices, positions, cash or trading data.** The only database write is its own `reports` rows.
- **Only `RESEARCH_*` variables** are read.
- **Model output is checked before it's written**, whatever the provider.
- **No automatic failover** between providers or models.

**Scale/Scope**: one run a day. About 20–120 articles in, and 0–10 reports out. A watchlist of at most 50 symbols.

## Constitution Check

*GATE: must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Deterministic trade path | Research has no broker credential and no path to one. Its only output is `reports` rows, which pass through the PM, the gate and Execution. Its variables are `RESEARCH_*` only (ADR 0015). | Pass |
| II. Analysts propose, PM decides | It writes reports only, with a suggested size as an unbinding target weight. Citations are structured and rebuilt from fetched articles (FR-008). It never reads `config/risk.yaml`, decisions or the portfolio. | Pass |
| III. Least privilege at the database | No new grant: it uses the existing `SELECT, INSERT` on `reports`, and row-level security pins `agent = 'research'`. An integration test asserts it still can't read positions, decisions or orders. | Pass |
| IV. Autonomous, one hard stop | No approval gate is added. | Pass |
| V. Spec and ADR first | Covered by ADRs 0002, 0008, 0015, 0016, 0017 and 0018 (the new provider, with the constitution amended to v1.1.0). `docs/specs/research-agent.md` and `data-model.md` are updated (FR-022). The 0011 constraint change is a data-model change inside an existing component, with no new role, flow or credential, so it needs no ADR. | Pass |
| VI. Paper only, US equities | Not affected. Research doesn't filter the universe; the gate re-checks every buy. | Pass |
| VII. Assistant and dashboard read-only | Not affected. They already read `reports`. | Pass |
| Technology section (v1.1.0) | Qwen goes through QwenCloud's OpenAI-compatible API, and Anthropic through the `anthropic` SDK, chosen in config. | Pass |

**Re-check after design**: all pass. The design adds one config file and one constraint change. It adds no table, no grant and no variable outside `RESEARCH_*`.

## Project Structure

### Documentation (this feature)

```text
specs/007-research-agent/
├── spec.md
├── plan.md                       # this file
├── research.md                   # R1–R15
├── data-model.md                 # reports rows, migration 0011, in-memory entities
├── quickstart.md
├── contracts/
│   ├── research-interface.md     # CLI, exit codes, env, config, answer schema, drop reasons, logs
│   └── ports.md                  # NewsSource, ModelClient
├── checklists/requirements.md
└── tasks.md                      # /speckit-tasks
```

### Source Code (repository root)

```text
src/trading_agent/
├── research/
│   ├── __init__.py
│   ├── __main__.py          # env, config, --dry-run, exit codes (R9, R10)
│   ├── config.py            # config/research.yaml loader (R11)
│   ├── ports.py             # NewsSource, ModelClient, RawArticle, ModelReply, errors
│   ├── finnhub.py           # NewsSource over urllib (R3)
│   ├── qwen.py              # ModelClient over urllib, OpenAI-compatible (R5)
│   ├── anthropic_client.py  # ModelClient over the anthropic SDK (R5)
│   ├── selection.py         # pure: window, caps, duplicates, ids, size limit (R4)
│   ├── prompt.py            # PROMPT_VERSION, system prompt, user document (R7)
│   ├── answer.py            # pure: ANSWER_SCHEMA, check(), drop reasons, rows (R6)
│   └── service.py           # one run; store protocol + Postgres store (R8)
└── storage/migrations/
    └── 0011_report_sell_size.sql   # R13

config/research.yaml         # new; watchlist empty
config/schedule.yaml         # research enabled, env listed (R15)

tests/
├── fakes/news.py, fakes/model.py
├── unit/research/           # selection, answer (tables + property), config, service, main,
│                            # qwen, anthropic_client, finnhub, import guard
└── integration/
    ├── research/            # write as ta_research, all-or-nothing, RLS
    └── storage/             # 0011 constraint; grants matrix unchanged

docs/specs/research-agent.md, docs/specs/data-model.md   # FR-022
.env.example                                             # RESEARCH_* names
pyproject.toml                                           # anthropic dependency
```

**Structure Decision**: a new `trading_agent.research` package, built like `reference` and `orchestrator`.
- **It may import** `trading_agent.risk.calendar`, `storage.db` and `reference.symbols.is_plausible_ticker` (a pure function).
- **It imports nothing** from `execution`, the rest of `risk`, or `orchestrator`. An import-guard test enforces that.

## Things flagged for the owner

1. **The `reports` constraint (migration 0011)**: one loosening and one tightening, both on analyst reports. No risk limit, sizing rule or order logic changes. The PM and the gate are unaffected.
   - **Loosened:** a sell may suggest 0 (your clarification).
   - **Tightened:** found while planning. Today an actionable report may have a **null** suggested size, because `NULL > 0` passes a CHECK. 0011 closes it, as `reports_conviction_range` already does for conviction. **Owner, 2026-10-01: include the tightening.**
2. **The Anthropic refusal fallback is off.** The Claude API reference recommends enabling server-side `fallbacks` by default on Sonnet 5.5. With it on, a refused request is re-run on another Claude model. I've left it off to match ADR 0018's "no automatic failover": a refusal becomes a `no_action` report saying `model_refused`. It only applies when Research runs on Sonnet. **Owner, 2026-10-01: keep it off.**
3. **A new dependency**: `anthropic` 1.x, the official SDK, which the constitution names for Anthropic. Qwen and Finnhub use the standard library, so no `openai` package is needed.
4. **Not yet confirmed; your try-out run settles all three** (quickstart step 4):
   - ~~whether Qwen enforces a JSON schema~~: settled from QwenCloud's structured-output guide. Qwen3.7-Plus supports strict schema mode, and thinking needs streaming, so the plan uses strict schema mode with thinking off (research R5);
   - whether Finnhub's `/company-news` is free for every symbol. A 403 per symbol is handled as missing news;
   - the token and cost estimates (R12).
5. **Prompt injection reaching the PM**: Research's rationale is model-written text that the PM's model will read. That's recorded in the spec's assumptions for the PM feature.
6. **Token Plan key** (owner, 2026-10-02): Research uses a QwenCloud Token Plan key; see the spec's Clarifications. The endpoint comes from `RESEARCH_QWEN_BASE_URL`, so a pay-as-you-go key is a swap of two values, not code.
7. **Before deploying**: migration 0011 needs the migration admin to own `reports`. That's the same item already on your list for 004.

## Complexity Tracking

No constitution violations to justify.
