"""The overview and the health check (specs/014 tasks T017; US1 scenarios 1-6)."""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tests.fakes.dashboard_store import FakeStore, failing
from tests.unit.dashboard.helpers import Built, build

SINCE = datetime(2026, 10, 5, 21, 12, tzinfo=UTC)
WEEKEND = datetime(2026, 10, 25, 2, 0, tzinfo=UTC)
POSITIONS = [
    {
        "symbol": "AAPL",
        "qty": Decimal("10"),
        "avg_entry_price": Decimal("187.25"),
        "updated_at": SINCE,
    },
    {
        "symbol": "<script>alert(1)</script>",
        "qty": Decimal("2.5"),
        "avg_entry_price": Decimal("100.10"),
        "updated_at": SINCE,
    },
]
POINTS = [
    {"taken_at": datetime(2026, 10, 19, 14, 0, tzinfo=UTC), "equity": Decimal("99000.00")},
    {"taken_at": datetime(2026, 10, 19, 20, 0, tzinfo=UTC), "equity": Decimal("100000.00")},
]


def _get(built: Built, path: str = "/", **kwargs):
    _, cookie = built.sign_in()
    return built.client().get(path, headers=cookie, **kwargs)


def _store(**kwargs) -> FakeStore:
    return FakeStore(positions=POSITIONS, points=POINTS, **kwargs)


def test_scenario_state_figures_positions_and_curve():
    built = build(store=_store())
    response = _get(built)
    assert response.status_code == 200
    text = response.text
    assert "Trading is running." in text
    for fragment in (
        "100,000.00",  # equity
        "40,000.00",  # cash
        "80,000.00",  # buying power
        "(3 h old)",
        "1,872.50",  # 10 x 187.25
        "250.25",  # 2.5 x 100.10
        "<polyline",
        "Read at 20 Oct 07:00 SGT (19 Oct 19:00 ET).",
    ):
        assert fragment in text
    assert "Pause trading" in text and 'action="/sign-out"' in text
    assert built.store.names() == ["state", "positions", "latest_snapshot", "equity_points"]
    assert built.store.read_calls == 1  # one transaction


def test_a_stored_symbol_is_escaped_never_live_html():
    text = _get(build(store=_store())).text
    assert "<script>alert(1)" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_paused_shows_the_banner_and_the_resume_button():
    state = {
        "trading_paused": True,
        "trading_paused_changed_at": SINCE,
        "daily_loss_halt_active": False,
        "current_trading_date": date(2026, 10, 19),
    }
    text = _get(build(store=_store(state=state))).text
    assert "Observe-only. Trading paused since 6 Oct 05:12 SGT (5 Oct 17:12 ET)." in text
    assert "The PM still runs (run_while_paused: true)" in text  # the repository's schedule
    assert "Resume trading" in text and "Pause trading" not in text


def test_the_halt_is_shown_on_the_overview_and_in_the_shell():
    state = {
        "trading_paused": False,
        "trading_paused_changed_at": None,
        "daily_loss_halt_active": True,
        "current_trading_date": date(2026, 10, 19),
    }
    text = _get(build(store=_store(state=state))).text
    assert text.count("Daily-loss halt active: no new orders until the next trading day.") == 2


def test_no_data_says_so_plainly_on_every_part():
    text = _get(build(store=FakeStore(snapshot=None))).text
    assert "No account snapshot yet." in text
    assert "No open positions." in text
    assert "No data for this range." in text
    assert "<polyline" not in text and "<circle" not in text


def test_one_snapshot_is_a_dot():
    one = FakeStore(points=[POINTS[0]])
    text = _get(build(store=one)).text
    assert "<circle" in text and "<polyline" not in text


def test_a_stale_snapshot_shows_its_age_on_the_weekend():
    old = {
        "taken_at": datetime(2026, 10, 23, 1, 30, tzinfo=UTC),
        "equity": Decimal("100"),
        "cash": Decimal("1"),
        "buying_power": Decimal("2"),
    }
    built = build(store=FakeStore(snapshot=old))
    built.clock.now = WEEKEND
    assert "(2 d old)" in _get(built).text


@pytest.mark.parametrize(
    ("query", "daily", "since"),
    [
        (
            "",
            False,
            datetime(2026, 10, 12, 4, 0, tzinfo=UTC),
        ),  # default 1w: Mon 00:00 ET a week back
        ("?range=1w", False, datetime(2026, 10, 12, 4, 0, tzinfo=UTC)),
        ("?range=nonsense", False, datetime(2026, 10, 12, 4, 0, tzinfo=UTC)),
        ("?range=1d", False, datetime(2026, 10, 19, 13, 30, tzinfo=UTC)),  # Monday's open
        ("?range=1m", True, datetime(2026, 9, 19, 4, 0, tzinfo=UTC)),
        ("?range=all", True, None),
    ],
)
def test_the_range_chooses_the_query(query, daily, since):
    built = build(store=_store())
    _get(built, "/" + query)
    ((_, (got_since, got_daily)),) = [c for c in built.store.calls if c[0] == "equity_points"]
    assert (got_since, got_daily) == (since, daily)


def test_the_range_links_mark_the_current_one():
    text = _get(build(store=_store()), "/?range=1m").text
    assert '<a href="/?range=1m" aria-current="page">1m</a>' in text
    assert '<a href="/?range=1d">1d</a>' in text


def test_a_database_failure_is_the_503_page_with_no_error_text():
    response = _get(build(store=failing()))
    assert response.status_code == 503
    assert "Database unavailable. Nothing is shown until it is back." in response.text
    assert "OperationalError" not in response.text


def test_without_a_session_nothing_is_read_and_nothing_shown():
    built = build(store=_store())
    response = built.client(follow_redirects=False).get("/")
    assert response.status_code == 303 and response.headers["location"] == "/sign-in"
    assert "100,000.00" not in response.text and built.store.read_calls == 0


# --- health check (analyze S3) --------------------------------------------------------------


def test_healthz_is_open_and_plain():
    built = build(store=_store())
    response = built.client().get("/healthz")
    assert response.status_code == 200 and response.text == "ok"
    assert response.headers["content-type"].startswith("text/plain")
    assert built.store.names() == ["ping"]


@pytest.mark.parametrize(
    "store", [FakeStore(ping_ok=False), failing()], ids=["ping false", "database error"]
)
def test_healthz_is_503_when_the_database_is_not_ok(store):
    response = build(store=store).client().get("/healthz")
    assert response.status_code == 503 and response.text == "unavailable"
    assert response.headers["content-type"].startswith("text/plain")


def test_healthz_asks_the_database_at_most_once_every_ten_seconds():
    built = build(store=_store())
    client = built.client()
    for _ in range(5):
        assert client.get("/healthz").status_code == 200
    assert built.store.read_calls == 1
    built.clock.advance(seconds=9, milliseconds=900)
    client.get("/healthz")
    assert built.store.read_calls == 1
    built.clock.advance(milliseconds=100)
    client.get("/healthz")
    assert built.store.read_calls == 2


def test_a_failure_is_cached_too_and_recovers_after_ten_seconds():
    built = build(store=FakeStore(ping_ok=False))
    client = built.client()
    assert client.get("/healthz").status_code == 503
    built.store.ping_ok = True
    assert client.get("/healthz").status_code == 503  # cached
    built.clock.advance(seconds=10)
    assert client.get("/healthz").status_code == 200


def test_concurrent_health_checks_make_one_database_call():
    built = build(store=_store())
    cache = built.app.state.health
    results = []
    barrier = threading.Barrier(20)

    def hit():
        barrier.wait()
        results.append(cache.ok())

    threads = [threading.Thread(target=hit) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert built.store.read_calls == 1
    assert results.count(True) >= 1 and set(results) <= {True, False}


class BlockingStore(FakeStore):
    """A store whose `read()` waits until released, to hold a health check in flight."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = True
        self.started = 0

    def read(self):
        self.started += 1
        if self.block:
            self.entered.set()
            assert self.release.wait(5)
        return super().read()


def _hammer(cache, count=20) -> tuple[list[bool], list[threading.Thread]]:
    results: list[bool] = []
    threads = [threading.Thread(target=lambda: results.append(cache.ok())) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)
    return results, threads


def test_a_health_check_in_flight_does_not_make_callers_wait_for_the_lock():
    store = BlockingStore()
    cache = build(store=store).app.state.health
    first = threading.Thread(target=cache.ok)
    first.start()
    assert store.entered.wait(5)
    results, threads = _hammer(cache)  # no answer yet: unavailable, at once
    assert not any(t.is_alive() for t in threads) and results == [False] * 20
    assert store.started == 1
    store.release.set()
    first.join(5)


def test_a_health_check_in_flight_returns_the_cached_answer_at_once():
    built = build(store=(store := BlockingStore()))
    store.block = False
    cache = built.app.state.health
    assert cache.ok() is True
    store.block = True
    built.clock.advance(seconds=10)  # the cache is stale: the next call starts a check
    first = threading.Thread(target=cache.ok)
    first.start()
    assert store.entered.wait(5)
    results, threads = _hammer(cache)
    assert not any(t.is_alive() for t in threads) and results == [True] * 20
    assert store.started == 2  # one at the start, one in flight: never more
    store.release.set()
    first.join(5)


def test_healthz_shows_nothing_else():
    built = build(store=_store())
    text = built.client().get("/healthz").text
    assert text == "ok" and timedelta
