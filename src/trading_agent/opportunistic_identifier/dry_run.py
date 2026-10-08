"""`--dry-run` output and its read-only store (contracts/oi-interface.md "Invocation").

A dry run does everything except the write, and prints JSON lines: the `slice`, each `skip`,
the `shortlist` (symbol, both ranks, score), each would-be row (`would_write`), each
`dropped` proposal, and a `summary` with the run's counts, token use included. It never prints
a key, the prompt or the model's raw answer: a dropped proposal is named by index, symbol and
reason only.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

from trading_agent.opportunistic_identifier.answer import ReportRow
from trading_agent.opportunistic_identifier.outcome import RunOutcome
from trading_agent.opportunistic_identifier.service import OIStore


class ReadOnlyStore:
    """What a dry run is given instead of the real store: it can read the open reports (or
    say none are open, with no database) and has no `write` at all."""

    def __init__(self, reader: OIStore | None) -> None:
        self._reader = reader

    def open_symbols(self, now: datetime) -> frozenset[str]:
        return self._reader.open_symbols(now) if self._reader is not None else frozenset()


def lines(outcome: RunOutcome, *, closed_day: bool) -> list[str]:
    records: list[dict] = []
    scan = outcome.slice
    if scan is not None:
        line = {
            "run_index": scan.run_index,
            "batch": scan.batch + 1 if scan.batches else 0,
            "batches": scan.batches,
            "symbols": list(scan.symbols),
            "session": not closed_day,
        }
        if closed_day:
            line["note"] = "closed day: the next session's first slot"
        records.append({"slice": line})
    records += [{"skip": {"symbol": s.symbol, "reason": s.reason}} for s in outcome.skips]
    records += [
        {
            "shortlist": {
                "symbol": c.symbol,
                "rank_move": c.rank_move,
                "rank_high": c.rank_high,
                "score": float(c.score),
            }
        }
        for c in outcome.shortlist
    ]
    records += [{"would_write": _row(row)} for row in outcome.rows]
    records += [
        {"dropped": {"index": d.index, "symbol": d.symbol, "reason": d.reason}}
        for d in outcome.drops
    ]
    records.append(
        {"summary": {**asdict(outcome.counts), "failure": outcome.failure, "note": outcome.note}}
    )
    return [json.dumps(record, ensure_ascii=False) for record in records]


def _row(row: ReportRow) -> dict:
    return {
        "symbol": row.symbol,
        "direction": row.direction,
        "conviction": row.conviction,
        "suggested_size_pct": None
        if row.suggested_size_pct is None
        else str(row.suggested_size_pct),
        "sources": row.sources,
        "rationale_md": row.rationale_md,
        "expires_at": row.expires_at.isoformat(),
    }
