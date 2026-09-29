"""US1 at the service level: a tick records every candidate once, paced (D7-D9)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from tests.unit.reference.support import make_job

NOW = datetime(2026, 9, 28, 12, 30, tzinfo=UTC)  # Monday 08:30 ET
DAY = date(2026, 9, 28)


def test_one_tick_records_each_symbol_with_three_calls_and_one_list():
    job, fake, store, _ = make_job(["AAPL", "MSFT"])
    report = job.tick(NOW)
    assert report.recorded == 2 and report.failed == 0
    assert [c for _, c, _ in fake.calls].count("list_us_symbols") == 1
    for symbol in ("AAPL", "MSFT"):
        assert sorted(fake.calls_for(symbol)) == ["get_metrics", "get_profile", "get_quote"]
        row = store.rows[(symbol, DAY)]
        assert row.exchange_mic == "XNAS" and row.security_type == "common_stock"


def test_seeds_are_fetched_too():
    job, fake, store, _ = make_job([], seeds=("SPY",))
    fake.add("SPY", type="ETP", mic="ARCX")
    job.tick(NOW)
    assert store.rows[("SPY", DAY)].security_type == "etf"


def test_a_second_tick_the_same_day_makes_no_calls():
    job, fake, store, _ = make_job(["AAPL"])
    job.tick(NOW)
    before = len(fake.calls)
    report = job.tick(NOW + timedelta(minutes=1))
    assert len(fake.calls) == before
    assert report.already == 1 and report.recorded == 0


def test_a_symbol_recorded_by_an_earlier_run_is_not_fetched():
    job, fake, store, _ = make_job(["AAPL"])
    job.tick(NOW)
    # A restarted job: fresh memory, same database.
    job2, fake2, _, _ = make_job(["AAPL"])
    job2.store = store
    job2.tick(NOW + timedelta(minutes=5))
    assert fake2.calls_for("AAPL") == []


def test_the_symbol_list_is_fetched_again_on_a_new_day():
    job, fake, _, _ = make_job(["AAPL"])
    job.tick(NOW)
    job.tick(datetime(2026, 9, 29, 12, 30, tzinfo=UTC))
    assert [c for _, c, _ in fake.calls].count("list_us_symbols") == 2


def test_a_startup_list_tagged_for_another_day_is_not_used():
    job, fake, _, _ = make_job(["AAPL"])
    job.symbol_list = (date(2026, 9, 25), dict(fake.list_us_symbols()))
    fake.calls.clear()
    job.tick(NOW)
    assert [c for _, c, _ in fake.calls].count("list_us_symbols") == 1


def test_a_startup_list_for_today_is_used():
    job, fake, _, _ = make_job(["AAPL"])
    job.symbol_list = (DAY, dict(fake.list_us_symbols()))
    fake.calls.clear()
    job.tick(NOW)
    assert [c for _, c, _ in fake.calls].count("list_us_symbols") == 0


def test_no_calls_before_0800_or_on_a_weekend_or_after_the_close():
    for when in (
        datetime(2026, 9, 28, 11, 30, tzinfo=UTC),
        datetime(2026, 9, 26, 14, tzinfo=UTC),
        datetime(2026, 9, 28, 20, 0, tzinfo=UTC),
    ):
        job, fake, store, _ = make_job(["AAPL"])
        job.tick(when)
        assert fake.calls == [] and store.rows == {}


def test_calls_are_spaced_by_the_configured_rate():
    job, fake, _, _ = make_job(["AAA", "BBB", "CCC"], calls_per_minute=30)
    job.tick(NOW)
    times = [t for t, _, _ in fake.calls]
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert gaps and min(gaps) >= 2.0


def test_a_tick_stops_starting_symbols_after_about_fifty_seconds():
    symbols = [f"S{chr(65 + i)}" for i in range(20)]
    job, fake, store, clock = make_job(symbols, calls_per_minute=30)
    report = job.tick(NOW)
    # 2 s per call: the list plus 3 per symbol; ~50 s allows 8 symbols at most.
    assert 0 < report.recorded <= 9
    assert report.deferred == 20 - report.recorded
    assert clock.t <= 60


def test_sc002_two_hundred_symbols_finish_before_0915():
    symbols = [f"{chr(65 + i // 26)}{chr(65 + i % 26)}X" for i in range(200)]
    job, _, store, clock = make_job(symbols, calls_per_minute=30)
    now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)  # 08:00 ET
    deadline = datetime(2026, 9, 28, 13, 15, tzinfo=UTC)  # 09:15 ET
    while len(store.rows) < 200:
        assert now < deadline, f"only {len(store.rows)} of 200 by 09:15"
        start = clock.t
        job.tick(now)
        # The next tick starts 60 s after this one started, or when it ended if later.
        now += timedelta(seconds=max(60.0, clock.t - start))
