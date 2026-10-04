"""service.run, User Story 3: the cuts and the size limit reach the model's input
(specs/008-portfolio-manager research P7; spec US3)."""

from __future__ import annotations

import logging
from datetime import timedelta

from tests.unit.portfolio_manager.builders import inputs, journal_row, report
from tests.unit.portfolio_manager.service_support import (
    Settings,
    answer_of,
    decide,
    market,
    model_input,
    run,
)
from tests.unit.portfolio_manager.support import NOW


def two_symbols():
    return inputs(
        [
            report("m", "MSFT", generated_at=NOW - timedelta(minutes=5), rationale="m" * 400),
            report("a", "AAPL", generated_at=NOW - timedelta(hours=2), rationale="a" * 400),
        ],
        journal=[journal_row("2026-09-30", "j" * 400)],
    )


QUOTES = (("AAPL", "200"), ("MSFT", "300"))


def test_rationales_and_journal_summaries_are_cut_to_the_configured_limits():
    settings = Settings(rationale_max_chars=30, journal_summary_max_chars=20)
    _, _, model, _ = run(two_symbols(), market(*QUOTES), settings=settings)
    doc = model_input(model)
    assert [len(s["reports"][0]["rationale"]) for s in doc["symbols"]] == [30, 30]
    assert len(doc["journal"][0]["summary"]) == 20


def test_over_the_input_limit_whole_symbols_go_from_the_end_and_are_logged(caplog):
    full, _, _, _ = run(two_symbols(), market(*QUOTES))
    limit = full.input_chars - 1
    with caplog.at_level(logging.INFO, logger="trading_agent.portfolio_manager"):
        outcome, store, model, _ = run(
            two_symbols(),
            market(*QUOTES),
            answer_of(decide("MSFT", "buy", 4, ["R2"]), decide("AAPL", "buy", 4, ["R1"])),
            settings=Settings(max_input_chars=limit),
        )
    doc = model_input(model)
    assert [s["symbol"] for s in doc["symbols"]] == ["MSFT"]
    assert outcome.input_chars <= limit
    assert outcome.skipped == (("AAPL", "input_limit"),)
    assert outcome.candidates == ["MSFT"]
    assert "skipped: AAPL (input_limit)" in caplog.text
    # The model was never shown AAPL, so it cannot decide it, even with a valid-looking id.
    assert [d.symbol for d in store.written] == ["MSFT"]
    assert [(d.symbol, d.reason) for d in outcome.drops] == [("AAPL", "unknown_symbol")]


def test_one_report_with_a_huge_title_and_many_sources_does_not_evict_the_others():
    flood = [
        {"title": "x" * 300_000, "publisher": "p" * 300_000, "relevance": "primary"}
        for _ in range(500)
    ]
    data = inputs(
        [
            report("m", "MSFT", generated_at=NOW - timedelta(minutes=5), sources=flood),
            report("a", "AAPL", generated_at=NOW - timedelta(hours=2)),
        ]
    )
    outcome, _, model, _ = run(data, market(*QUOTES))
    assert outcome.skipped == ()
    assert sorted(outcome.candidates) == ["AAPL", "MSFT"]
    (msft,) = [s for s in model_input(model)["symbols"] if s["symbol"] == "MSFT"]
    (r,) = msft["reports"]
    assert len(r["sources"]) == 10
    assert r["evidence"]["primary_sources"] == 500
    assert outcome.input_chars < 30_000
