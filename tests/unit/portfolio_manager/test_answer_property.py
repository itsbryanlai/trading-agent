"""answer.check as properties (specs/008-portfolio-manager SC-001, SC-003, SC-008; FR-009).

Hypothesis writes arbitrary answers, valid shapes and adversarial ones, against one fixed
`Given`. Whatever the answer says, every row the checker lets through must satisfy the
rules; and every drop reason must be reachable, so the property isn't passing only
because the checker drops everything."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, find, given, settings
from hypothesis import strategies as st

from tests.unit.portfolio_manager.answer_support import (
    CAP,
    GIVEN,
    REPORT_IDS,
    SYMBOLS,
)
from trading_agent.portfolio_manager import answer

_TEXT = st.text(alphabet=st.characters(exclude_categories=()), max_size=80)
_SYMBOL = st.one_of(
    st.sampled_from(SYMBOLS), st.sampled_from(["XYZ", "aapl", "", " AAPL"]), _TEXT, st.integers()
)
_DIRECTION = st.one_of(
    st.sampled_from(["buy", "sell", "hold"]),
    st.sampled_from(["BUY", "Hold", "short", "", "cover"]),
    _TEXT,
    st.none(),
)
_TARGET = st.one_of(
    st.floats(min_value=0, max_value=100),
    st.integers(min_value=-5, max_value=105),
    st.floats(allow_nan=True, allow_infinity=True),
    st.sampled_from([0, 0.0004, 4, 100, 100.001, -1, 1e300, True, False, "4", None]),
    _TEXT,
)
_IDS = st.lists(
    st.one_of(
        st.sampled_from(REPORT_IDS),
        st.sampled_from(["R0", "R7", "db-aapl-1", "", "r1"]),
        _TEXT,
    ),
    max_size=4,
)
_ITEM = st.one_of(
    st.builds(
        lambda s, d, t, r, i: {
            "symbol": s,
            "direction": d,
            "target_weight_pct": t,
            "reasoning": r,
            "report_ids": i,
        },
        _SYMBOL,
        _DIRECTION,
        _TARGET,
        _TEXT,
        _IDS,
    ),
    st.builds(  # likelier to pass the early rules
        lambda s, d, t, r, i: {
            "symbol": s,
            "direction": d,
            "target_weight_pct": t,
            "reasoning": r,
            "report_ids": i,
        },
        st.sampled_from(SYMBOLS),
        st.sampled_from(["buy", "sell", "hold"]),
        st.floats(min_value=0, max_value=100),
        _TEXT,
        st.lists(st.sampled_from(REPORT_IDS), min_size=1, max_size=3),
    ),
    st.builds(  # a symbol with ids that belong to it: reaches the later rules
        lambda pair, d, t, r: {
            "symbol": pair[0],
            "direction": d,
            "target_weight_pct": t,
            "reasoning": r,
            "report_ids": pair[1],
        },
        st.sampled_from(
            [
                ("AAPL", ["R1"]),
                ("AAPL", ["R2", "R1"]),
                ("MSFT", ["R3"]),
                ("NVDA", ["R4"]),
                ("NVDA", ["R5"]),
                ("NVDA", ["R4", "R5"]),
                ("TSLA", ["R6"]),
            ]
        ),
        st.sampled_from(["buy", "sell", "hold"]),
        st.floats(min_value=0, max_value=20) | st.integers(min_value=0, max_value=20),
        _TEXT,
    ),
    st.dictionaries(st.text(max_size=8), st.integers() | _TEXT, max_size=5),  # wrong shape
    st.none() | st.integers() | _TEXT | st.lists(st.integers(), max_size=2),
)
ANSWERS = st.lists(_ITEM, max_size=6)

_BY_DB_ID = {ref.report_id: ref for ref in GIVEN.refs.values()}
_HUNDRED = Decimal(100)


def _check(items) -> answer.Checked:
    return answer.check(json.dumps({"decisions": items}), GIVEN)


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(ANSWERS)
def test_every_decision_written_satisfies_every_rule(items):
    checked = _check(items)
    assert not checked.unusable
    assert len(checked.decisions) + len(checked.drops) == checked.received == len(items)
    assert len({d.symbol for d in checked.decisions}) == len(checked.decisions)
    for d in checked.decisions:
        facts = GIVEN.symbols[d.symbol]  # SC-001: a symbol that was given
        assert d.quote == facts.quote and d.quote_time == facts.quote_time
        assert d.direction in answer.DIRECTIONS
        assert 0 <= d.size_pct <= _HUNDRED
        assert d.size_pct == d.size_pct.quantize(Decimal("0.001"))
        assert len(d.reasoning) <= CAP
        assert d.report_ids and len(set(d.report_ids)) == len(d.report_ids)
        cited = [_BY_DB_ID[i] for i in d.report_ids]  # every id was given...
        assert all(ref.symbol == d.symbol for ref in cited)  # ...for this symbol
        sides = {ref.direction for ref in cited}
        if d.direction == "buy":
            assert "buy" in sides  # SC-008
            assert d.size_pct > 0 and d.size_pct > facts.current_weight_pct
        if d.direction == "sell":
            assert d.size_pct < facts.current_weight_pct
        if d.direction == "hold":
            assert d.size_pct == min(facts.current_weight_pct, _HUNDRED).quantize(
                Decimal("0.001"), "ROUND_DOWN"
            )
        given_sides = {ref.direction for ref in GIVEN.refs.values() if ref.symbol == d.symbol}
        if {"buy", "sell"} <= given_sides:
            assert {"buy", "sell"} <= sides  # SC-003


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(max_size=200))
def test_arbitrary_text_never_raises(text):
    checked = answer.check(text, GIVEN)
    assert checked.decisions == ()


@pytest.mark.parametrize("reason", answer.DROP_REASONS)
def test_every_drop_reason_is_reachable(reason):
    find(
        ANSWERS,
        lambda items: any(d.reason == reason for d in _check(items).drops),
        settings=settings(max_examples=5000, deadline=None, database=None),
    )


@pytest.mark.parametrize("direction", ["buy", "sell", "hold"])
def test_every_direction_can_be_written(direction):
    find(
        ANSWERS,
        lambda items: any(d.direction == direction for d in _check(items).decisions),
        settings=settings(max_examples=5000, deadline=None, database=None),
    )
