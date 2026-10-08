"""How far each agent's reports travelled (research J9).

Pure. Counts only: `written`, `argued`, `no_action`, `cited`, `cited_decisions`, `approved`
and `filled`. A decision that cites several of an agent's reports counts once for that
agent, and once for each other agent it cites.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

Row = Mapping[str, Any]


def usage(
    reports: Iterable[Row],
    decision_reports: Iterable[Row],
    verdicts: Iterable[Row],
    orders: Iterable[Row],
) -> dict[str, dict[str, int]]:
    """Per agent over the window's reports; agents with no report are absent."""
    approved_verdicts: dict[object, set[object]] = defaultdict(set)
    for verdict in verdicts:
        if verdict["verdict"] == "approved" and verdict.get("decision_id") is not None:
            approved_verdicts[verdict["decision_id"]].add(verdict["id"])
    filled_verdicts = {o["risk_verdict_id"] for o in orders if (o["fill_qty"] or 0) > 0}

    decisions_of: dict[object, set[object]] = defaultdict(set)
    for link in decision_reports:
        decisions_of[link["report_id"]].add(link["decision_id"])

    counts: dict[str, dict[str, int]] = {}
    cited_by: dict[str, set[object]] = defaultdict(set)
    for report in reports:
        agent = report["agent"]
        entry = counts.setdefault(agent, dict.fromkeys(_FIELDS, 0))
        entry["written"] += 1
        entry["no_action" if report["direction"] == "no_action" else "argued"] += 1
        decided = decisions_of.get(report["id"], set())
        if decided:
            entry["cited"] += 1
            cited_by[agent] |= decided

    for agent, decisions in cited_by.items():
        entry = counts[agent]
        entry["cited_decisions"] = len(decisions)
        entry["approved"] = sum(1 for d in decisions if approved_verdicts.get(d))
        entry["filled"] = sum(
            1 for d in decisions if approved_verdicts.get(d, set()) & filled_verdicts
        )
    return counts


_FIELDS = ("written", "argued", "no_action", "cited", "cited_decisions", "approved", "filled")
