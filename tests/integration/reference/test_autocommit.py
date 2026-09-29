"""The production path: a real autocommit connection as ta_reference_data, not the
rolled-back test transaction the other integration tests use (adversarial review).
A failed insert must not poison the connection for the next symbol.

These rows are committed, so they use a far-off trading day that no other test
reads, and are deleted afterwards.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.fakes.market_data import FakeMarketData
from tests.unit.reference.support import Clock
from trading_agent.reference.config import ReferenceConfig
from trading_agent.reference.normalize import ReferenceRow
from trading_agent.reference.service import PgReferenceStore, ReferenceJob

DAY = date(2027, 3, 1)  # a Monday session, EST
JOB_NOW = datetime(2027, 3, 1, 13, 30, tzinfo=UTC)  # 08:30 ET


@pytest.fixture
def job_conn(database_url):
    conn = psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
    conn.execute("SET ROLE ta_reference_data")
    try:
        yield conn
    finally:
        conn.close()
        with psycopg.connect(database_url, autocommit=True) as admin:
            admin.execute("DELETE FROM instrument_reference WHERE trading_day = %s", (DAY,))


def _rows(conn):
    rows = conn.execute(
        "SELECT symbol FROM instrument_reference WHERE trading_day = %s ORDER BY symbol", (DAY,)
    ).fetchall()
    return [r["symbol"] for r in rows]


def test_a_failed_insert_does_not_poison_the_next(job_conn):
    store = PgReferenceStore(job_conn)  # no _allow_savepoints: the real path
    bad = ReferenceRow("ACBAD", "common_stock", "XNAS", Decimal("1000"), Decimal("10"), Decimal(0))
    good = ReferenceRow("ACOK", "common_stock", "XNAS", Decimal("1000"), Decimal("10"), Decimal(5))
    with pytest.raises(psycopg.errors.CheckViolation):
        store.insert(bad, DAY)
    store.insert(good, DAY)
    assert _rows(job_conn) == ["ACOK"]


def test_a_full_tick_on_an_autocommit_connection(job_conn):
    clock = Clock()
    fake = FakeMarketData(clock=clock.on_call, quote_time=datetime(2027, 3, 1, 12, tzinfo=UTC))
    fake.add("ACSEA")
    fake.add("ACSEB")
    job = ReferenceJob(
        fake,
        PgReferenceStore(job_conn),
        ReferenceConfig(seed_symbols=("ACSEA", "ACSEB"), calls_per_minute=30),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    report = job.tick(JOB_NOW)
    assert report.recorded == 2 and _rows(job_conn) == ["ACSEA", "ACSEB"]
    assert job.tick(JOB_NOW).already == 2


def test_the_store_refuses_a_transactional_connection(database_url):
    with psycopg.connect(database_url) as conn:
        with pytest.raises(Exception, match="autocommit"):
            PgReferenceStore(conn)
