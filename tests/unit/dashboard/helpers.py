"""Shared builders for the dashboard's HTTP tests (specs/014 tasks T014).

Everything is in process: `TestClient(app, base_url="https://testserver")` so `Secure` cookies
are sent, a fake store and a fake clock, and no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fakes.dashboard_store import FakeStore
from trading_agent.dashboard import auth
from trading_agent.dashboard.app import create_app
from trading_agent.dashboard.config import load_config
from trading_agent.dashboard.deps import COOKIE_NAME
from trading_agent.dashboard.throttle import SignInGate

SECRET = "fake-secret-not-real-0123456789abcdef0123"
PASSWORD = "correct horse battery staple"
# The tasks.md test clock, "Default": Tue 2026-10-20 07:00 +08, after Monday's close.
DEFAULT_NOW = datetime(2026, 10, 19, 23, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[3]


class Clock:
    def __init__(self, now: datetime = DEFAULT_NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@dataclass
class Built:
    app: FastAPI
    clock: Clock
    store: FakeStore
    control: SimpleNamespace
    sessions: auth.Sessions
    gate: SignInGate
    password_hash: str

    def client(self, *, base_url: str = "https://testserver", **kwargs) -> TestClient:
        kwargs.setdefault("raise_server_exceptions", False)
        return TestClient(self.app, base_url=base_url, **kwargs)

    def sign_in(self) -> tuple[auth.Session, dict[str, str]]:
        """A live session and the Cookie header that carries it."""
        session = self.sessions.create(self.clock())
        return session, {"Cookie": f"{COOKIE_NAME}={auth.sign(SECRET, session.id)}"}


def build(*, store: FakeStore | None = None, market_open=lambda now: False) -> Built:
    config = load_config()
    clock = Clock()
    store = store or FakeStore()
    control = SimpleNamespace(calls=[])
    sessions = auth.Sessions(config.max_sessions, timedelta(hours=config.session_hours))
    gate = SignInGate(
        config.sign_in_delay.first_seconds,
        config.sign_in_delay.cap_seconds,
        config.sign_in_delay.tracked_addresses,
    )
    password_hash = auth.hash_password(PASSWORD, n=16)
    app = create_app(
        store=store,
        control=control,
        sessions=sessions,
        gate=gate,
        config=config,
        secret=SECRET,
        password_hash=password_hash,
        config_dir=ROOT / "config",
        clock=clock,
        market_open=market_open,
    )
    return Built(app, clock, store, control, sessions, gate, password_hash)
