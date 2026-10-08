"""`--check SYMBOL ...`: what the provider sends and whether J2 accepts it (specs/012 T019)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from tests.fakes.market_data import FakeMarketData
from trading_agent.journal.check import MAX_SYMBOLS, run_check, valid_symbols
from trading_agent.journal.config import JournalConfig
from trading_agent.reference.provider import KeyRejected, NotPermitted, RateLimited

CFG = JournalConfig(5, 20, 480, 5)
FRIDAY_EVENING = datetime(2026, 10, 9, 22, 30, tzinfo=UTC)


def utc(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def check(symbols, market, now=FRIDAY_EVENING):
    lines: list[str] = []
    sleeps: list[float] = []
    code = run_check(symbols, market, CFG, now=now, sleep=sleeps.append, out=lines.append)
    return code, [json.loads(line)["check"] for line in lines], sleeps


def test_a_close_inside_the_session_is_accepted_with_its_raw_values():
    market = FakeMarketData(quote_time=utc(9, 19, 59))
    market.add("AAPL", current="229.15")
    code, (line,), _ = check(["AAPL"], market)
    assert code == 0
    assert line == {
        "symbol": "AAPL",
        "c": "229.15",
        "t": "2026-10-09T19:59:00+00:00",
        "session_open": "2026-10-09T13:30:00+00:00",
        "session_close": "2026-10-09T20:00:00+00:00",
        "accepted": True,
        "reason": None,
    }


def test_a_quote_stamped_after_the_close_plus_grace_is_refused_with_the_reason():
    market = FakeMarketData(quote_time=utc(9, 20, 6))
    market.add("AAPL", current="229.15")
    _, (line,), _ = check(["AAPL"], market)
    assert (line["accepted"], line["reason"]) == (False, "not_today")


def test_a_symbol_with_no_price_is_refused_as_no_price():
    market = FakeMarketData(quote_time=utc(9, 19, 59))
    market.add("AAPL", current=None)
    _, (line,), _ = check(["AAPL"], market)
    assert (line["c"], line["accepted"], line["reason"]) == (None, False, "no_price")


def test_provider_errors_for_one_symbol_are_reported_and_the_rest_are_checked():
    market = FakeMarketData(quote_time=utc(9, 19, 59))
    for symbol in ("AAA", "BBB", "CCC"):
        market.add(symbol, current=10)
    market.fail("get_quote", "AAA", error=NotPermitted())
    market.fail("get_quote", "BBB", error=RateLimited())
    code, lines, sleeps = check(["AAA", "BBB", "CCC"], market)
    assert code == 0
    assert [(x["symbol"], x["accepted"], x["reason"]) for x in lines] == [
        ("AAA", False, "not_permitted"),
        ("BBB", False, "rate_limited"),
        ("CCC", True, None),
    ]
    assert sleeps == [3.0, 3.0]  # paced between calls, 20 a minute


def test_a_rejected_key_is_exit_1_and_logged_by_type(caplog):
    market = FakeMarketData()
    market.add("AAPL")
    market.fail("get_quote", error=KeyRejected("SECRET"))
    with caplog.at_level(logging.INFO, logger="trading_agent.journal"):
        code, lines, _ = check(["AAPL"], market)
    assert code == 1 and lines == []
    assert "journal: failed: market_data_key_rejected" in caplog.text
    assert "SECRET" not in caplog.text


def test_on_a_weekend_the_last_session_is_used():
    market = FakeMarketData(quote_time=utc(9, 19, 59))
    market.add("AAPL", current=10)
    _, (line,), _ = check(["AAPL"], market, now=utc(10, 15))
    assert line["session_close"] == "2026-10-09T20:00:00+00:00" and line["accepted"] is True


def test_the_symbols_are_validated():
    assert valid_symbols(["AAPL", "BRK.B", "A-B"])
    assert valid_symbols(["A"] * MAX_SYMBOLS)
    assert not valid_symbols([])
    assert not valid_symbols(["A"] * (MAX_SYMBOLS + 1))
    for bad in ("aapl", "1AB", "TOOLONGSYMBOL", "A B", "", "A;B"):
        assert not valid_symbols([bad])


def test_check_makes_one_quote_call_per_symbol():
    market = FakeMarketData(quote_time=utc(9, 19, 59))
    market.add("AAPL", current=10)
    check(["AAPL"], market)
    assert [c for _, c, _ in market.calls] == ["get_quote"]
