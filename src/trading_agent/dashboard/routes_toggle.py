"""The pause/resume toggle (contracts/toggle.md; research D13-D15, ADR 0028).

`GET /toggle` shows the one available action. `POST /toggle` re-reads everything the decision
depends on (the deployed setting and the orchestrator's newest Portfolio Manager run), decides
with the pure table, calls the control writer only on `Write`, writes exactly one `toggle ...`
log line, and redirects to `/toggle/done`. Pausing never depends on a read; resuming fails
closed on any read that fails.
"""

from __future__ import annotations

from datetime import UTC
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from trading_agent.dashboard import deployed, pages, timefmt, toggle
from trading_agent.dashboard.deps import (
    Deps,
    SessionDep,
    WriteDep,
    WriteRefused,
    emit,
    get_deps,
    render,
)
from trading_agent.dashboard.routes_pages import shell
from trading_agent.dashboard.store import StoreError

router = APIRouter()

RESULTS = ("paused", "resumed", "stale", "refused", "failed")
RUN_WHILE_PAUSED_TEXT = (
    "The deployed schedule still has run_while_paused: true. Switching on has two steps: "
    "first release run_while_paused: false, then resume after a close. See the deployment "
    'runbook, section 5 "Switching trading on".'
)
REFUSALS = {
    "run_while_paused": RUN_WHILE_PAUSED_TEXT,
    "unreadable": (
        "The deployed schedule's run_while_paused could not be read, so resuming is refused "
        "until it can."
    ),
    "no_skipped_run": (
        "The deployed schedule says run_while_paused: false, but the orchestrator hasn't yet "
        "skipped a Portfolio Manager run because of the pause, so it may still be running the "
        "old setting. Resume becomes available after the next morning session is skipped. See "
        'the deployment runbook, section 5 "Switching trading on".'
    ),
}
GENERIC_REFUSAL = "The request was refused."


def _pm_skipped(reader) -> bool:
    return toggle.skipped_for_pause(reader.latest_pm_run())


def _market_open(deps: Deps, now) -> bool | None:
    try:
        return bool(deps.market_open(now))
    except Exception:  # an unknown market state still shows the warning (FR-022)
        return None


@router.get("/toggle")
def confirm(request: Request, session: SessionDep) -> Response:
    deps = get_deps(request)
    now = deps.clock()
    try:
        with deps.store.read() as reader:
            state = reader.state()
            if state is None:
                raise StoreError("database: NoStateRow")
            skipped = _pm_skipped(reader)
    except StoreError:
        # The read login failed: pausing never depends on a read, so offer it alone (FR-020).
        # Resume is never offered here, and the form assumes `running`; a wrong guess is
        # caught by the writer's conditional update as "nothing changed".
        context = _unread_shell(deps, session, now)
        return render(deps, "toggle.html", **{**context, "action": "fallback"})
    setting = deployed.run_while_paused(deps.config_dir)
    action = toggle.available(state["trading_paused"], setting, skipped)
    changed = state["trading_paused_changed_at"]
    market = _market_open(deps, now)
    page = {
        "action": type(action).__name__.lower(),
        "refusal": REFUSALS.get(getattr(action, "reason", ""), GENERIC_REFUSAL),
        "since": None if changed is None else timefmt.moment(changed, deps.zone),
        "market_warning": toggle.warn_market(market),
        "market_unknown": market is None,
        "halt": pages.halt_line(state),
    }
    context = shell(deps, session, state, now, "toggle")
    return render(deps, "toggle.html", **{**context, **page})


def _decide(deps: Deps, expected, target):
    setting = deployed.run_while_paused(deps.config_dir)
    skipped = False
    if (expected, target) == (toggle.PAUSED, toggle.RUNNING):
        try:
            with deps.store.read() as reader:
                skipped = _pm_skipped(reader)
        except StoreError:
            skipped = False  # fail closed: no evidence, no resume
    return toggle.decide(expected, target, setting, skipped)


def _done(result: str) -> Response:
    return RedirectResponse(f"/toggle/done?result={result}", status_code=303)


@router.post("/toggle")
def apply(
    request: Request,
    session: WriteDep,
    expected: Annotated[str | None, Form()] = None,
    target: Annotated[str | None, Form()] = None,
) -> Response:
    deps = get_deps(request)
    decision = _decide(deps, expected, target)
    if isinstance(decision, toggle.Refused):
        emit("toggle refused", reason=decision.reason)
        if decision.reason == "malformed":
            raise WriteRefused
        return _done("refused")
    try:
        outcome = deps.control.set_paused(
            expected == toggle.PAUSED, decision.target == toggle.PAUSED
        )
    except Exception as exc:  # a failed write is reported, never retried
        emit("toggle failed", type=type(exc).__name__)
        return _done("failed")
    if outcome != "written":
        emit("toggle refused", reason="stale")
        return _done("stale")
    at = deps.clock().astimezone(UTC).isoformat(timespec="seconds")
    word = "paused" if decision.target == toggle.PAUSED else "resumed"
    emit(f"toggle {word}", at=at)
    return _done(word)


def _unread_shell(deps: Deps, session, now) -> dict:
    """The shell's slots when no state could be read: no banner, no state, only the basics."""
    return {
        "signed_in": True,
        "csrf": session.csrf,
        "active": "toggle",
        "read_at": timefmt.moment(now, deps.zone),
        "unread": True,
        "now_text": "",
        "state_text": "",
        "refusal": GENERIC_REFUSAL,
    }


def _read_fresh(deps: Deps):
    with deps.store.read() as reader:
        state = reader.state()
        if state is None:
            raise StoreError("database: NoStateRow")
        return state, _pm_skipped(reader)


@router.get("/toggle/done")
def done(
    request: Request,
    session: SessionDep,
    result: Annotated[str, Query()] = "",
) -> Response:
    if result not in RESULTS:
        raise HTTPException(404)
    deps = get_deps(request)
    now = deps.clock()
    heading = {"paused": "Trading paused.", "resumed": "Trading resumed."}.get(
        result, "Nothing was changed."
    )
    try:
        state, skipped = _read_fresh(deps)
    except StoreError:  # the result is still known; only the fresh state is missing (FR-020)
        context = _unread_shell(deps, session, now)
        return render(deps, "toggle_done.html", **{**context, "result": result, "heading": heading})
    paused = state["trading_paused"]
    refusal = GENERIC_REFUSAL
    if result == "refused":
        action = toggle.available(paused, deployed.run_while_paused(deps.config_dir), skipped)
        refusal = REFUSALS.get(getattr(action, "reason", ""), GENERIC_REFUSAL)
    page = {
        "result": result,
        "heading": heading,
        "unread": False,
        "now_text": "paused" if paused else "running",
        "state_text": pages.state_text(state, deps.zone),
        "refusal": refusal,
    }
    context = shell(deps, session, state, now, "toggle")
    return render(deps, "toggle_done.html", **{**context, **page})
