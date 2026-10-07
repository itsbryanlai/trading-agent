"""Exit codes 0, 1, 3, 4 and 5, and what each logs (specs/011 US3; contracts/oi-interface.md
"Exit codes"; research O12)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from tests.fakes.model import FakeModel
from tests.fakes.oi_market_data import FakeOIMarketData
from tests.unit.opportunistic_identifier.conftest import SECRETS
from tests.unit.opportunistic_identifier.support import Clock, FakeConn
from tests.unit.opportunistic_identifier.test_main import proposal, run
from trading_agent.opportunistic_identifier import __main__ as runner
from trading_agent.opportunistic_identifier.ports import KeyRejected
from trading_agent.risk import calendar

SECRET = "SECRET-provider-text-do-not-log"


def critical(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.CRITICAL]


def test_the_database_being_unreachable_is_exit_3(env, config_path, caplog):
    code, *_ = run(config_path=config_path, connect_error=psycopg.OperationalError(SECRET))
    assert code == runner.EXIT_DATABASE == 3
    assert critical(caplog) == ["opportunistic_identifier: database unreachable: OperationalError"]
    assert SECRET not in caplog.text


def test_a_failed_read_is_exit_3_and_nothing_is_written(env, config_path, caplog):
    conn = FakeConn(fail_read=True)
    code, conn, _ = run(config_path=config_path, conn=conn)
    assert code == 3 and conn.written == [] and conn.closed
    assert critical(caplog) == ["opportunistic_identifier: database error: OperationalError"]


def test_a_failed_write_of_reports_is_exit_3(env, config_path, caplog):
    code, conn, _ = run(config_path=config_path, conn=FakeConn(fail_write=True))
    assert code == 3 and conn.closed
    assert critical(caplog) == ["opportunistic_identifier: database error: OperationalError"]


def test_a_failed_write_of_the_no_action_row_itself_is_exit_3_not_1(env, config_path, caplog):
    # The run's own failure (an unusable answer) can't be recorded either: its own status.
    code, conn, _ = run(config_path=config_path, conn=FakeConn(fail_write=True), answer="not json")
    assert code == 3 and conn.written == []
    assert critical(caplog) == ["opportunistic_identifier: database error: OperationalError"]


def test_an_exception_before_any_write_is_exit_4_with_its_type_only(env, config_path, caplog):
    def explode(clock):
        raise RuntimeError(SECRET)

    code, conn, _ = run(config_path=config_path, model=explode)
    assert code == runner.EXIT_CRASHED == 4 and conn.written == []
    assert critical(caplog) == ["opportunistic_identifier: crashed: RuntimeError"]
    assert SECRET not in caplog.text and conn.closed


def test_a_run_that_records_a_failure_is_exit_1_and_writes_its_row(env, config_path):
    market = FakeOIMarketData()
    market.add("AAA")
    market.fail("quote", error=KeyRejected())
    code, conn, _ = run(config_path=config_path, market=market)
    assert code == runner.EXIT_FAILURE_RECORDED == 1
    assert len(conn.written) == 1


def test_a_quiet_no_action_is_exit_0(env, config_path):
    code, conn, _ = run(config_path=config_path, answer={"proposals": []})
    assert code == 0 and len(conn.written) == 1


def test_every_proposal_dropped_is_exit_0(env, config_path):
    code, conn, _ = run(config_path=config_path, answer={"proposals": [proposal("ZZZ")]})
    assert code == 0 and len(conn.written) == 1


def test_outside_the_window_is_exit_0_and_nothing_is_read_or_written(env, config_path, caplog):
    saturday = Clock(datetime(2026, 10, 10, 16, 0, tzinfo=UTC))
    caplog.set_level(logging.INFO)
    code, conn, _ = run(config_path=config_path, clock=saturday)
    assert code == runner.EXIT_OK and conn.written == []
    assert "opportunistic_identifier: outside the trading window; nothing to do" in caplog.text


class ClosingModel(FakeModel):
    def __init__(self, clock, when):
        super().__init__({"proposals": [proposal()]})
        self.clock, self.when = clock, when

    def complete(self, system, user, schema):
        self.clock.now = self.when
        return super().complete(system, user, schema)


def test_the_close_passing_during_the_run_is_exit_5_with_an_error_and_nothing_written(
    env, config_path, caplog
):
    start = datetime(2026, 10, 8, 19, 55, tzinfo=UTC)  # 15:55 ET
    market = FakeOIMarketData(quote_time=start - timedelta(minutes=5))
    market.add("AAA", current="190")
    close = calendar.close_time(start.date())
    code, conn, _ = run(
        config_path=config_path,
        market=market,
        clock=Clock(start),
        model=lambda clock: ClosingModel(clock, close + timedelta(minutes=1)),
    )
    assert code == runner.EXIT_WINDOW_CLOSED == 5 and conn.written == []
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors == [
        "opportunistic_identifier: window_closed: the close passed before the write; "
        "nothing written"
    ]


def test_exit_5_is_distinct_from_every_other_status():
    codes = [
        runner.EXIT_OK,
        runner.EXIT_FAILURE_RECORDED,
        runner.EXIT_REFUSED,
        runner.EXIT_DATABASE,
        runner.EXIT_CRASHED,
        runner.EXIT_WINDOW_CLOSED,
    ]
    assert sorted(codes) == [0, 1, 2, 3, 4, 5]


@pytest.mark.parametrize("secret", SECRETS)
def test_no_exit_path_logs_a_credential(env, config_path, caplog, secret):
    run(config_path=config_path, connect_error=psycopg.OperationalError(secret))
    run(config_path=config_path, conn=FakeConn(fail_write=True))
    assert secret not in caplog.text


class ClosingConn(FakeConn):
    """A connection whose insert is refused for expiring before it was generated."""

    def cursor(self):
        import contextlib

        from tests.unit.opportunistic_identifier.test_service_window_deadline import Violation

        class Cursor:
            def executemany(self, statement, rows):
                raise Violation("reports_expires_after_generated")

        return contextlib.nullcontext(Cursor())


def test_the_database_refusing_a_row_at_the_close_is_exit_5_not_3(env, config_path, caplog):
    code, conn, _ = run(config_path=config_path, conn=ClosingConn())
    assert code == runner.EXIT_WINDOW_CLOSED == 5 and conn.written == []
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [
        "opportunistic_identifier: window_closed: the close passed before the write; "
        "nothing written"
    ]
