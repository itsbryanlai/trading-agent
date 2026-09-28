"""The order identifier (ADR 0012, FR-008)."""

import re
from uuid import UUID

from tests.unit.execution.builders import approved_buy, approved_sell
from trading_agent.execution.ids import order_id

FORMAT = re.compile(r"^\d{4}-\d{2}-\d{2}-[A-Z][A-Z0-9.]*-(buy|sell)-[0-9a-f]{8}$")


def test_day_symbol_side_and_the_verdicts_first_eight_hex_chars():
    a = approved_buy()
    assert order_id(a.verdict_id, a.order) == "2026-09-28-AAPL-buy-3f9c2a1b"


def test_matches_the_database_format_and_keeps_share_class_dots():
    for approval in (approved_buy(symbol="BRK.B"), approved_sell()):
        oid = order_id(approval.verdict_id, approval.order)
        assert FORMAT.match(oid), oid
        assert len(oid) <= 128
    b = approved_buy(symbol="BRK.B")
    assert order_id(b.verdict_id, b.order) == "2026-09-28-BRK.B-buy-3f9c2a1b"


def test_stable_and_distinct_per_verdict():
    a = approved_sell(source="decision", verdict_id=UUID("11111111-0000-4000-8000-000000000000"))
    b = approved_sell(source="stop_loss", verdict_id=UUID("22222222-0000-4000-8000-000000000000"))
    assert order_id(a.verdict_id, a.order) == order_id(a.verdict_id, a.order)
    assert order_id(a.verdict_id, a.order) != order_id(b.verdict_id, b.order)


def test_verdicts_sharing_their_first_eight_hex_chars_clash():
    # The case FR-008's identifier_clash refusal exists for.
    a = approved_buy(verdict_id=UUID("3f9c2a1b-0000-4000-8000-000000000001"))
    b = approved_buy(verdict_id=UUID("3f9c2a1b-ffff-4000-8000-000000000002"))
    assert order_id(a.verdict_id, a.order) == order_id(b.verdict_id, b.order)
