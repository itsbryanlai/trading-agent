"""`python -m trading_agent.journal`: exit codes, variables and logs (specs/012 T016; contract
journal-interface.md; research J12). Fakes only: no database, no network."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from tests.fakes.journal_store import FakeJournalStore, empty_reads
from tests.fakes.market_data import FakeMarketData
from trading_agent.journal import __main__ as runner
from trading_agent.journal.config import DEFAULT_CONFIG_PATH
from trading_agent.reference.provider import KeyRejected

DATABASE = "postgresql://fake-user:fake-password-not-real@127.0.0.1:1/none"
KEY = "fake-not-real-key"
NOW = datetime(2026, 10, 9, 22, 30, tzinfo=UTC)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("JOURNAL_DATABASE_URL", DATABASE)
    monkeypatch.setenv("JOURNAL_FINNHUB_API_KEY", KEY)


class Conn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def snapshots():
    from decimal import Decimal

    return {
        "snapshot_open": {
            "taken_at": datetime(2026, 10, 9, 13, 0, tzinfo=UTC),
            "equity": Decimal(1),
        },
        "snapshot_close": {
            "taken_at": datetime(2026, 10, 9, 19, 0, tzinfo=UTC),
            "equity": Decimal(1),
        },
    }


def go(argv=(), *, store=None, market=None, connect=None, config_path=DEFAULT_CONFIG_PATH, now=NOW):
    store = store or FakeJournalStore(empty_reads(**snapshots()))
    conn = Conn()
    lines: list[str] = []
    code = runner.main(
        list(argv),
        market_factory=lambda key: market or FakeMarketData(),
        connect=connect or (lambda url, **kw: conn),
        store_factory=lambda c: store,
        config_path=config_path,
        clock=lambda: now,
        sleep=lambda s: None,
        monotonic=lambda: 0.0,
        out=lines.append,
    )
    return code, store, conn, lines


def critical(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.CRITICAL]


def test_a_run_that_writes_is_exit_0_and_closes_the_connection(env):
    code, store, conn, _ = go()
    assert code == runner.EXIT_OK == 0
    assert len(store.upserts) == 1 and conn.closed


@pytest.mark.parametrize(
    "now", [datetime(2026, 10, 10, 22, 30, tzinfo=UTC), datetime(2026, 10, 9, 19, 0, tzinfo=UTC)]
)
def test_nothing_to_do_is_exit_0(env, now):
    code, store, _, _ = go(now=now)
    assert code == 0 and store.upserts == []


def test_a_named_failure_is_exit_1_and_writes_nothing(env, caplog):
    store = FakeJournalStore(empty_reads())  # no snapshot
    with caplog.at_level(logging.INFO, logger="trading_agent.journal"):
        code, store, conn, _ = go(store=store)
    assert code == runner.EXIT_FAILURE == 1
    assert store.upserts == [] and conn.closed
    assert "journal: failed: no_account_snapshot" in caplog.text


@pytest.mark.parametrize("missing", ["JOURNAL_DATABASE_URL", "JOURNAL_FINNHUB_API_KEY"])
def test_a_missing_variable_is_exit_2_and_named(env, monkeypatch, caplog, missing):
    monkeypatch.delenv(missing)
    code, *_ = go()
    assert code == runner.EXIT_REFUSED == 2
    assert missing in critical(caplog)[0]


@pytest.mark.parametrize(
    "argv",
    [
        ["--nope"],
        ["extra"],
        ["--dry-run", "--dry-run"],
        ["--check"],
        ["--check", "aapl"],
        ["--replace", "--dry-run"],
        ["--replace", "--check", "AAPL"],
        ["--replace", "x"],
    ],
)
def test_bad_arguments_are_exit_2(env, argv):
    code, store, conn, _ = go(argv)
    assert code == 2 and store.upserts == []


def test_a_bad_config_is_exit_2(env, tmp_path: Path, caplog):
    path = tmp_path / "journal.yaml"
    path.write_text("holding_sessions: 5\n")
    code, *_ = go(config_path=path)
    assert code == 2
    assert "close_grace_minutes" in critical(caplog)[0]


def test_the_database_being_unreachable_is_exit_3(env, caplog):
    def refuse(url, **kw):
        raise psycopg.OperationalError(DATABASE)

    code, *_ = go(connect=refuse)
    assert code == runner.EXIT_DATABASE == 3
    assert critical(caplog) == ["journal: database unreachable: OperationalError"]


def test_a_database_error_during_the_run_is_exit_3(env, caplog):
    class Broken(FakeJournalStore):
        def read(self, *a):
            raise psycopg.errors.InsufficientPrivilege(DATABASE)

    code, _, conn, _ = go(store=Broken())
    assert code == 3 and conn.closed
    assert critical(caplog) == ["journal: database error: InsufficientPrivilege"]
    assert DATABASE not in caplog.text


def test_any_other_exception_is_exit_4_by_type_only(env, caplog):
    class Crashing(FakeJournalStore):
        def read(self, *a):
            raise RuntimeError("SECRET-text-do-not-log")

    code, _, conn, _ = go(store=Crashing())
    assert code == runner.EXIT_CRASHED == 4 and conn.closed
    assert critical(caplog) == ["journal: crashed: RuntimeError"]
    assert "SECRET" not in caplog.text


def test_no_variable_value_appears_in_any_log_record(env, caplog):
    with caplog.at_level(logging.DEBUG):
        go()
        go(store=FakeJournalStore(empty_reads()))
    assert caplog.records
    assert DATABASE not in caplog.text and KEY not in caplog.text
    assert "fake-password-not-real" not in caplog.text


# --- --dry-run (T018) ----------------------------------------------------------------------


def test_a_dry_run_prints_the_would_be_row_as_json_lines_and_writes_nothing(env):
    import json

    code, store, conn, lines = go(["--dry-run"])
    assert code == 0 and store.upserts == [] and conn.closed
    parsed = [json.loads(line) for line in lines]
    assert parsed[0] == {"trading_day": "2026-10-09", "equity_open": "1", "equity_close": "1"}
    assert parsed[1]["summary_md"].startswith("## 2026-10-09\n")
    assert parsed[2]["per_agent_attribution"]["schema_version"] == 1


def test_a_dry_run_that_fails_prints_nothing_and_is_exit_1(env):
    code, store, _, lines = go(["--dry-run"], store=FakeJournalStore(empty_reads()))
    assert code == 1 and lines == []


def test_a_dry_run_on_a_day_with_nothing_to_do_prints_nothing(env):
    code, _, _, lines = go(["--dry-run"], now=datetime(2026, 10, 10, 22, 30, tzinfo=UTC))
    assert code == 0 and lines == []


def test_a_dry_run_still_needs_the_database_url(env, monkeypatch):
    monkeypatch.delenv("JOURNAL_DATABASE_URL")
    code, *_ = go(["--dry-run"])
    assert code == 2


# --- --check (T019) --------------------------------------------------------------------------


def test_check_needs_only_the_market_data_key_and_prints_a_line_per_symbol(env, monkeypatch):
    import json

    monkeypatch.delenv("JOURNAL_DATABASE_URL")
    market = FakeMarketData(quote_time=datetime(2026, 10, 9, 19, 59, tzinfo=UTC))
    market.add("AAPL", current="229.15")

    def no_database(url, **kw):
        raise AssertionError("--check must not connect")

    code, store, _, lines = go(["--check", "AAPL"], market=market, connect=no_database)
    assert code == 0 and store.upserts == [] and store.read_calls == []
    (line,) = [json.loads(x)["check"] for x in lines]
    assert (line["symbol"], line["accepted"]) == ("AAPL", True)


def test_check_without_its_key_is_exit_2_and_names_it(env, monkeypatch, caplog):
    monkeypatch.delenv("JOURNAL_FINNHUB_API_KEY")
    code, *_ = go(["--check", "AAPL"])
    assert code == 2 and "JOURNAL_FINNHUB_API_KEY" in critical(caplog)[0]


def test_check_with_a_rejected_key_is_exit_1(env):
    market = FakeMarketData()
    market.add("AAPL")
    market.fail("get_quote", error=KeyRejected())
    code, *_ = go(["--check", "AAPL"], market=market)
    assert code == 1


def test_check_with_more_than_twenty_symbols_is_exit_2(env):
    code, *_ = go(["--check", *[f"S{i}" for i in range(21)]])
    assert code == 2


# --- --replace and an existing row (T026) -----------------------------------------------------


def test_a_plain_run_over_an_existing_row_is_exit_0_and_writes_nothing(env, caplog):
    store = FakeJournalStore(empty_reads(row_exists=True, **snapshots()))
    with caplog.at_level(logging.INFO, logger="trading_agent.journal"):
        code, store, conn, _ = go(store=store)
    assert code == 0 and store.upserts == [] and conn.closed
    assert "journal: nothing to do: already_written" in caplog.text


def test_replace_rewrites_an_existing_row(env):
    store = FakeJournalStore(empty_reads(row_exists=True, **snapshots()))
    code, store, _, _ = go(["--replace"], store=store)
    assert code == 0 and len(store.replaces) == 1
