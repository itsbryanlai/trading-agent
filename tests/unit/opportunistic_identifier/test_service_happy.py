"""One run on fake data (specs/011 US1; research O5-O9, O11; contracts/oi-interface.md
"Logs"). No network and no model: FakeOIMarketData, FakeModel and an in-memory store."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tests.fakes.model import FakeModel
from tests.fakes.oi_market_data import FakeOIMarketData
from tests.fakes.oi_store import FakeOIStore
from tests.unit.opportunistic_identifier.support import NOW, Clock, config, messages
from trading_agent.opportunistic_identifier import rotation
from trading_agent.opportunistic_identifier.ports import (
    NotPermitted,
    ProviderUnavailable,
    RateLimited,
)
from trading_agent.opportunistic_identifier.service import OIRun
from trading_agent.risk import calendar

UNIVERSE = ["AAA", "BBB", "CCC", "ETFX", "GHST", "STAL"]
STALE = NOW - timedelta(days=1)


def proposal(symbol, **changes) -> dict:
    item = {
        "symbol": symbol,
        "direction": "buy",
        "conviction": 4,
        "suggested_size_pct": 5,
        "rationale": f"{symbol} looks cheap after its fall.",
    }
    item.update(changes)
    return item


def market(**kwargs) -> FakeOIMarketData:
    data = FakeOIMarketData(**kwargs)
    data.add("AAA", current="190")  # down 5%
    data.add("BBB", current="184")  # down 8%
    data.add("CCC", current="196")  # down 2%
    data.add("ETFX", type="ETP", mic="ARCX")
    data.add("STAL", quote_time=STALE)  # GHST is not on the list at all
    return data


def run(
    data=None,
    answer=None,
    *,
    universe=UNIVERSE,
    store=None,
    clock=None,
    dry_run=False,
    **cfg_changes,
):
    data = data if data is not None else market()
    clock = clock or Clock()
    model = FakeModel(answer if answer is not None else {"proposals": []})
    store = store or FakeOIStore()
    cfg = config(universe, **cfg_changes)
    outcome = OIRun(
        data,
        model,
        store,
        cfg,
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        dry_run=dry_run,
    ).run()
    return outcome, data, model, store, clock


def answer_for(*symbols) -> dict:
    return {"proposals": [proposal(s) for s in symbols]}


# --- the fetch ---------------------------------------------------------------------------


def test_names_failing_the_listing_check_cost_no_call_and_the_rest_are_fetched_in_order():
    _, data, model, _, _ = run()
    assert data.calls_for("ETFX") == [] and data.calls_for("GHST") == []
    assert data.calls[0] == ("us_listings", None)
    assert data.calls == [
        ("us_listings", None),
        *[
            (call, s)
            for s in ("AAA", "BBB", "CCC")
            for call in ("quote", "profile", "fundamentals")
        ],
        ("quote", "STAL"),  # stale: the other two calls are skipped
    ]
    assert len(model.calls) == 1


def test_calls_are_paced_and_the_symbol_list_counts_for_its_three_requests():
    _, _, _, _, clock = run()
    interval = 60 / 20  # finnhub_calls_per_minute in the shipped config
    # 3 names x 3 calls + 1 stale quote = 10 calls after the list; each waits for the one before.
    assert clock.slept == [3 * interval] + [interval] * 9


def test_the_pace_follows_the_configuration():
    _, _, _, _, clock = run(finnhub_calls_per_minute=60)
    assert clock.slept == [3.0] + [1.0] * 9


def test_the_model_sees_only_the_shortlist_as_one_json_document_and_the_schema():
    _, _, model, _, _ = run()
    (call,) = model.calls
    doc = json.loads(call["user"])
    assert [n["symbol"] for n in doc["names"]] == ["BBB", "AAA", "CCC"]  # largest fall first
    assert doc["trading_day"] == "2026-10-08" and set(doc) == {"now", "trading_day", "names"}
    assert call["schema"]["required"] == ["proposals"]
    assert "ETFX" not in call["user"] and "STAL" not in call["user"]


def test_a_stale_name_and_an_unlisted_one_are_skipped_not_failed():
    outcome, *_ = run()
    assert {(s.symbol, s.reason) for s in outcome.skips} == {
        ("ETFX", "universe_listing"),
        ("GHST", "not_listed"),
        ("STAL", "stale_quote"),
    }


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ProviderUnavailable(), "provider_unavailable"),
        (NotPermitted(), "not_permitted"),
        (RateLimited(), "rate_limited"),
    ],
)
def test_one_names_fetch_failing_is_a_skip_and_the_run_carries_on(error, reason):
    data = market()
    data.fail("profile", "BBB", error=error)
    outcome, _, model, store, _ = run(data, answer_for("AAA"))
    assert outcome.counts.skipped[reason] == 1 and outcome.failure is None
    assert [r.symbol for r in store.rows] == ["AAA"]
    assert "BBB" not in model.calls[0]["user"]


# --- the rows ------------------------------------------------------------------------------


def test_the_rows_written_equal_the_valid_proposals_in_one_batch():
    answer = {
        "proposals": [
            proposal("BBB", conviction=5, suggested_size_pct=7.5),
            proposal("AAA", suggested_size_pct=3),
            proposal("ETFX"),  # skipped before the model: not shortlisted
        ]
    }
    outcome, _, _, store, _ = run(answer=answer)
    assert len(store.writes) == 1
    assert [(r.symbol, r.direction, r.conviction) for r in store.rows] == [
        ("BBB", "buy", 5),
        ("AAA", "buy", 4),
    ]
    assert [r.suggested_size_pct for r in store.rows] == [Decimal("7.500"), Decimal("3.000")]
    assert all(len(r.sources) == 3 for r in store.rows)
    assert all(r.expires_at == calendar.close_time(date(2026, 10, 8)) for r in store.rows)
    assert outcome.rows == store.rows and outcome.failure is None


def test_run_counts_are_right():
    outcome, *_ = run(
        answer={"proposals": [proposal("AAA"), proposal("ZZZ")]}, store=FakeOIStore({"CCC"})
    )
    c = outcome.counts
    assert (c.in_slice, c.fetched, c.already_open, c.eligible, c.shortlisted) == (6, 4, 1, 2, 2)
    assert c.skipped == {"not_listed": 1, "stale_quote": 1, "universe_listing": 1}
    assert (c.proposed, c.written, c.dropped) == (2, 1, {"malformed_answer": 1})
    assert (c.input_tokens, c.output_tokens) == (1200, 300)


def test_names_with_an_open_report_are_fetched_but_never_shortlisted():
    outcome, _, model, _, _ = run(store=FakeOIStore({"AAA"}))
    assert [n["symbol"] for n in json.loads(model.calls[0]["user"])["names"]] == ["BBB", "CCC"]
    assert outcome.counts.already_open == 1


def test_log_lines_follow_the_contract_with_token_counts(caplog):
    with caplog.at_level(logging.INFO, logger="trading_agent.opportunistic_identifier"):
        run(answer=answer_for("AAA", "BBB"))
    assert messages(caplog) == [
        "opportunistic_identifier: run started (prompt v0.1, provider qwen, model qwen3.7-plus, "
        "batch 1/1)",
        "opportunistic_identifier: 6 in slice, 4 fetched, 3 skipped (not_listed: 1, "
        "stale_quote: 1, universe_listing: 1), 0 already open",
        "opportunistic_identifier: 3 eligible, 3 shortlisted",
        "opportunistic_identifier: model used 1200 input and 300 output tokens",
        "opportunistic_identifier: 2 proposals received, 2 accepted, 0 dropped",
        "opportunistic_identifier: wrote 2 report(s)",
    ]


def test_logs_never_hold_the_prompt_or_the_answer(caplog):
    with caplog.at_level(logging.DEBUG):
        run(answer=answer_for("AAA"))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "looks cheap" not in text and "ACME CORP" not in text


# --- quiet no_action rows ------------------------------------------------------------------


def test_a_model_that_proposes_nothing_gives_one_no_action_naming_it_and_the_counts():
    outcome, _, _, store, _ = run()
    (row,) = store.rows
    assert (row.symbol, row.direction, row.conviction, row.suggested_size_pct, row.sources) == (
        None,
        "no_action",
        None,
        None,
        [],
    )
    assert row.rationale_md.startswith("Opportunistic Identifier run: nothing_argued.")
    assert "6 in slice, 4 fetched" in row.rationale_md
    assert "skipped (not_listed: 1, stale_quote: 1, universe_listing: 1)" in row.rationale_md
    assert "3 eligible, 3 shortlisted" in row.rationale_md
    assert (outcome.note, outcome.failure) == ("nothing_argued", None)
    assert row.expires_at == calendar.close_time(date(2026, 10, 8))


def test_every_proposal_dropped_gives_all_dropped(caplog):
    with caplog.at_level(logging.INFO, logger="trading_agent.opportunistic_identifier"):
        outcome, _, _, store, _ = run(answer=answer_for("ZZZ"))
    (row,) = store.rows
    assert outcome.note == "all_dropped" and row.direction == "no_action"
    assert "dropped (malformed_answer: 1)" in row.rationale_md
    assert "opportunistic_identifier: dropped proposal 0 (-): malformed_answer" in messages(caplog)


def test_an_empty_scan_list_makes_no_fetch_and_no_model_call():
    outcome, data, model, store, _ = run(universe=[])
    assert data.calls == [] and model.calls == []
    (row,) = store.rows
    assert outcome.note == "empty_scan_universe" and "The scan list is empty." in row.rationale_md


def test_an_all_skipped_slice_makes_no_model_call():
    data = FakeOIMarketData()
    data.add("STAL", quote_time=STALE)
    outcome, data, model, store, _ = run(data, universe=["STAL", "GHST"])
    assert model.calls == [] and data.calls == [("us_listings", None), ("quote", "STAL")]
    (row,) = store.rows
    assert outcome.note == "empty_shortlist" and row.direction == "no_action"


def test_a_document_over_max_input_chars_fails_with_input_too_large():
    outcome, _, model, store, _ = run(max_input_chars=500)
    assert model.calls == [] and outcome.failure == "input_too_large"
    (row,) = store.rows
    assert row.rationale_md.startswith("Opportunistic Identifier run failed: input_too_large.")


# --- the deadline ---------------------------------------------------------------------------------


class TimedMarket(FakeOIMarketData):
    """Records the monotonic time of every call, through the shared clock."""

    clock: Clock

    def _record(self, call, symbol):
        self.times.append(self.clock.monotonic())
        super()._record(call, symbol)


def test_no_call_starts_after_the_fetch_deadline_and_the_model_still_runs():
    clock = Clock()
    data = TimedMarket()
    data.clock, data.times = clock, []
    for n, symbol in enumerate("ABCDEFGHIJ"):
        data.add(symbol, current=str(190 - n))
    # One call a minute: the list at 0 s, then a call every 60 s from 180 s. The Qwen window
    # is 650 s, so the call at 660 s (C's fundamentals) is the first that may not start.
    outcome, _, model, store, _ = run(
        data,
        answer_for("A"),
        universe=list("ABCDEFGHIJ"),
        clock=clock,
        finnhub_calls_per_minute=1,
    )
    deadline = config().fetch_deadline(0.0)
    assert deadline == 650 and data.times and max(data.times) < deadline
    assert len(data.times) == 9  # the list, A's three, B's three, C's quote and profile
    assert outcome.counts.skipped["not_fetched"] == 8  # C's last call and D..J
    assert len(model.calls) == 1 and [r.symbol for r in store.rows] == ["A"]


# --- a dry run ------------------------------------------------------------------------------------


def test_a_dry_run_on_a_closed_day_uses_the_next_sessions_first_slot_and_writes_nothing():
    saturday = datetime(2026, 10, 10, 16, 0, tzinfo=UTC)
    data = market(quote_time=saturday - timedelta(minutes=5))
    outcome, _, _, store, _ = run(data, answer_for("AAA"), clock=Clock(saturday), dry_run=True)
    monday = date(2026, 10, 12)
    assert store.writes == []
    assert [r.symbol for r in outcome.rows] == ["AAA"]
    assert outcome.rows[0].expires_at == calendar.close_time(monday)
    expected = rotation.slice_for(
        UNIVERSE, monday, datetime(2026, 10, 12, 14, 0, tzinfo=UTC), config().slots, 40
    )
    assert outcome.slice == expected
