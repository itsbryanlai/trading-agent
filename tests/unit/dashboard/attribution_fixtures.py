"""Attribution objects copied from specs/012-journal-writer/contracts/attribution.md."""

from __future__ import annotations

import copy

CONTRACT_EXAMPLE = {
    "schema_version": 1,
    "trading_day": "2026-10-09",
    "previous_trading_day": "2026-10-08",
    "sessions_covered": 1,
    "missed_sessions": [],
    "holding_sessions": 5,
    "summary_version": "0.1",
    "account": {
        "return": "0.000000",
        "base": "previous_close",
        "close_taken_at": "2026-10-09T19:30:04+00:00",
    },
    "agents": {
        "research": {
            "started_on": "2026-10-09",
            "index": "100.00000000",
            "day_return": "0.000000",
            "holdings": {
                "AAPL": {
                    "weight_pct": "5.000000",
                    "ref_price": "229.1500",
                    "support_session": "2026-10-09",
                }
            },
            "scaled_by": None,
            "late_reports": 0,
            "unpriced": [],
            "skipped_targets": [],
            "exited": {"sell": [], "holding_limit": []},
            "usage": {
                "written": 3,
                "argued": 2,
                "no_action": 1,
                "cited": 1,
                "cited_decisions": 1,
                "approved": 1,
                "filled": 0,
            },
        }
    },
}


def example(**changes) -> dict:
    """A deep copy of the contract's example with top-level keys replaced."""
    return {**copy.deepcopy(CONTRACT_EXAMPLE), **changes}


def day(
    trading_day: str,
    account_return: str | None,
    *,
    agents: dict | None = None,
    missed: list | None = None,
) -> dict:
    row = example(trading_day=trading_day, missed_sessions=missed or [])
    row["account"]["return"] = account_return
    if agents is not None:
        row["agents"] = agents
    return row


def book(started_on: str, index: str, **usage) -> dict:
    base = copy.deepcopy(CONTRACT_EXAMPLE["agents"]["research"])
    base.update(started_on=started_on, index=index)
    base["usage"].update(usage)
    return base
