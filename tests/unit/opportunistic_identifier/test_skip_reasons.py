"""The closed set of per-name skip reasons: the code's set and the contract's list agree
(contracts/oi-interface.md "Closed sets"), and every reason the screen gives is in it."""

from __future__ import annotations

import re

import pytest

from tests.unit.opportunistic_identifier.support import ROOT
from trading_agent.opportunistic_identifier import outcome, screen

CONTRACT = ROOT / "specs" / "011-opportunistic-identifier" / "contracts" / "oi-interface.md"


def contract_skip_reasons() -> set[str]:
    text = CONTRACT.read_text()
    block = text.split("**Skip reasons**")[1].split("`already_open` is counted separately")[0]
    return {name for name in re.findall(r"`([a-z0-9_]+)`", block)}


def test_every_reason_the_contract_names_is_in_the_code_set():
    assert contract_skip_reasons() <= outcome.SKIP_REASONS


def test_the_screens_own_reasons_are_in_the_code_set_and_the_contract():
    own = {
        screen.STALE_QUOTE,
        screen.MISSING_PRICE,
        screen.MISSING_52_WEEK_HIGH,
        screen.INCONSISTENT_52_WEEK_RANGE,
        screen.MISSING_FUNDAMENTALS,
        screen.IMPLAUSIBLE_MOVE,
    }
    assert own <= outcome.SKIP_REASONS
    assert own <= contract_skip_reasons()


def test_the_new_reason_is_named_exactly():
    assert screen.INCONSISTENT_52_WEEK_RANGE == "inconsistent_52_week_range"
    assert "inconsistent_52_week_range" in contract_skip_reasons()


@pytest.mark.parametrize("name", ["already_open", "nothing_argued", "malformed_answer"])
def test_other_closed_sets_are_not_mixed_in(name):
    assert name not in outcome.SKIP_REASONS
