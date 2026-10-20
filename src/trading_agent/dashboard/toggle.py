"""The pause/resume decision table (contracts/toggle.md, ADR 0028, research D23).

Pure: it decides from values handed to it. Pausing is always allowed. Resuming is written
only when the deployed `run_while_paused` is exactly `False` and the orchestrator's newest
Portfolio Manager run was skipped because of the pause; anything else, including a value that
could not be read, fails closed with a reason. Whether the stored state still matches what the
confirmation page showed is decided by the database (a conditional UPDATE), not here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# The detail the orchestrator writes on a run it skips for the pause. A unit test pins this
# to orchestrator/planner.py's PAUSED, which this package may not import.
SKIPPED_FOR_PAUSE = "trading paused"

PAUSED = "paused"
RUNNING = "running"


@dataclass(frozen=True)
class Pause:
    """The one action offered while trading is running."""


@dataclass(frozen=True)
class Resume:
    """The one action offered while paused, when resuming is allowed."""


@dataclass(frozen=True)
class ResumeRefused:
    reason: str


@dataclass(frozen=True)
class Write:
    """Set the flag to `target` (`paused` or `running`)."""

    target: str


@dataclass(frozen=True)
class Refused:
    reason: str


def skipped_for_pause(row: Mapping | None) -> bool:
    """Whether the Portfolio Manager's newest run was skipped because trading is paused."""
    if row is None:
        return False
    return row.get("outcome") == "skipped" and row.get("detail") == SKIPPED_FOR_PAUSE


def _resume_refusal(run_while_paused, skipped) -> str | None:
    if run_while_paused is True:
        return "run_while_paused"
    if run_while_paused is not False:
        return "unreadable"
    if skipped is not True:
        return "no_skipped_run"
    return None


def available(stored_paused: bool, run_while_paused, skipped) -> Pause | Resume | ResumeRefused:
    """The action the confirmation page offers for the stored state."""
    if not stored_paused:
        return Pause()
    reason = _resume_refusal(run_while_paused, skipped)
    return Resume() if reason is None else ResumeRefused(reason)


def decide(expected, target, run_while_paused, skipped) -> Write | Refused:
    """What a confirmed POST may do. `expected` and `target` are the form's two fields."""
    if (expected, target) == (RUNNING, PAUSED):
        return Write(PAUSED)
    if (expected, target) != (PAUSED, RUNNING):
        return Refused("malformed")
    reason = _resume_refusal(run_while_paused, skipped)
    return Write(RUNNING) if reason is None else Refused(reason)


def warn_market(market_open: bool | None) -> bool:
    """Whether the resume page warns that the market is open (or that it can't be told)."""
    return market_open is not False
