"""Usage counts per agent (research J9, spec US2 scenario 2)."""

from __future__ import annotations

from decimal import Decimal

from trading_agent.journal.usage import usage


def _report(rid, agent, direction="buy"):
    return {"id": rid, "agent": agent, "direction": direction}


def _link(decision, report):
    return {"decision_id": decision, "report_id": report}


def _verdict(vid, decision, verdict="approved"):
    return {"id": vid, "decision_id": decision, "verdict": verdict}


def _order(verdict_id, qty, status="filled"):
    return {"risk_verdict_id": verdict_id, "status": status, "fill_qty": Decimal(qty)}


def test_decision_citing_both_agents_approved_not_filled():
    reports = [_report("r1", "research"), _report("o1", "opportunistic_identifier")]
    links = [_link("d1", "r1"), _link("d1", "o1")]
    counts = usage(reports, links, [_verdict("v1", "d1")], [])
    for agent in ("research", "opportunistic_identifier"):
        assert counts[agent] == {
            "written": 1,
            "argued": 1,
            "no_action": 0,
            "cited": 1,
            "cited_decisions": 1,
            "approved": 1,
            "filled": 0,
        }


def test_written_argued_and_no_action():
    reports = [_report("a", "research"), _report("b", "research", "no_action")]
    counts = usage(reports, [], [], [])["research"]
    assert (counts["written"], counts["argued"], counts["no_action"]) == (2, 1, 1)
    assert counts["cited"] == 0


def test_two_reports_one_decision_count_once_in_cited_decisions():
    reports = [_report("a", "research"), _report("b", "research")]
    links = [_link("d1", "a"), _link("d1", "b")]
    counts = usage(reports, links, [_verdict("v1", "d1")], [_order("v1", "5")])["research"]
    assert counts["cited"] == 2
    assert counts["cited_decisions"] == 1
    assert counts["approved"] == 1
    assert counts["filled"] == 1


def test_rejected_decision_is_cited_not_approved():
    counts = usage(
        [_report("a", "research")],
        [_link("d1", "a")],
        [_verdict("v1", "d1", "rejected")],
        [],
    )["research"]
    assert (counts["cited_decisions"], counts["approved"], counts["filled"]) == (1, 0, 0)


def test_filled_needs_positive_fill_qty():
    reports = [_report("a", "research")]
    links = [_link("d1", "a"), _link("d2", "a"), _link("d3", "a")]
    verdicts = [_verdict("v1", "d1"), _verdict("v2", "d2"), _verdict("v3", "d3")]
    orders = [
        _order("v1", "3", "partially_filled"),
        _order("v2", "0", "expired"),
        _order("v3", "0", "submitted"),
    ]
    counts = usage(reports, links, verdicts, orders)["research"]
    assert counts["approved"] == 3
    assert counts["filled"] == 1


def test_decision_without_verdict_is_neither_approved_nor_filled():
    counts = usage([_report("a", "research")], [_link("d1", "a")], [], [])["research"]
    assert (counts["cited_decisions"], counts["approved"]) == (1, 0)


def test_agent_without_reports_is_absent():
    assert usage([], [], [], []) == {}
