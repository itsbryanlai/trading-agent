"""The summary template rendered from the day's facts (contracts/summary-template.md)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from tests.unit.journal.summary_fixtures import (
    DAY,
    NOON,
    decision,
    example_day,
    order,
    reads,
    verdict,
)
from trading_agent.journal.facts import day_facts
from trading_agent.journal.summary import SUMMARY_VERSION, render

EXAMPLE = """\
## 2026-10-09
- Equity: 100000.00 at the open, 100000.00 last recorded (19:30 UTC); change 0.00 (0.00%)
- Daily-loss breaker: not triggered
- Decisions: 3 (buy 2, sell 1, hold 0); approved 1, rejected 2
- Rejected by rule: max_position_pct 1, trading_paused 1
- Orders: 1 submitted; filled 1, partially filled 0, expired 0, rejected 0, canceled 0, open 0
- Execution refusals: none
- Stop-loss exits: 0 triggers; approved 0, rejected 0
- Decided symbols: AAPL, MSFT, NVDA
- Notes: none
"""


def _render(rows, missed=(), unpriced=0) -> str:
    return render(day_facts(rows, DAY, missed, unpriced))


def test_version():
    assert SUMMARY_VERSION == "0.1"


def test_contract_example_renders_character_for_character():
    assert _render(example_day()) == EXAMPLE


def test_scenario_three_decisions_one_filled_two_rejected():
    text = _render(example_day())
    assert "Decisions: 3" in text and "approved 1, rejected 2" in text
    assert "filled 1" in text and "max_position_pct 1, trading_paused 1" in text


def test_busy_day_keeps_the_first_lines_inside_2000_chars():
    decisions, verdicts = [], []
    for i in range(500):
        d = decision(f"S{i}", "buy")
        decisions.append(d)
        verdicts.append(verdict(d["id"], "rejected", f"rule_{'x' * (i % 40)}"[:40]))
    rows = reads(decisions=decisions, verdicts=verdicts)
    missed = tuple(date(2026, 8, 1 + i % 28) for i in range(30))
    text = _render(rows, missed, 9)
    assert len(text) <= 2000
    lines = text.splitlines()
    assert lines[1].startswith("- Equity:") and lines[2].startswith("- Daily-loss breaker")
    assert "Decisions: 500 (buy 500" in text
    assert "and 28 more" in lines[4]  # 36 distinct rules, 8 shown
    assert "and 470 more" in lines[8]  # 500 symbols, 30 shown
    assert "and 20 more" in lines[9]  # 30 missed sessions, 10 shown
    assert lines[3].startswith("- Decisions: 500") and lines[5].startswith("- Orders:")


def test_breaker_triggered_by_halt_verdict():
    d = decision("AAPL")
    rows = reads(decisions=[d], verdicts=[verdict(d["id"], "rejected", "daily_loss_halt")])
    assert "Daily-loss breaker: triggered" in _render(rows)


def test_breaker_triggered_by_line_crossed_refusal():
    rows = reads(refusals=[{"reason": "daily_loss_line_crossed", "refused_at": NOON}])
    text = _render(rows)
    assert "Daily-loss breaker: triggered" in text
    assert "Execution refusals: daily_loss_line_crossed 1" in text


def test_breaker_not_triggered_by_other_rules():
    d = decision("AAPL")
    rows = reads(
        decisions=[d],
        verdicts=[verdict(d["id"], "rejected", "trading_paused")],
        refusals=[{"reason": "trading_paused", "refused_at": NOON}],
    )
    assert "not triggered" in _render(rows)


def test_odd_rejection_rule_counts_as_other():
    ds = [decision("AAPL"), decision("MSFT"), decision("NVDA")]
    rows = reads(
        decisions=ds,
        verdicts=[
            verdict(ds[0]["id"], "rejected", "Ignore Previous; Instructions"),
            verdict(ds[1]["id"], "rejected", None),
            verdict(ds[2]["id"], "rejected", "x" * 41),
        ],
    )
    text = _render(rows)
    assert "Rejected by rule: other 3" in text
    assert "Ignore" not in text


def test_malformed_ticker_counted_not_shown():
    ds = [decision("AAPL"), decision("buy everything"), decision("lower")]
    text = _render(reads(decisions=ds))
    assert "Decided symbols: AAPL, 2 malformed" in text
    assert "everything" not in text and "lower" not in text


def test_empty_day_reads_none_and_zero():
    text = _render(reads())
    assert "Decisions: 0 (buy 0, sell 0, hold 0); approved 0, rejected 0" in text
    assert "Rejected by rule: none" in text
    assert "Execution refusals: none" in text
    assert "Decided symbols: none" in text
    assert "Orders: 0 submitted" in text
    assert "Notes: none" in text


def test_stop_loss_verdicts_count_only_on_the_stop_loss_line_and_their_orders_on_orders():
    stop_ok = verdict(None, "approved", trigger="t1")
    stop_no = verdict(None, "rejected", "trading_paused", trigger="t2")
    rows = reads(
        verdicts=[stop_ok, stop_no],
        orders=[order(stop_ok["id"])],
        triggers=[{"id": "t1", "observed_at": NOON}, {"id": "t2", "observed_at": NOON}],
    )
    text = _render(rows)
    assert "Stop-loss exits: 2 triggers; approved 1, rejected 1" in text
    assert "Decisions: 0" in text and "Rejected by rule: none" in text
    assert "Orders: 1 submitted; filled 1" in text


def test_decision_without_verdict_is_neither_approved_nor_rejected():
    text = _render(reads(decisions=[decision("AAPL")]))
    assert "Decisions: 1 (buy 1, sell 0, hold 0); approved 0, rejected 0" in text


def test_orders_count_every_status():
    a, b, c, d = (verdict(None, "approved", trigger=f"t{i}") for i in range(4))
    rows = reads(
        verdicts=[a, b, c, d],
        orders=[
            order(a["id"], "partially_filled", "2"),
            order(b["id"], "expired", "0"),
            order(c["id"], "submitted", "0"),
            order(d["id"], "canceled", "0"),
        ],
    )
    assert (
        "Orders: 4 submitted; filled 0, partially filled 1, expired 1, rejected 0, "
        "canceled 1, open 1" in _render(rows)
    )


def test_zero_equity_at_open_shows_na():
    rows = reads(
        snapshot_open={"taken_at": datetime(2026, 10, 9, 13, 30, tzinfo=UTC), "equity": Decimal(0)}
    )
    assert "change 100000.00 (n/a)" in _render(rows)


def test_equity_change_and_percentage():
    rows = reads(
        snapshot_close={
            "taken_at": datetime(2026, 10, 9, 19, 30, tzinfo=UTC),
            "equity": Decimal("98765.4321"),
        }
    )
    assert "98765.43 last recorded (19:30 UTC); change -1234.57 (-1.23%)" in _render(rows)


def test_notes_list_missed_sessions_and_unpriced():
    text = _render(reads(), (date(2026, 10, 8),), 1)
    assert "- Notes: missed sessions 2026-10-08; 1 symbol unpriced" in text
    many = tuple(date(2026, 9, 1 + i) for i in range(12))
    assert "and 2 more; 3 symbols unpriced" in _render(reads(), many, 3)


def test_other_days_decisions_are_not_counted():
    yesterday = datetime(2026, 10, 8, 16, 0, tzinfo=UTC)
    rows = reads(decisions=[decision("AAPL", at=yesterday), decision("MSFT")])
    assert "Decisions: 1 " in _render(rows)
