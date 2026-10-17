"""SC-006 (specs/011-opportunistic-identifier): a buy report the Opportunistic Identifier
wrote, as its own role, is what the Portfolio Manager's store reads back, tagged with the
OI as its agent. Times use the next real session, so database `now()` never passes expiry."""

from __future__ import annotations

from decimal import Decimal

from tests.integration.helpers import as_role
from tests.integration.portfolio_manager.test_store import CLOSE, RUN_START
from trading_agent.opportunistic_identifier.answer import ReportRow
from trading_agent.opportunistic_identifier.service import PgOIStore
from trading_agent.portfolio_manager.store import PostgresStore

SOURCES = [
    {
        "title": "Finnhub quote for AAA",
        "url": "https://finnhub.io/api/v1/quote?symbol=AAA",
        "publisher": "Finnhub",
        "published_at": "2026-10-08T14:55:00+00:00",
        "relevance": "primary",
    }
]


def test_an_open_oi_buy_report_is_read_by_the_pm_with_the_oi_as_its_agent(conn):
    buy = ReportRow("AAA", "buy", 4, Decimal("6.5"), SOURCES, "Cheap after its fall.", CLOSE)
    quiet = ReportRow(None, "no_action", None, None, [], "Nothing argued.", CLOSE)
    with as_role(conn, "ta_opportunistic_identifier"):
        PgOIStore(conn, _allow_savepoints=True).write([buy, quiet])

    with as_role(conn, "ta_portfolio_manager"):
        inputs = PostgresStore(conn, _allow_savepoints=True).read_inputs(
            RUN_START, journal_entries=5
        )

    (report,) = inputs.reports  # the no_action row is not an input
    assert report.agent == "opportunistic_identifier"
    assert (report.symbol, report.direction, report.conviction) == ("AAA", "buy", 4)
    assert report.suggested_size_pct == Decimal("6.500")
    assert report.rationale_md == "Cheap after its fall." and report.consumed is False
    assert list(report.sources) == SOURCES
    assert report.expires_at == CLOSE
