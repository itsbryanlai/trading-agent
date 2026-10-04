"""A fixed `Given` for the answer checker's drop and property tests.

Report ids as the model sees them (ordered by symbol, then time, then id):
  R1, R2  AAPL  buy, buy        (not held)
  R3      MSFT  sell            (held: 15% of equity)
  R4, R5  NVDA  buy, sell       (a conflict; held: 5%)
  R6      TSLA  buy             (not held)
"""

from __future__ import annotations

import json

from tests.unit.portfolio_manager.builders import fetched, inputs, position, report
from tests.unit.portfolio_manager.support import MAX_AGE, NOW
from trading_agent.portfolio_manager import answer
from trading_agent.portfolio_manager.inputs import build_candidates

CAP = 40
SYMBOLS = ("AAPL", "MSFT", "NVDA", "TSLA")
REPORT_IDS = ("R1", "R2", "R3", "R4", "R5", "R6")


def make_given(cap: int = CAP):
    data = inputs(
        [
            report("db-aapl-1", "AAPL", "buy", size="5"),
            report("db-aapl-2", "AAPL", "buy", agent="opportunistic_identifier", size="4"),
            report("db-msft", "MSFT", "sell", size="0"),
            report("db-nvda-b", "NVDA", "buy", size="3"),
            report("db-nvda-s", "NVDA", "sell", size="1"),
            report("db-tsla", "TSLA", "buy", size="2"),
        ],
        [position("MSFT", "50"), position("NVDA", "50")],
        equity="100000",
    )
    prices = (("AAPL", "200"), ("MSFT", "300"), ("NVDA", "100"), ("TSLA", "250"))
    quotes = {s: fetched(s, p) for s, p in prices}
    built = build_candidates(data, quotes, run_start=NOW, max_age=MAX_AGE)
    return built.given(reasoning_max_chars=cap)


GIVEN = make_given()


def item(symbol="AAPL", direction="buy", target=4, reasoning="Because.", ids=("R1",)):
    return {
        "symbol": symbol,
        "direction": direction,
        "target_weight_pct": target,
        "reasoning": reasoning,
        "report_ids": list(ids),
    }


def check_items(*items, given=GIVEN):
    return answer.check(json.dumps({"decisions": list(items)}), given)


def check_text(text: str, given=GIVEN):
    return answer.check(text, given)
