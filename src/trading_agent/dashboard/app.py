"""The app factory: security headers, error pages and the static stylesheet.

`create_app` takes every collaborator as an argument (research D2-D4, D10-D12). The docs and
OpenAPI routes are off, so the only routes are the ones this package registers. Later stories
add their routers here.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from trading_agent.dashboard import deps as shared
from trading_agent.dashboard import routes_auth
from trading_agent.dashboard.auth import Sessions
from trading_agent.dashboard.config import DashboardConfig
from trading_agent.dashboard.deps import Deps, NotSignedIn, WriteRefused
from trading_agent.dashboard.store import StoreError
from trading_agent.dashboard.throttle import SignInGate

HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000",
}
STATIC_PREFIX = "/static/"

NOT_FOUND = "Not found."
REFUSED = "Request refused. Nothing was changed."
UNAVAILABLE = "Database unavailable. Nothing is shown until it is back."
FAILED = "Something went wrong."

_TYPE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def apply_headers(response: Response, path: str) -> Response:
    for name, value in HEADERS.items():
        response.headers[name] = value
    if not (path.startswith(STATIC_PREFIX) and response.status_code < 400):
        response.headers["Cache-Control"] = "no-store"
    return response


def create_app(
    *,
    store,
    control,
    sessions: Sessions,
    gate: SignInGate,
    config: DashboardConfig,
    secret: str,
    password_hash: str,
    config_dir: Path,
    clock: Callable[[], datetime],
    market_open: Callable[[datetime], bool],
) -> FastAPI:
    deps = Deps(
        store=store,
        control=control,
        sessions=sessions,
        gate=gate,
        config=config,
        secret=secret,
        password_hash=password_hash,
        config_dir=config_dir,
        clock=clock,
        market_open=market_open,
        templates=shared.build_environment(),
        zone=ZoneInfo(config.owner_time_zone),
    )
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.deps = deps
    _add_headers(app)
    _add_error_pages(app, deps)

    app.include_router(routes_auth.router)

    @app.get("/static/style.css")
    def stylesheet() -> FileResponse:
        return FileResponse(shared.STATIC / "style.css", media_type="text/css")

    return app


def _add_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        return apply_headers(await call_next(request), request.url.path)


def _error_page(deps: Deps, request: Request, status_code: int, message: str) -> Response:
    # The 500 handler runs outside the middleware above, so every page sets its own headers.
    response = shared.render(deps, "error.html", status_code, message=message)
    return apply_headers(response, request.url.path)


def _database_error_type(exc: StoreError) -> str:
    name = str(exc).removeprefix("database: ")
    return name if _TYPE_NAME.match(name) else type(exc).__name__


def _add_error_pages(app: FastAPI, deps: Deps) -> None:
    @app.exception_handler(NotSignedIn)
    async def not_signed_in(request: Request, exc: NotSignedIn) -> Response:
        return RedirectResponse(shared.SIGN_IN_PATH, status_code=303)

    @app.exception_handler(WriteRefused)
    async def write_refused(request: Request, exc: WriteRefused) -> Response:
        return _error_page(deps, request, 403, REFUSED)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> Response:
        return _error_page(deps, request, 403, REFUSED)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code in (404, 405):
            return _error_page(deps, request, 404, NOT_FOUND)
        return _error_page(deps, request, exc.status_code, REFUSED)

    @app.exception_handler(StoreError)
    async def database_unavailable(request: Request, exc: StoreError) -> Response:
        shared.emit("error", type=_database_error_type(exc), route=shared.route_template(request))
        return _error_page(deps, request, 503, UNAVAILABLE)

    @app.exception_handler(Exception)
    async def failed(request: Request, exc: Exception) -> Response:
        shared.emit("error", type=type(exc).__name__, route=shared.route_template(request))
        return _error_page(deps, request, 500, FAILED)
