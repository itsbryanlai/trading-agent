"""The sign-in gate (specs/014 tasks T008; research D7, analyze S1)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trading_agent.dashboard.throttle import Acquired, SignInGate, Wait

NOW = datetime(2026, 10, 19, 23, 0, tzinfo=UTC)


def _gate(first=1, cap=30, tracked=1000) -> SignInGate:
    return SignInGate(first, cap, tracked)


def _fail(gate: SignInGate, address: str, now: datetime) -> None:
    assert gate.try_acquire(address, now) == Acquired()
    gate.release(address, False, now)


def test_a_fresh_gate_acquires():
    assert _gate().try_acquire("a", NOW) == Acquired()


def test_twenty_threads_at_the_same_instant_get_exactly_one_acquired():
    gate = _gate()
    barrier = threading.Barrier(20)
    results = []

    def attempt(i: int) -> None:
        barrier.wait()
        results.append(gate.try_acquire(f"addr-{i % 3}", NOW))

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(isinstance(r, Acquired) for r in results) == 1
    assert sum(isinstance(r, Wait) for r in results) == 19


def test_while_a_check_is_in_flight_everyone_else_waits_and_release_frees_the_slot():
    gate = _gate()
    assert gate.try_acquire("a", NOW) == Acquired()
    other = gate.try_acquire("b", NOW)
    assert isinstance(other, Wait) and other.seconds >= 1
    gate.release("a", True, NOW)
    assert gate.try_acquire("b", NOW) == Acquired()


def test_the_slot_is_freed_even_when_the_check_raises():
    gate = _gate()

    def check() -> None:
        assert gate.try_acquire("a", NOW) == Acquired()
        try:
            raise RuntimeError("scrypt blew up")
        finally:
            gate.release("a", False, NOW)

    with pytest.raises(RuntimeError):
        check()
    assert isinstance(gate.try_acquire("b", NOW), Wait)  # the failure's delay, not a stuck slot
    assert gate.try_acquire("b", NOW + timedelta(seconds=1)) == Acquired()


def test_the_delay_doubles_from_one_to_the_cap():
    gate = _gate(first=1, cap=30)
    now = NOW
    waits = []
    for _ in range(8):
        _fail(gate, "a", now)
        blocked = gate.try_acquire("a", now)
        assert isinstance(blocked, Wait)
        # The address delay is added to the site-wide one, so a lone address sees twice.
        waits.append(blocked.seconds // 2)
        now += timedelta(seconds=blocked.seconds)
    assert waits == [1, 2, 4, 8, 16, 30, 30, 30]


def test_the_address_delay_adds_to_the_site_wide_one():
    gate = _gate()
    _fail(gate, "a", NOW)
    assert gate.try_acquire("a", NOW) == Wait(2)  # 1 s site-wide + 1 s for the address
    assert gate.try_acquire("a", NOW + timedelta(seconds=1)) == Acquired()


def test_another_address_sees_only_the_site_wide_part():
    gate = _gate()
    _fail(gate, "a", NOW)
    _fail(gate, "a", NOW + timedelta(seconds=2))  # a's second failure: 2 s site, 2 s address
    now = NOW + timedelta(seconds=2)
    assert gate.try_acquire("a", now) == Wait(4)
    assert gate.try_acquire("b", now) == Wait(2)


def test_after_the_wait_passes_the_gate_always_checks_there_is_no_lockout():
    gate = _gate(first=1, cap=30)
    now = NOW
    for _ in range(40):
        assert gate.try_acquire("a", now) == Acquired()
        gate.release("a", False, now)
        assert isinstance(gate.try_acquire("a", now), Wait)
        now += timedelta(seconds=60)  # past both delays, which never exceed 30 s each


def test_a_wait_counts_down_in_whole_seconds_rounded_up():
    gate = _gate(first=5, cap=30)
    _fail(gate, "a", NOW)  # 5 s site-wide plus 5 s for the address
    assert gate.try_acquire("a", NOW + timedelta(milliseconds=400)) == Wait(10)  # 9.2 s left
    assert gate.try_acquire("a", NOW + timedelta(milliseconds=4100)) == Wait(2)  # 1.8 s left
    assert gate.try_acquire("a", NOW + timedelta(milliseconds=4600)) == Wait(1)  # 0.8 s left
    assert gate.try_acquire("a", NOW + timedelta(seconds=5)) == Acquired()


def test_a_success_resets_the_site_wide_gate_and_that_address_only():
    gate = _gate()
    _fail(gate, "a", NOW)
    _fail(gate, "b", NOW + timedelta(seconds=3))  # site-wide: second failure
    now = NOW + timedelta(seconds=10)
    assert gate.try_acquire("a", now) == Acquired()
    gate.release("a", True, now)  # resets the site-wide gate and a
    assert gate.try_acquire("a", now) == Acquired()
    gate.release("a", True, now)
    assert gate.try_acquire("b", now) == Acquired()
    gate.release("b", False, now)
    # The site-wide count restarted (1 s); b kept its own count, so its second delay is 2 s.
    assert gate.try_acquire("b", now) == Wait(3)
    assert gate.try_acquire("c", now) == Wait(1)


def test_a_too_early_attempt_is_not_counted_as_a_failure():
    gate = _gate()
    _fail(gate, "a", NOW)
    for _ in range(10):
        assert isinstance(gate.try_acquire("a", NOW), Wait)
    assert gate.try_acquire("a", NOW + timedelta(seconds=2)) == Acquired()
    gate.release("a", False, NOW + timedelta(seconds=2))
    assert gate.try_acquire("a", NOW + timedelta(seconds=2)) == Wait(4)  # second failure: 2 + 2


def test_at_most_the_tracked_number_of_addresses_are_kept_oldest_first_out():
    gate = _gate(first=1, cap=30, tracked=10)
    for i in range(11):
        gate.release(f"addr-{i}", False, NOW)  # recording needs no prior acquire
    assert gate.tracked == 10
    # Eleven site-wide failures: 30 s. addr-0 was evicted so adds nothing; addr-1 adds 1 s.
    assert gate.try_acquire("addr-0", NOW) == Wait(30)
    assert gate.try_acquire("addr-1", NOW) == Wait(31)


def test_each_failure_refreshes_an_addresss_place_in_the_eviction_order():
    gate = _gate(first=1, cap=30, tracked=3)
    for address in ("a", "b", "c", "a", "d"):  # a is refreshed, so b is the oldest when d comes
        gate.release(address, False, NOW)
    assert gate.tracked == 3
    # Five site-wide failures: 16 s. a has two failures (2 s), c and d one (1 s), b is forgotten.
    assert gate.try_acquire("a", NOW) == Wait(18)
    assert gate.try_acquire("b", NOW) == Wait(16)
    assert gate.try_acquire("c", NOW) == Wait(17)
    assert gate.try_acquire("d", NOW) == Wait(17)


@settings(max_examples=200, deadline=None)
@given(
    first=st.integers(1, 10),
    extra=st.integers(0, 290),
    steps=st.lists(st.tuples(st.integers(0, 3), st.integers(0, 120), st.booleans()), max_size=60),
)
def test_a_wait_is_always_between_zero_and_twice_the_cap(first, extra, steps):
    cap = first + extra
    gate = SignInGate(first, cap, 10)
    now = NOW
    for address, gap, ok in steps:
        now += timedelta(seconds=gap)
        outcome = gate.try_acquire(f"a{address}", now)
        if isinstance(outcome, Wait):
            assert 1 <= outcome.seconds <= max(2 * cap, 1)
        else:
            gate.release(f"a{address}", ok, now)


def test_waits_reports_the_two_parts_for_the_log():
    gate = _gate()
    assert gate.waits("a", NOW) == (0, 0)
    _fail(gate, "a", NOW)
    _fail(gate, "a", NOW + timedelta(seconds=2))
    now = NOW + timedelta(seconds=2)
    assert gate.waits("a", now) == (2, 2)
    assert gate.waits("b", now) == (2, 0)
    assert gate.waits("a", now + timedelta(seconds=5)) == (0, 0)
