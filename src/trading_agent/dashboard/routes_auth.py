"""Sign-in and sign-out (contracts/routes.md; research D4, D7, D8).

The sign-in gate comes first: `try_acquire` either returns the wait, answered at once without
checking the password, or reserves the single check slot. The slot is always freed in a
`finally`, so an error while checking can't leave sign-in stuck.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import RedirectResponse

from trading_agent.dashboard import auth
from trading_agent.dashboard.address import client_address
from trading_agent.dashboard.deps import (
    COOKIE_NAME,
    WriteDep,
    WriteRefused,
    current_session,
    emit,
    get_deps,
    origin_ok,
    render,
)
from trading_agent.dashboard.throttle import Acquired

router = APIRouter()

FAILED = "Sign-in failed."


def _cookie_attributes(max_age: int) -> dict:
    return {
        "max_age": max_age,
        "path": "/",
        "secure": True,
        "httponly": True,
        "samesite": "strict",
    }


def _form(request: Request, status_code: int = 200, error: str | None = None) -> Response:
    return render(get_deps(request), "sign_in.html", status_code, error=error)


@router.get("/sign-in")
def sign_in_form(request: Request) -> Response:
    if current_session(request) is not None:
        return RedirectResponse("/", status_code=303)
    return _form(request)


@router.post("/sign-in")
def sign_in(request: Request, password: Annotated[str | None, Form()] = None) -> Response:
    deps = get_deps(request)
    if not origin_ok(request.headers.get("origin"), request.headers.get("host")):
        raise WriteRefused
    now = deps.clock()
    address = client_address(request.headers, request.client.host if request.client else None)
    outcome = deps.gate.try_acquire(address, now)
    if not isinstance(outcome, Acquired):
        emit("sign_in too_soon", wait=outcome.seconds)
        message = f"Too many attempts. Try again in {outcome.seconds} seconds."
        return _form(request, 429, message)
    ok = False
    session = None
    try:
        if auth.verify_password(password or "", deps.password_hash):
            session = deps.sessions.create(now)
            ok = True
    finally:
        deps.gate.release(address, ok, now)
    if session is None:
        site_wait, address_wait = deps.gate.waits(address, now)
        emit("sign_in failed", site_wait=site_wait, address_wait=address_wait)
        return _form(request, 200, FAILED)
    emit("sign_in ok")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        auth.sign(deps.secret, session.id),
        **_cookie_attributes(deps.config.session_hours * 3600),
    )
    return response


@router.post("/sign-out")
def sign_out(request: Request, session: WriteDep) -> Response:
    deps = get_deps(request)
    deps.sessions.delete(session.id)
    emit("sign_out")
    response = RedirectResponse("/sign-in", status_code=303)
    response.set_cookie(COOKIE_NAME, "", **_cookie_attributes(0))
    return response
