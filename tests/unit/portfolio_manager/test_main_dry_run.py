"""`--dry-run` (specs/008-portfolio-manager contracts/pm-interface.md): everything but the
write, no market-hours check, a JSON line per fact."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from tests.unit.portfolio_manager.builders import report
from tests.unit.portfolio_manager.main_support import run_main
from tests.unit.portfolio_manager.service_support import Clock, answer_of, decide, market
from tests.unit.portfolio_manager.support import NOW

AFTER_CLOSE = datetime(2026, 10, 1, 20, 30, tzinfo=UTC)  # 16:30 ET


def lines(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_it_prints_each_kind_of_line_and_writes_nothing(env, capsys):
    answer = answer_of(decide(), decide(symbol="ZZZZ", ids=("R1",)))
    result = run_main(["--dry-run"], answer=answer)

    out = lines(capsys)
    assert result.code == 0 and result.store.writes == []
    assert [line["type"] for line in out] == ["candidate", "would_write", "dropped", "summary"]
    candidate, would_write, dropped, summary = out
    assert candidate["symbol"] == "AAPL" and float(candidate["quote"]) == 200
    assert candidate["current_weight_pct"] and candidate["quote_time"]
    assert candidate["report_ids"] == ["db-aapl"]
    assert (would_write["symbol"], would_write["direction"]) == ("AAPL", "buy")
    assert float(would_write["size_pct"]) == 4 and would_write["report_ids"] == ["db-aapl"]
    assert (dropped["index"], dropped["reason"]) == (1, "unknown_symbol")
    assert summary["would_write"] == 1 and summary["dropped"] == 1 and summary["failure"] is None
    assert summary["input_chars"] > 0 and "input_tokens" in summary


def test_a_symbol_without_a_quote_prints_as_skipped_with_its_reason(env, capsys):
    old = market(("AAPL", "200"), at=NOW - timedelta(minutes=30))
    result = run_main(["--dry-run"], quotes=old)
    out = lines(capsys)
    assert result.code == 1  # no candidate left: the same failure a real run reports
    assert {"type": "skipped", "symbol": "AAPL", "reason": "quote_stale"} in out
    assert out[-1]["type"] == "summary" and out[-1]["failure"] == "no_fresh_quotes"


def test_it_skips_the_market_hours_check(env, capsys):
    late = Clock(AFTER_CLOSE)
    held_open = [report("db-aapl", "AAPL", expires_at=AFTER_CLOSE + timedelta(hours=4))]
    quotes = market(("AAPL", "200"), at=AFTER_CLOSE - timedelta(minutes=1))
    dry = run_main(["--dry-run"], clock=late, reports=held_open, quotes=quotes)
    assert dry.code == 0 and dry.model.calls
    assert any(line["type"] == "would_write" for line in lines(capsys))

    real = run_main([], clock=Clock(AFTER_CLOSE), reports=held_open, quotes=quotes)
    assert real.code == 0 and not real.model.calls and real.store.writes == []


def test_a_real_run_prints_nothing(env, capsys):
    assert run_main([]).code == 0
    assert capsys.readouterr().out == ""


def test_it_still_needs_the_database_url(env, monkeypatch, capsys):
    monkeypatch.delenv("PORTFOLIO_MANAGER_DATABASE_URL")
    result = run_main(["--dry-run"])
    assert result.code == 2 and "conn" not in result.seen and capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "args", [["--dryrun"], ["--dry-run", "--dry-run"], ["--dry-run", "x"], ["x"]]
)
def test_any_other_argument_exits_two(env, capsys, args):
    result = run_main(args)
    assert result.code == 2 and "conn" not in result.seen and capsys.readouterr().out == ""
