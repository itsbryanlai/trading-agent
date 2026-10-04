"""service.run, User Story 3: the PM weighs evidence, not just the reports' numbers
(specs/008-portfolio-manager spec US3-3). Code can check that the model sees every
report and that a decision may cite them all; whether the reasoning weighs convergence
well is the model's."""

from __future__ import annotations

from datetime import timedelta

from tests.unit.portfolio_manager.builders import inputs, report
from tests.unit.portfolio_manager.service_support import (
    answer_of,
    decide,
    market,
    model_input,
    run,
)
from tests.unit.portfolio_manager.support import NOW


def converging():
    return inputs(
        [
            report("db-r", "AAPL", "buy", size="5", generated_at=NOW - timedelta(hours=2)),
            report(
                "db-oi",
                "AAPL",
                "buy",
                size="4",
                agent="opportunistic_identifier",
                generated_at=NOW - timedelta(hours=1),
                sources=[],
            ),
        ]
    )


def test_converging_reports_both_reach_the_model_with_their_own_sizes_and_evidence():
    _, _, model, _ = run(converging(), market(("AAPL", "200")))
    (symbol,) = model_input(model)["symbols"]
    by_agent = {r["agent"]: r for r in symbol["reports"]}
    assert set(by_agent) == {"research", "opportunistic_identifier"}
    assert (by_agent["research"]["id"], by_agent["opportunistic_identifier"]["id"]) == ("R1", "R2")
    assert by_agent["research"]["suggested_size_pct"] == "5"
    assert by_agent["opportunistic_identifier"]["suggested_size_pct"] == "4"
    assert by_agent["research"]["evidence"] == {"primary_sources": 1, "secondary_sources": 0}
    assert by_agent["opportunistic_identifier"]["evidence"] == {
        "primary_sources": 0,
        "secondary_sources": 0,
    }


def test_the_system_prompt_forbids_summing_or_averaging_the_suggested_sizes():
    _, _, model, _ = run(converging(), market(("AAPL", "200")))
    system = model.calls[0]["system"]
    assert "never the sum or the average of their suggested sizes" in system
    assert "say in your reasoning how you weighed the agreement" in system


def test_a_decision_citing_both_converging_reports_is_written_with_both_links():
    outcome, store, _, _ = run(
        converging(),
        market(("AAPL", "200")),
        answer_of(decide("AAPL", "buy", 6, ["R1", "R2"], reasoning="Two analysts agree.")),
    )
    assert outcome.drops == ()
    (d,) = store.written
    assert d.report_ids == ("db-r", "db-oi")
    assert d.size_pct == 6  # the model's own size, not 5 + 4 and not 4.5
    assert d.reasoning == "Two analysts agree."
