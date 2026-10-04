"""`--dry-run`: everything except the write (specs/007-research-agent US5; research R10;
analyze A3)."""

from __future__ import annotations

import json
import logging

import psycopg

from tests.fakes.model import FakeModel
from tests.fakes.news import FakeNews
from tests.unit.research.conftest import SECRETS
from tests.unit.research.support import SAT_0830, Clock, article, proposal
from tests.unit.research.test_main import FakeConn
from trading_agent.research import __main__ as runner

MON_CLOSE = "2026-10-05T20:00:00+00:00"
THU_CLOSE = "2026-10-01T20:00:00+00:00"


def dry(conn=None, connect_error=None, model=None, news=None, now=None):
    conn = conn if conn is not None else FakeConn()
    clock = Clock(now) if now is not None else Clock()
    printed: list[str] = []
    seen: dict = {}

    def connect(url, **options):
        seen["connect"] = options
        if connect_error is not None:
            raise connect_error
        return conn

    code = runner.main(
        ["--dry-run"],
        news_factory=lambda key: (
            news or FakeNews(general=[article("apple", related=("AAPL",), at=clock.now)])
        ),
        model_factory=lambda provider, key, cfg, **kw: (
            model
            or FakeModel(
                {"proposals": [proposal("AAPL", ids=["A1"]), proposal("ZZZZ", ids=["A1"])]}
            )
        ),
        connect=connect,
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        out=printed.append,
    )
    return code, conn, [json.loads(line) for line in printed], seen


def test_prints_rows_and_drops_and_writes_nothing(env, caplog):
    caplog.set_level(logging.INFO)
    code, conn, lines, seen = dry()
    assert code == runner.EXIT_OK and conn.written == []
    rows = [line["would_write"] for line in lines if "would_write" in line]
    drops = [line["dropped"] for line in lines if "dropped" in line]
    (summary,) = [line["summary"] for line in lines if "summary" in line]
    assert [r["symbol"] for r in rows] == ["AAPL"] and rows[0]["expires_at"] == THU_CLOSE
    assert drops == [{"index": 1, "symbol": "ZZZZ", "reason": "unlisted_symbol"}]
    assert summary["input_tokens"] == 1200 and summary["input_chars"] > 0
    assert summary["tagged_articles"] == 1
    assert summary["failure"] is None
    text = caplog.text + json.dumps(lines)
    for secret in SECRETS:
        assert secret not in text


def test_runs_without_a_database(env, monkeypatch):
    monkeypatch.delenv("RESEARCH_DATABASE_URL")
    code, conn, lines, seen = dry()
    assert code == runner.EXIT_OK and "connect" not in seen
    assert {"note": "no database: open reports not read"} in lines


def test_an_unreachable_database_is_exit_3(env):
    code, *_ = dry(connect_error=psycopg.OperationalError("refused"))
    assert code == runner.EXIT_DATABASE


def test_outside_the_window_it_still_runs_with_the_next_sessions_close(env):
    code, conn, lines, _ = dry(now=SAT_0830)
    assert code == runner.EXIT_OK
    rows = [line["would_write"] for line in lines if "would_write" in line]
    assert rows and {r["expires_at"] for r in rows} == {MON_CLOSE}


def test_a_model_failure_prints_its_category_and_is_exit_1(env):
    from trading_agent.llm.ports import ModelUnavailable

    code, conn, lines, _ = dry(model=FakeModel(error=ModelUnavailable()))
    assert code == runner.EXIT_FAILURE_RECORDED and conn.written == []
    (summary,) = [line["summary"] for line in lines if "summary" in line]
    assert summary["failure"] == "model_unavailable"


def test_after_the_close_on_a_session_day_the_expiry_is_the_next_sessions_close(env):
    from datetime import UTC, datetime

    evening = datetime(2026, 10, 1, 23, 0, tzinfo=UTC)  # Thursday, after the close
    code, conn, lines, _ = dry(now=evening)
    rows = [line["would_write"] for line in lines if "would_write" in line]
    assert code == runner.EXIT_OK
    assert {r["expires_at"] for r in rows} == {"2026-10-02T20:00:00+00:00"}


def test_prints_the_articles_sent_to_the_model(env):
    code, conn, lines, _ = dry()
    articles = [line["article"] for line in lines if "article" in line]
    assert articles == [
        {
            "id": "A1",
            "published_at": "2026-10-01T12:30:00+00:00",
            "title": "Headline apple",
            "summary": "Summary of apple.",
            "related": ["AAPL"],
        }
    ]


def test_output_order_is_articles_rows_drops_summary(env):
    _, _, lines, _ = dry()
    kinds = [next(iter(line)) for line in lines if "note" not in line]
    order = {"article": 0, "would_write": 1, "dropped": 2, "summary": 3}
    assert [order[k] for k in kinds] == sorted(order[k] for k in kinds)
    assert set(kinds) == set(order)
