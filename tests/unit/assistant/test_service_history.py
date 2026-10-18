"""US2 scenarios through `service.handle` with fakes (specs/013 T017)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from tests.fakes.assistant_store import FakeStore
from tests.fakes.model import FakeModel
from trading_agent.assistant.config import load_config
from trading_agent.assistant.memory import Memory
from trading_agent.assistant.service import handle

SGT = ZoneInfo("Asia/Singapore")
NOW = datetime(2026, 10, 20, 7, 0, tzinfo=SGT)
CONFIG = load_config()


def choice(name, **kw):
    base = {"name": name, "symbol": "", "agent": "", "from_date": "", "to_date": "", "section": ""}
    return base | kw


def ask(question, choices, rows, *, known=("MU", "NVDA", "AAPL"), cut=()):
    store = FakeStore(known=set(known), rows=rows, cut=set(cut))
    answer_model = FakeModel({"reply": "done"})
    handled = handle(
        question, now=NOW, config=CONFIG, store=store,
        plan_model=FakeModel({"action_request": False, "queries": list(choices)}),
        answer_model=answer_model, memory=Memory(5, 30),
    )
    return handled, store, json.loads(answer_model.calls[0]["user"])


def stamp(day):
    return {
        "owner": f"2026-10-{day:02d}T09:00:00+08:00",
        "et": f"2026-10-{day - 1:02d}T21:00:00-04:00",
    }


def test_three_research_reports_on_nvda_this_week():
    rows = [
        {"id": f"r{n}", "agent": "research", "symbol": "NVDA", "direction": "buy",
         "generated_at": stamp(14 + n)}
        for n in range(3)
    ]
    handled, store, doc = ask(
        "what did research say about NVDA this week?",
        [choice("reports", agent="research", symbol="NVDA",
                from_date="2026-10-14", to_date="2026-10-20")],
        {"reports": rows},
    )
    (sent,) = store.run_calls[0]
    assert (sent.agent, sent.symbol) == ("research", "NVDA")
    assert (sent.from_date, sent.to_date) == (date(2026, 10, 14), date(2026, 10, 20))
    assert sent.start == datetime(2026, 10, 13, 16, tzinfo=UTC)
    assert sent.end == datetime(2026, 10, 20, 16, tzinfo=UTC)
    assert doc["results"][0]["rows"] == rows and handled.outcome.kind == "answered"


def test_a_sell_decision_on_mu_carries_its_cited_reports_ids_and_agents():
    cited = [{"id": "r1", "agent": "research", "direction": "sell"},
             {"id": "r2", "agent": "opportunistic_identifier", "direction": "sell"}]
    row = {"symbol": "MU", "direction": "sell", "cited_reports": cited,
           "verdict": "approved", "order_status": "filled"}
    _, store, doc = ask("why did we sell MU?", [choice("decisions", symbol="MU")],
                        {"decisions": [row]})
    (sent,) = store.run_calls[0]
    assert sent.symbol == "MU" and sent.agent == "" and sent.section == ""
    assert (sent.from_date, sent.to_date) == (date(2026, 10, 20), date(2026, 10, 20))
    sent_rows = doc["results"][0]["rows"]
    assert [(r["id"], r["agent"]) for r in sent_rows[0]["cited_reports"]] == [
        ("r1", "research"), ("r2", "opportunistic_identifier")]


def test_ten_journal_rows_with_attribution_reach_the_answer_document():
    rows = [
        {"trading_day": f"2026-10-{day:02d}", "equity_close": "100500.00",
         "per_agent_attribution": {"opportunistic_identifier": {"index": 101.2},
                                   "account_return_pct": 0.5}}
        for day in range(6, 16)
    ]
    _, store, doc = ask("how has the journal looked?", [choice("journal")], {"journal": rows})
    (sent,) = store.run_calls[0]
    assert (sent.to_date - sent.from_date).days + 1 == 10
    got = doc["results"][0]["rows"]
    assert len(got) == 10
    assert got[0]["per_agent_attribution"]["opportunistic_identifier"]["index"] == 101.2
    assert got[0]["per_agent_attribution"]["account_return_pct"] == 0.5


def test_a_failed_morning_run_is_in_the_answer_document():
    row = {"agent": "portfolio_manager", "trading_day": "2026-10-19", "reason": "morning",
           "outcome": "failed", "detail": "model unavailable"}
    _, store, doc = ask("did the morning run work?",
                        [choice("agent_runs", agent="portfolio_manager")], {"agent_runs": [row]})
    assert store.run_calls[0][0].agent == "portfolio_manager"
    assert doc["results"][0]["rows"] == [row]


def test_the_max_position_size_question_chooses_the_risk_settings():
    handled, store, doc = ask("what's the max position size?",
                              [choice("settings", section="risk")], {})
    assert store.run_calls == [] and doc["dropped"] == []
    (result,) = doc["results"]
    assert result["query"] == "settings" and result["parameters"] == {"section": "risk"}
    assert "max_position_pct" in result["rows"][0]["risk"]
    assert handled.outcome.queries == ("settings",)
