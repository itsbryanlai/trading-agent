"""US1: an approved buy is placed only if live numbers still confirm it (research E6)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from tests.unit.execution.builders import (
    NOW,
    approved_buy,
    buy_live,
    quote,
    session,
)
from trading_agent.execution import reasons
from trading_agent.execution.checks import check_buy, precheck_buy
from trading_agent.execution.model import Refuse, Retry, Submit

OTHER = UUID("3f9c2a1b-ffff-4000-8000-000000000002")


def _refused(outcome, reason):
    assert isinstance(outcome, Refuse), outcome
    assert outcome.reason == reason
    return outcome.details


def test_ask_under_the_ceiling_submits_a_day_limit_buy_at_the_ask():
    outcome = check_buy(approved_buy(), buy_live(), session())
    assert isinstance(outcome, Submit)
    request = outcome.request
    assert (request.side, request.qty, request.order_type) == ("buy", 24, "limit")
    assert request.limit_price == Decimal("201.50")
    assert request.time_in_force == "day"
    assert request.client_order_id == "2026-09-28-AAPL-buy-3f9c2a1b"


def test_ask_above_the_ceiling_is_refused_and_exactly_at_it_submits():
    details = _refused(
        check_buy(approved_buy(), buy_live(ask=quote("202.01")), session()),
        reasons.QUOTE_ABOVE_CEILING,
    )
    assert details == {"ask": "202.01", "ceiling": "202", "quote_time": NOW.isoformat()}
    assert isinstance(check_buy(approved_buy(), buy_live(ask=quote("202")), session()), Submit)


def test_equity_at_the_daily_loss_line_is_refused_and_a_cent_above_passes():
    details = _refused(
        check_buy(approved_buy(), buy_live(equity="80000"), session()),
        reasons.DAILY_LOSS_LINE_CROSSED,
    )
    assert Decimal(details["line"]) == Decimal(80000)
    live = buy_live(equity="80000.01", cash="80000.01")
    assert isinstance(check_buy(approved_buy(), live, session()), Submit)


def test_a_crossing_earlier_today_blocks_buys_even_after_equity_recovers():
    # Constitution IV: new order submission halts for the rest of the day.
    live = buy_live(equity="95000", min_equity_since_open="79000")
    details = _refused(check_buy(approved_buy(), live, session()), reasons.DAILY_LOSS_LINE_CROSSED)
    assert details["min_equity_since_open"] == "79000"


def test_position_ceiling_is_refused_above_eight_percent_and_allowed_at_it():
    live = buy_live(held_qty="10", ask=quote("200"))
    details = _refused(
        check_buy(approved_buy(qty=31), live, session()), reasons.MAX_POSITION_PCT
    )  # (10 + 31) x 200 = 8,200 > 8,000
    assert details["held"] == "10" and details["qty"] == 31
    assert isinstance(check_buy(approved_buy(qty=30), live, session()), Submit)  # 8,000


def test_cash_reserve_is_refused_below_twenty_percent_and_allowed_at_it():
    live = buy_live(cash="25000", ask=quote("200"))
    _refused(check_buy(approved_buy(qty=26), live, session()), reasons.CASH_RESERVE_PCT)
    assert isinstance(check_buy(approved_buy(qty=25), live, session()), Submit)  # 20,000 left


def test_an_approval_from_an_earlier_day_or_after_the_close_has_expired():
    old = approved_buy(day=date(2026, 9, 25))
    details = _refused(check_buy(old, buy_live(), session()), reasons.APPROVAL_EXPIRED)
    assert details["trading_day"] == "2026-09-25"
    closed = session(NOW + timedelta(hours=6, minutes=1), market_open=False, after_close=True)
    _refused(check_buy(approved_buy(), buy_live(), closed), reasons.APPROVAL_EXPIRED)


def test_before_todays_open_is_a_retry_not_a_refusal():
    early = session(NOW - timedelta(hours=1), market_open=False)
    assert isinstance(check_buy(approved_buy(), buy_live(), early), Retry)


def test_pause_and_missing_baseline_refuse_and_a_bad_config_retries():
    _refused(check_buy(approved_buy(), buy_live(paused=True), session()), reasons.TRADING_PAUSED)
    live = buy_live(baseline=None)
    _refused(check_buy(approved_buy(), live, session()), reasons.NO_DAILY_BASELINE)
    assert isinstance(check_buy(approved_buy(), buy_live(config=None), session()), Retry)


@pytest.mark.parametrize(
    "ask",
    [None, quote("0"), quote("201.50", at=NOW - timedelta(seconds=61))],
    ids=["missing", "zero", "stale"],
)
def test_an_unusable_ask_is_a_retry(ask):
    assert isinstance(check_buy(approved_buy(), buy_live(ask=ask), session()), Retry)


def test_an_ask_exactly_sixty_seconds_old_is_still_live():
    live = buy_live(ask=quote("201.50", at=NOW - timedelta(seconds=60)))
    assert isinstance(check_buy(approved_buy(), live, session()), Submit)


def test_identifier_clash_is_refused():
    details = _refused(
        check_buy(approved_buy(), buy_live(), session(), clash_with=OTHER),
        reasons.IDENTIFIER_CLASH,
    )
    assert details == {
        "order_id": "2026-09-28-AAPL-buy-3f9c2a1b",
        "other_verdict_id": str(OTHER),
    }


def test_open_buy_orders_count_against_both_limits():
    # (20 held + 15 open + 6) x 200 = 8,200 > 8,000
    live = buy_live(held_qty="20", open_buy_qty_symbol="15", ask=quote("200"))
    _refused(check_buy(approved_buy(qty=6), live, session()), reasons.MAX_POSITION_PCT)
    # 100,000 - 75,000 open - 30 x 200 = 19,000 < 20,000; 6,000 is under the ceiling
    live = buy_live(open_buy_cost_all="75000", ask=quote("200"))
    _refused(check_buy(approved_buy(qty=30), live, session()), reasons.CASH_RESERVE_PCT)
    assert isinstance(check_buy(approved_buy(qty=25), live, session()), Submit)


def test_precedence_follows_the_research_table():
    live = buy_live(paused=True, baseline=None, ask=quote("300"))
    _refused(check_buy(approved_buy(), live, session()), reasons.TRADING_PAUSED)
    _refused(check_buy(approved_buy(), live, session(), clash_with=OTHER), reasons.IDENTIFIER_CLASH)
    old = approved_buy(day=date(2026, 9, 25))
    _refused(check_buy(old, live, session(), clash_with=OTHER), reasons.APPROVAL_EXPIRED)


def test_precheck_decides_rows_one_to_six_without_account_or_quote():
    unfetched = buy_live(equity=None, cash=None, ask=None)
    assert precheck_buy(approved_buy(), unfetched, session()) is None
    paused = buy_live(paused=True, equity=None, cash=None, ask=None)
    _refused(precheck_buy(approved_buy(), paused, session()), reasons.TRADING_PAUSED)


def test_a_sub_cent_ask_is_rounded_down_and_never_exceeds_the_ceiling():
    outcome = check_buy(approved_buy(), buy_live(ask=quote("201.505")), session())
    assert outcome.request.limit_price == Decimal("201.50")
    outcome = check_buy(approved_buy(), buy_live(ask=quote("201.999")), session())
    assert outcome.request.limit_price == Decimal("201.99") <= Decimal("202")


def test_details_never_carry_floats():
    for live in (buy_live(equity="80000"), buy_live(ask=quote("300")), buy_live(held_qty="40")):
        outcome = check_buy(approved_buy(), live, session())
        assert isinstance(outcome, Refuse)
        assert not any(isinstance(v, float) for v in outcome.details.values())
