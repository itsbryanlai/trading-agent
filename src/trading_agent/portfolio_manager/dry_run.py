"""The `--dry-run` report (specs/008-portfolio-manager contracts/pm-interface.md): one JSON
object per line on stdout, from what a run did instead of writing.

Lines, in order: `candidate` for each symbol given to the model, `skipped` for each symbol
that was not, `would_write` for each decision that would have been written, `dropped` for
each proposal the checker refused, and one `summary`. This is the owner's terminal, not a
log: a would-be row carries its reasoning. Nothing here reads a variable.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from trading_agent.portfolio_manager.service import RunOutcome


def lines(outcome: RunOutcome) -> Iterator[str]:
    built = outcome.built
    if built is not None:
        for c in built.candidates:
            yield _line(
                "candidate",
                symbol=c.symbol,
                quote=str(c.quote),
                quote_time=c.quote_time.isoformat(),
                current_weight_pct=str(c.current_weight_pct),
                report_ids=sorted(r.report_id for r in built.refs.values() if r.symbol == c.symbol),
            )
    for symbol, reason in outcome.skipped:
        yield _line("skipped", symbol=symbol, reason=reason)
    for d in outcome.decisions:
        yield _line(
            "would_write",
            symbol=d.symbol,
            direction=d.direction,
            size_pct=str(d.size_pct),
            quote=str(d.quote),
            quote_time=d.quote_time.isoformat(),
            report_ids=list(d.report_ids),
            reasoning=d.reasoning,
        )
    for drop in outcome.drops:
        yield _line("dropped", index=drop.index, symbol=drop.symbol, reason=drop.reason)
    yield _line(
        "summary",
        failure=outcome.failure,
        note=outcome.note or None,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        input_chars=outcome.input_chars,
        candidates=len(outcome.candidates),
        would_write=len(outcome.decisions),
        dropped=len(outcome.drops),
    )


def _line(kind: str, **fields) -> str:
    return json.dumps({"type": kind, **fields}, ensure_ascii=False)
