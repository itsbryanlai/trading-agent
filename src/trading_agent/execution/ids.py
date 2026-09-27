"""The order identifier (ADR 0012).

`{trading_day}-{symbol}-{side}-{first 8 hex chars of the verdict id}`: the
order's primary key and the broker's client order id. Re-derived identically
after a restart because the verdict row is durable.
"""

from __future__ import annotations

from uuid import UUID

from trading_agent.risk.model import ApprovedOrder


def order_id(verdict_id: UUID, order: ApprovedOrder) -> str:
    return f"{order.trading_day.isoformat()}-{order.symbol}-{order.side}-{str(verdict_id)[:8]}"
