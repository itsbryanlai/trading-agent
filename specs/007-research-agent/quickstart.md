# Quickstart: Research agent

How to check that feature 007 works. Steps 1–3 need no keys and touch no network. Steps 4–5 are the owner's own, with real keys. Each costs one model call, about $0.01 on Qwen (research R12).

## 1. Install and run the offline tests

```bash
uv pip install --python .venv/bin/python -e ".[dev]"
```

```bash
.venv/bin/python -m pytest tests/unit/research -q
```

**Expected:** all pass, covering every drop reason, every failure category, the config bounds, and the SC-002 property.

## 2. Integration tests (Docker `ta-pg` on port 5433)

```bash
TEST_DATABASE_URL=postgresql://postgres:dev@localhost:5433/postgres .venv/bin/python -m pytest tests/integration -m integration -q
```

**Expected:** all pass. That includes:
- migration 0011's check: a sell at 0 is accepted, a buy at 0 is rejected, and a null size on an actionable row is rejected;
- Research's writes as `ta_research`, all or nothing;
- the unchanged grants matrix.

## 3. Lint

```bash
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
```

## 4. Owner: a try-out run (no writes)

Set `RESEARCH_FINNHUB_API_KEY` and `RESEARCH_DASHSCOPE_API_KEY` in your own shell. Optionally add one or two tickers to `config/research.yaml`'s `watchlist` locally. Then run:

```bash
.venv/bin/python -m trading_agent.research --dry-run
```

**Check:**
- The would-be reports print as JSON lines. Each one has citations whose URLs are real Finnhub articles.
- The token use and input size print. Compare them with research R12's estimate.
- Company news for each watchlist symbol arrived, or is listed as missing. A `403` on a symbol answers research R3's open question about the free tier.
- The model answers in strict schema mode. If the provider rejects the `json_schema` response format, the run reports `model_unavailable`; if its answer is still unusable, `unusable_answer`. Either means revisiting research R5.

## 5. Owner: the Sonnet switch (optional)

In a local copy of `config/research.yaml`, set `model.provider: anthropic` and `model.name: claude-sonnet-5-5`. Set `RESEARCH_ANTHROPIC_API_KEY`, then repeat step 4. Expect the same shape of output, and roughly 5× the cost.

## 6. Enabling it

This feature sets `research.enabled: true` in `config/schedule.yaml`. Nothing runs until the orchestrator is deployed, which is a later item. On the orchestrator's service, set the four `RESEARCH_*` variables. Put the same DashScope key in `RESEARCH_DASHSCOPE_API_KEY`, as decided.
