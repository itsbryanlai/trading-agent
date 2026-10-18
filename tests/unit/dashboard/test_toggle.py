"""The pause/resume decision table (specs/014 tasks T010; contracts/toggle.md, ADR 0028)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from trading_agent.dashboard import toggle
from trading_agent.dashboard.toggle import (
    Pause,
    Refused,
    Resume,
    ResumeRefused,
    Write,
)

ROOT = Path(__file__).resolve().parents[3]
SKIPPED_ROW = {"outcome": "skipped", "detail": "trading paused", "slot_key": "pm-0930"}


# --- the evidence from the orchestrator -----------------------------------------------------


def test_the_text_is_the_orchestrators_own():
    # No import of the sibling: read its source and pin the text on both sides (ADR 0028).
    source = (ROOT / "src" / "trading_agent" / "orchestrator" / "planner.py").read_text()
    match = re.search(r'^PAUSED = "([^"]+)"$', source, flags=re.MULTILINE)
    assert match, "orchestrator/planner.py no longer defines PAUSED"
    assert match.group(1) == toggle.SKIPPED_FOR_PAUSE == "trading paused"


def test_a_skipped_run_with_the_pause_detail_is_the_evidence():
    assert toggle.skipped_for_pause(SKIPPED_ROW) is True


@pytest.mark.parametrize(
    "row",
    [
        None,
        {},
        {"outcome": "succeeded", "detail": None},
        {"outcome": "succeeded", "detail": "trading paused"},
        {"outcome": "failed", "detail": "trading paused"},
        {"outcome": "running", "detail": None},
        {"outcome": "timed_out", "detail": "trading paused"},
        {"outcome": "interrupted", "detail": "trading paused"},
        {"outcome": "skipped", "detail": "outside session"},
        {"outcome": "skipped", "detail": "trading paused "},
        {"outcome": "skipped", "detail": "Trading paused"},
        {"outcome": "skipped", "detail": "trading paused: more"},
        {"outcome": "skipped", "detail": None},
        {"outcome": "skipped"},
        {"detail": "trading paused"},
        {"outcome": "SKIPPED", "detail": "trading paused"},
    ],
)
def test_anything_else_is_not_the_evidence(row):
    assert toggle.skipped_for_pause(row) is False


# --- what the page offers -------------------------------------------------------------------


def test_running_offers_pause_whatever_the_setting_or_evidence():
    for setting in (True, False, None):
        for skipped in (True, False):
            assert toggle.available(False, setting, skipped) == Pause()


def test_paused_offers_resume_only_with_the_setting_off_and_the_evidence():
    assert toggle.available(True, False, True) == Resume()


@pytest.mark.parametrize(
    ("setting", "skipped", "reason"),
    [
        (True, True, "run_while_paused"),
        (True, False, "run_while_paused"),
        (None, True, "unreadable"),
        (None, False, "unreadable"),
        (False, False, "no_skipped_run"),
    ],
)
def test_paused_refuses_resume_with_the_reason(setting, skipped, reason):
    assert toggle.available(True, setting, skipped) == ResumeRefused(reason)


# --- the decision table, row by row ---------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "target", "setting", "skipped", "outcome"),
    [
        # running -> pause: any setting
        ("running", "paused", False, True, Write("paused")),
        ("running", "paused", False, False, Write("paused")),
        ("running", "paused", True, False, Write("paused")),
        ("running", "paused", None, False, Write("paused")),
        # paused -> resume
        ("paused", "running", False, True, Write("running")),
        ("paused", "running", False, False, Refused("no_skipped_run")),
        ("paused", "running", True, True, Refused("run_while_paused")),
        ("paused", "running", True, False, Refused("run_while_paused")),
        ("paused", "running", None, True, Refused("unreadable")),
        ("paused", "running", None, False, Refused("unreadable")),
        # malformed: nothing is written
        ("paused", "paused", False, True, Refused("malformed")),
        ("running", "running", False, True, Refused("malformed")),
        (None, "running", False, True, Refused("malformed")),
        ("paused", None, False, True, Refused("malformed")),
        (None, None, False, True, Refused("malformed")),
        ("", "running", False, True, Refused("malformed")),
        ("paused", "", False, True, Refused("malformed")),
        ("maybe", "running", False, True, Refused("malformed")),
        ("paused", "resumed", False, True, Refused("malformed")),
        ("PAUSED", "running", False, True, Refused("malformed")),
        ("true", "false", False, True, Refused("malformed")),
    ],
)
def test_decision_table(expected, target, setting, skipped, outcome):
    assert toggle.decide(expected, target, setting, skipped) == outcome


def test_a_non_boolean_setting_is_unreadable_not_false():
    for odd in ("false", 0, 1, "", [], {}):
        assert toggle.decide("paused", "running", odd, True) == Refused("unreadable")


def test_only_a_real_true_counts_as_evidence():
    for odd in (1, "yes", "true", None, {"outcome": "skipped"}):
        assert toggle.decide("paused", "running", False, odd) == Refused("no_skipped_run")


@given(
    expected=st.one_of(st.none(), st.sampled_from(["paused", "running", "x", ""]), st.text()),
    target=st.one_of(st.none(), st.sampled_from(["paused", "running", "x", ""]), st.text()),
    setting=st.one_of(st.none(), st.booleans(), st.integers(), st.text()),
    skipped=st.one_of(st.booleans(), st.none(), st.integers()),
)
def test_a_resume_is_written_only_when_the_setting_is_off_and_a_run_was_skipped(
    expected, target, setting, skipped
):
    outcome = toggle.decide(expected, target, setting, skipped)
    if outcome == Write("running"):
        assert expected == "paused"
        assert setting is False
        assert skipped is True
    if isinstance(outcome, Write):
        assert (expected, target) in {("running", "paused"), ("paused", "running")}
    else:
        assert isinstance(outcome, Refused)
        assert outcome.reason in {"run_while_paused", "unreadable", "no_skipped_run", "malformed"}


@given(setting=st.one_of(st.none(), st.booleans()), skipped=st.booleans())
def test_a_pause_is_never_refused(setting, skipped):
    assert toggle.decide("running", "paused", setting, skipped) == Write("paused")


# --- the market warning ---------------------------------------------------------------------


@pytest.mark.parametrize(("market_open", "warn"), [(True, True), (None, True), (False, False)])
def test_warn_market(market_open, warn):
    assert toggle.warn_market(market_open) is warn
