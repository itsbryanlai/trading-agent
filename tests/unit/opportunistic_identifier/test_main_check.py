"""`--check SYMBOL...`: what the provider sends for a few names, with no model and no
database (specs/011 US4; quickstart step 2)."""

from __future__ import annotations

import json

import pytest

from tests.fakes.oi_market_data import FakeOIMarketData
from tests.unit.opportunistic_identifier.conftest import SECRETS
from tests.unit.opportunistic_identifier.test_main import run
from trading_agent.opportunistic_identifier import __main__ as runner
from trading_agent.opportunistic_identifier.ports import KeyRejected, ProviderUnavailable

KEYS = ("10DayAverageTradingVolume", "52WeekHigh", "pbQuarterly", "peTTM")  # sorted, as sent


def market() -> FakeOIMarketData:
    data = FakeOIMarketData()
    data.add("AAA", current="190", received_keys=KEYS)
    data.add("ETFX", type="ETP", mic="ARCX")
    data.add("STAL", quote_time=None)
    data.add("NOHI", high_52w=None, received_keys=("10DayAverageTradingVolume",))
    return data


@pytest.fixture
def no_other_variables(env, monkeypatch):
    for name in (
        "OPPORTUNISTIC_IDENTIFIER_DATABASE_URL",
        "OPPORTUNISTIC_IDENTIFIER_DASHSCOPE_API_KEY",
        "OPPORTUNISTIC_IDENTIFIER_QWEN_BASE_URL",
    ):
        monkeypatch.delenv(name)


def check(config_path, *symbols, data=None, **kw):
    printed: list[str] = []
    code, conn, seen = run(
        ["--check", *symbols],
        config_path=config_path,
        market=data if data is not None else market(),
        out=printed.append,
        **kw,
    )
    return code, seen, printed, [json.loads(line)["check"] for line in printed]


def test_check_prints_per_symbol_the_raw_keys_the_derived_values_and_the_verdict(
    no_other_variables, config_path
):
    code, _, _, (line,) = check(config_path, "AAA")
    assert code == runner.EXIT_OK
    assert line["symbol"] == "AAA" and line["result"] == "eligible"
    assert line["metric_keys_received"] == list(KEYS)
    assert line["listing"] == {"type": "Common Stock", "mic": "XNGS"}
    assert line["quote"]["c"] == 190 and line["quote"]["pc"] == 200 and line["quote"]["t"]
    assert line["profile"] == {"market_cap_millions": 3000000, "currency": "USD"}
    assert line["fundamentals"]["high_52w"] == 250 and line["fundamentals"]["pe_ttm"] == 20
    derived = line["derived"]
    assert derived["move_today_pct"] == -5 and derived["below_52w_high_pct"] == 24
    assert derived["market_cap_usd"] == 3_000_000_000_000
    assert derived["avg_daily_dollar_volume_usd"] == 5_000_000_000
    assert derived["share_price_usd"] == 200


def test_each_symbol_gets_one_line_in_the_order_asked(no_other_variables, config_path):
    _, _, _, lines = check(config_path, "NOHI", "AAA")
    assert [line["symbol"] for line in lines] == ["NOHI", "AAA"]


def test_a_name_failing_the_listing_check_shows_its_reason_and_costs_no_call(
    no_other_variables, config_path
):
    data = market()
    _, _, _, (line,) = check(config_path, "ETFX", data=data)
    assert line["result"] == "universe_listing" and "quote" not in line
    assert data.calls_for("ETFX") == []


def test_a_symbol_not_on_the_list_is_not_listed(no_other_variables, config_path):
    _, _, _, (line,) = check(config_path, "GHST")
    assert line["result"] == "not_listed"


def test_a_stale_quote_stops_after_the_one_call_and_says_so(no_other_variables, config_path):
    data = market()
    _, _, _, (line,) = check(config_path, "STAL", data=data)
    assert line["result"] == "stale_quote" and "profile" not in line
    assert data.calls_for("STAL") == ["quote"]


def test_a_skipped_name_still_shows_its_values_so_the_owner_can_see_why(
    no_other_variables, config_path
):
    _, _, _, (line,) = check(config_path, "NOHI")
    assert line["result"] == "missing_52_week_high"
    assert line["fundamentals"]["high_52w"] is None and line["quote"]["c"] == 201


def test_a_per_name_provider_error_is_that_names_result(no_other_variables, config_path):
    data = market()
    data.fail("profile", "AAA", error=ProviderUnavailable("SECRET-text"))
    code, _, printed, (line,) = check(config_path, "AAA", data=data)
    assert code == 0 and line["result"] == "provider_unavailable"
    assert "SECRET-text" not in "\n".join(printed)


def test_a_rejected_key_fails_the_check_without_echoing_anything(
    no_other_variables, config_path, caplog
):
    data = market()
    data.fail("quote", error=KeyRejected("SECRET-text"))
    code, _, printed, lines = check(config_path, "AAA", data=data)
    assert code == runner.EXIT_FAILURE_RECORDED and lines == []
    assert "SECRET-text" not in caplog.text and "KeyRejected" in caplog.text


def test_no_model_client_and_no_database_are_needed(no_other_variables, config_path):
    code, seen, _, _ = check(config_path, "AAA")
    assert code == 0 and "model" not in seen and "connect" not in seen


def test_the_finnhub_key_is_still_required(no_other_variables, config_path, monkeypatch, caplog):
    monkeypatch.delenv("OPPORTUNISTIC_IDENTIFIER_FINNHUB_API_KEY")
    code, *_ = check(config_path, "AAA")
    assert code == runner.EXIT_REFUSED
    assert "OPPORTUNISTIC_IDENTIFIER_FINNHUB_API_KEY" in caplog.text


def test_calls_are_paced_at_the_configured_rate(no_other_variables, config_path):
    from tests.unit.opportunistic_identifier.support import Clock

    clock = Clock()
    check(config_path, "AAA", clock=clock)
    # The list's three requests, then quote, profile and fundamentals, each waiting its turn.
    assert clock.slept == [9.0, 3.0, 3.0]


def test_ten_symbols_are_allowed(no_other_variables, config_path):
    data = FakeOIMarketData()
    names = [f"A{chr(65 + n)}" for n in range(10)]
    for name in names:
        data.add(name)
    code, _, _, lines = check(config_path, *names, data=data)
    assert code == 0 and len(lines) == 10


@pytest.mark.parametrize(
    "symbols",
    [
        [],
        [f"S{chr(65 + n)}" for n in range(11)],
        ["aapl"],
        ["AAPL", "TOOLONGX"],
        ["BRK/B"],
        [""],
        ["AAPL", "AAPL "],
    ],
    ids=["none", "eleven", "lowercase", "too-long", "slash", "empty", "space"],
)
def test_no_symbols_too_many_or_an_implausible_ticker_is_exit_2(
    no_other_variables, config_path, symbols
):
    code, seen, printed, _ = check(config_path, *symbols)
    assert code == runner.EXIT_REFUSED and printed == [] and "market_key" not in seen


def test_nothing_printed_holds_a_key_or_a_header(no_other_variables, config_path):
    _, _, printed, _ = check(config_path, "AAA", "ETFX", "STAL", "NOHI")
    text = "\n".join(printed)
    for secret in SECRETS:
        assert secret not in text
    assert "X-Finnhub-Token" not in text and "token" not in text.lower()


def test_values_are_json_numbers_not_decimal_strings(no_other_variables, config_path):
    _, _, _, (line,) = check(config_path, "AAA")
    for value in (
        line["quote"]["c"],
        line["fundamentals"]["pe_ttm"],
        line["derived"]["market_cap_usd"],
    ):
        assert isinstance(value, int | float) and not isinstance(value, bool)
