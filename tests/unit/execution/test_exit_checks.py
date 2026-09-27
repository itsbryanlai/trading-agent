"""US2: approved sells and stop-loss exits go out as market orders (research E6)."""

from __future__ import annotations

import inspect
from datetime import date, timedelta
from uuid import UUID

from tests.unit.execution.builders import NOW, approved_sell, exit_live, session
from trading_agent.execution import reasons
from trading_agent.execution.checks import check_exit
from trading_agent.execution.model import Refuse, Retry, Submit


def test_a_held_position_is_sold_with_a_day_market_order():
    outcome = check_exit(approved_sell(qty=50), exit_live(held=50), session())
    assert isinstance(outcome, Submit)
    request = outcome.request
    assert (request.side, request.qty, request.order_type) == ("sell", 50, "market")
    assert request.limit_price is None and request.time_in_force == "day"
    assert request.client_order_id == "2026-09-28-AAPL-sell-3f9c2a1b"


def test_fewer_shares_held_than_approved_is_refused():
    outcome = check_exit(approved_sell(qty=50), exit_live(held=30), session())
    assert isinstance(outcome, Refuse) and outcome.reason == reasons.SHARES_HELD_DIFFER
    assert outcome.details == {"held": "30", "open_sell_qty": "0", "qty": 50}


def test_shares_already_in_an_open_sell_are_not_available():
    exit_ = approved_sell(qty=50, source="stop_loss")
    outcome = check_exit(exit_, exit_live(held=50, open_sell=50), session())
    assert isinstance(outcome, Refuse) and outcome.reason == reasons.SHARES_HELD_DIFFER
    assert isinstance(check_exit(exit_, exit_live(held=80, open_sell=30), session()), Submit)


def test_an_earlier_days_approval_has_expired():
    old = approved_sell(day=date(2026, 9, 25))
    outcome = check_exit(old, exit_live(), session())
    assert isinstance(outcome, Refuse) and outcome.reason == reasons.APPROVAL_EXPIRED


def test_before_the_open_is_a_retry():
    early = session(NOW - timedelta(hours=1), market_open=False)
    assert isinstance(check_exit(approved_sell(), exit_live(), early), Retry)


def test_identifier_clash_is_refused():
    other = UUID("3f9c2a1b-ffff-4000-8000-000000000002")
    outcome = check_exit(approved_sell(), exit_live(), session(), clash_with=other)
    assert isinstance(outcome, Refuse) and outcome.reason == reasons.IDENTIFIER_CLASH


def test_nothing_about_equity_pause_baseline_or_config_can_reach_an_exit():
    # FR-006, FR-018, FR-020: the function doesn't even take those inputs.
    params = set(inspect.signature(check_exit).parameters)
    assert params == {"approval", "live", "session", "clash_with"}
    live_fields = set(exit_live().__dataclass_fields__)
    assert live_fields == {"held_qty", "open_sell_qty_symbol"}
