"""`python -m trading_agent.research`: startup, exit codes, and never printing a
variable's value (specs/007-research-agent research R9; FR-017, FR-018)."""

from __future__ import annotations

import logging

import psycopg
import pytest

from tests.fakes.model import FakeModel
from tests.fakes.news import FakeNews
from tests.unit.research.conftest import FAKE_DASHSCOPE, FAKE_FINNHUB, SECRETS
from tests.unit.research.support import Clock, article, proposal
from trading_agent.research import __main__ as runner


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, statement, rows):
        if self.conn.fail_write:
            raise psycopg.OperationalError("lost")
        self.conn.written.extend(rows)


class FakeTransaction:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeConn:
    autocommit = True

    def __init__(self, open_rows=(), fail_read=False, fail_write=False):
        self.open_rows = list(open_rows)
        self.fail_read = fail_read
        self.fail_write = fail_write
        self.written: list = []
        self.closed = False

    def execute(self, statement, params=None):
        if self.fail_read:
            raise psycopg.OperationalError("lost")
        return FakeResult(self.open_rows)

    def transaction(self):
        return FakeTransaction()

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def run(args=(), *, conn=None, connect_error=None, news=None, model=None, seen=None, **kw):
    conn = conn if conn is not None else FakeConn()
    clock = Clock()
    printed: list[str] = []
    seen = seen if seen is not None else {}

    def connect(url, **options):
        seen["connect"] = (url, options)
        if connect_error is not None:
            raise connect_error
        return conn

    def news_factory(key):
        seen["news_key"] = key
        return news or FakeNews(general=[article("apple", related=("AAPL",))])

    def model_factory(provider, key, model_cfg, *, base_url=None):
        seen["model"] = (provider, key, model_cfg.name)
        seen["base_url"] = base_url
        return model or FakeModel({"proposals": [proposal("AAPL", ids=["A1"])]})

    code = runner.main(
        list(args),
        news_factory=news_factory,
        model_factory=model_factory,
        connect=connect,
        clock=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        out=printed.append,
        **kw,
    )
    return code, conn, printed, seen


def _no_secret_in(caplog, printed):
    text = "\n".join([r.getMessage() for r in caplog.records] + printed)
    for secret in SECRETS:
        assert secret not in text


def test_happy_path(env, caplog):
    caplog.set_level(logging.INFO)
    code, conn, printed, seen = run()
    assert code == runner.EXIT_OK
    assert len(conn.written) == 1 and conn.closed
    assert seen["connect"][1]["autocommit"] is True
    assert seen["connect"][1]["connect_timeout"] == 10
    assert seen["news_key"] == FAKE_FINNHUB
    assert seen["model"] == ("qwen", FAKE_DASHSCOPE, "qwen3.7-plus")
    _no_secret_in(caplog, printed)


@pytest.mark.parametrize(
    "missing", ["RESEARCH_DATABASE_URL", "RESEARCH_FINNHUB_API_KEY", "RESEARCH_DASHSCOPE_API_KEY"]
)
def test_a_missing_variable_refuses_to_start(env, monkeypatch, caplog, missing):
    monkeypatch.delenv(missing)
    code, conn, printed, seen = run()
    assert code == runner.EXIT_REFUSED
    assert missing in caplog.text and "connect" not in seen
    _no_secret_in(caplog, printed)


def test_a_bad_config_refuses_to_start(env, tmp_path, caplog):
    path = tmp_path / "research.yaml"
    path.write_text("watchlist: []\n")
    code, *_ = run(config_path=path)
    assert code == runner.EXIT_REFUSED


def test_an_unreachable_database_is_exit_3(env, caplog):
    code, conn, printed, seen = run(connect_error=psycopg.OperationalError("refused"))
    assert code == runner.EXIT_DATABASE
    assert "OperationalError" in caplog.text
    _no_secret_in(caplog, printed)


@pytest.mark.parametrize("args", [["--now"], ["--dry-run", "extra"], ["x"]])
def test_unknown_arguments_refuse_to_start(env, args):
    assert run(args)[0] == runner.EXIT_REFUSED
