"""What every route shares: the injected parts, the sign-in guards, templates and log lines.

`create_app` (app.py) builds one `Deps` and stores it on the app; routes reach it through
`get_deps`. Everything with a side effect (the store, the control writer, the clock, the
calendar) arrives here as an argument, so tests run the whole app in process.

The guards are the dashboard's session and cross-site checks (research D4, D11):
- `require_session` for pages: no valid session means a redirect to /sign-in;
- `require_write` for every POST except the sign-in: a valid session, then the CSRF value,
  then an `Origin` that is absent or has the request's own host and port.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from trading_agent.dashboard import auth
from trading_agent.dashboard.config import DashboardConfig
from trading_agent.dashboard.throttle import SignInGate

COOKIE_NAME = "__Host-ta_dashboard"
SIGN_IN_PATH = "/sign-in"
TOGGLE_PATH = "/toggle"
TEMPLATES = Path(__file__).resolve().parent / "templates"
STATIC = Path(__file__).resolve().parent / "static"

log = logging.getLogger("trading_agent.dashboard")


def emit(event: str, **fields: object) -> None:
    """One log line, `dashboard <event> key=value ...`. Callers pass only safe values."""
    tail = "".join(f" {key}={value}" for key, value in fields.items())
    log.info("dashboard %s%s", event, tail)


@dataclass(frozen=True)
class Deps:
    store: object
    control: object
    sessions: auth.Sessions
    gate: SignInGate
    config: DashboardConfig
    secret: str
    password_hash: str
    config_dir: Path
    clock: Callable[[], datetime]
    market_open: Callable[[datetime], bool]
    templates: Environment
    zone: ZoneInfo


class NotSignedIn(Exception):
    """Redirect to the sign-in page."""


class WriteRefused(Exception):
    """403: the request carried no valid CSRF value or came from another site."""


def build_environment() -> Environment:
    """Autoescape on for every template; a missing variable is an error, not an empty string."""
    return Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=True,
        undefined=StrictUndefined,
        auto_reload=False,
    )


def get_deps(request: Request) -> Deps:
    return request.app.state.deps


def render(deps: Deps, template: str, status_code: int = 200, **context: object) -> HTMLResponse:
    """Render a page. The shell's slots default to empty, so a page fills only what it has."""
    page = {
        "signed_in": False,
        "csrf": None,
        "active": None,
        "banner": None,
        "halt": None,
        "toggle_label": None,
        "read_at": None,
        "pause_link": False,
        **context,
    }
    return HTMLResponse(deps.templates.get_template(template).render(page), status_code)


def route_template(request: Request) -> str:
    """The matched route's path template; never the URL, which may carry user input."""
    route = request.scope.get("route")
    return getattr(route, "path", "unmatched")


def current_session(request: Request) -> auth.Session | None:
    deps = get_deps(request)
    value = request.cookies.get(COOKIE_NAME)
    if not value:
        return None
    session_id = auth.unsign(deps.secret, value)
    if session_id is None:
        return None
    return deps.sessions.get(session_id, deps.clock())


def origin_ok(origin: str | None, host: str | None) -> bool:
    """True when `Origin` is absent, or its host and port equal the request's `Host`.

    The scheme is not compared: behind Railway's edge the app sees `http` while the browser
    sends `https://` (research D11).
    """
    if origin is None:
        return True
    if not host:
        return False
    try:
        netloc = urlsplit(origin).netloc
    except ValueError:
        return False
    return bool(netloc) and netloc.lower() == host.lower()


def require_session(request: Request) -> auth.Session:
    session = current_session(request)
    if session is None:
        raise NotSignedIn
    return session


def _refuse(request: Request, reason: str) -> None:
    if request.url.path == TOGGLE_PATH:
        emit("toggle refused", reason=reason)


async def require_write(request: Request) -> auth.Session:
    session = current_session(request)
    if session is None:
        _refuse(request, "no_session")
        raise NotSignedIn
    form = await request.form()
    submitted = form.get("csrf")
    if not auth.csrf_ok(session, submitted if isinstance(submitted, str) else None):
        _refuse(request, "csrf")
        raise WriteRefused
    if not origin_ok(request.headers.get("origin"), request.headers.get("host")):
        _refuse(request, "origin")
        raise WriteRefused
    return session


# Route parameters: `session: SessionDep` for a page, `session: WriteDep` for a POST.
SessionDep = Annotated[auth.Session, Depends(require_session)]
WriteDep = Annotated[auth.Session, Depends(require_write)]
