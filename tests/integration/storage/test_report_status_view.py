"""US2: report status is computed at read time by reports_with_status."""

from __future__ import annotations

from tests.integration.helpers import attempt
from tests.integration.storage.chain import insert_decision, insert_report


def _status(conn, report_id):
    return conn.execute(
        "SELECT status FROM reports_with_status WHERE id = %s", (report_id,)
    ).fetchone()["status"]


def _snapshot(conn, report_id):
    return conn.execute(
        "SELECT *, xmin::text AS row_version FROM reports WHERE id = %s", (report_id,)
    ).fetchone()


def test_unexpired_uncited_report_is_open(conn):
    assert _status(conn, insert_report(conn)) == "open"


def test_past_expiry_report_is_expired(conn):
    assert _status(conn, insert_report(conn, expires_in="-1 hour")) == "expired"


def test_cited_unexpired_report_is_consumed(conn):
    report = insert_report(conn)
    insert_decision(conn, [report])
    assert _status(conn, report) == "consumed"


def test_expiry_wins_over_consumption(conn):
    report = insert_report(conn, expires_in="-1 hour")
    insert_decision(conn, [report])
    assert _status(conn, report) == "expired"


def test_status_is_never_rejected(conn):
    insert_report(conn)
    insert_report(conn, expires_in="-1 hour")
    insert_decision(conn, [insert_report(conn)])
    statuses = {r["status"] for r in conn.execute("SELECT status FROM reports_with_status")}
    assert statuses <= {"open", "expired", "consumed"}


def test_computing_status_writes_nothing_to_reports(conn):
    report = insert_report(conn)
    before = _snapshot(conn, report)
    insert_decision(conn, [report])
    assert _status(conn, report) == "consumed"
    assert _snapshot(conn, report) == before


def test_research_cannot_read_the_view(conn):
    assert attempt(conn, "ta_research", "SELECT * FROM reports_with_status") == "denied"
