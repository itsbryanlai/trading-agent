"""The order identifier (ADR 0012).

`{trading_day}-{symbol}-{side}-{first 8 hex chars of the verdict id}`: the
order's primary key and the broker's client order id. Re-derived identically
after a restart because the verdict row is durable.
"""

from __future__ import annotations

import re
from uuid import UUID

from trading_agent.risk.model import ApprovedOrder

# Exactly migration 0007's orders_id_format CHECK.
ORDER_ID_FORMAT = re.compile(r"^\d{4}-\d{2}-\d{2}-[A-Z][A-Z0-9.]*-(buy|sell)-[0-9a-f]{8}$")


def is_valid(identifier: str) -> bool:
    """Whether the database would accept this identifier. Checked before any broker
    call, so no order is placed that couldn't be recorded (research E16)."""
    return ORDER_ID_FORMAT.fullmatch(identifier) is not None


def order_id(verdict_id: UUID, order: ApprovedOrder) -> str:
    return f"{order.trading_day.isoformat()}-{order.symbol}-{order.side}-{str(verdict_id)[:8]}"
