"""Which slice of the scan list a run fetches (specs/011-opportunistic-identifier research O3).

Pure and stateless: no database, no memory between runs, and no clock. `now` and `today`
are arguments.

The rule:
1. The scan list is sorted, and cut into consecutive batches of `slice_size` names:
   `B = ceil(U / slice_size)` batches for `U` names.
2. A day's slots are the times from `slots.first`, every `slots.every_minutes`, up to
   `min(slots.last, close - slots.before_close_minutes)`: exactly the orchestrator's
   `oi_slots` rule, so an early close has fewer slots and none is counted that won't run.
3. The run index `k` is the number of slots on every XNYS session from `EPOCH` up to
   yesterday, plus today's slot number: the latest of today's slots at or before `now`, or
   0 before the first.
4. The batch fetched is `k mod B`.

Every slot that exists has its own index, so any `B` consecutive slots fetch every batch
once, and every name is seen within the trading days it takes the calendar to provide `B`
slots (6 on a normal day). A missed slot is never backfilled, the same as the orchestrator:
that batch's turn simply passes.

A late start (accepted, research O3): the agent names its slot from its own clock. The
orchestrator starts a slot only within that slot's interval, but if it starts one in its
last seconds the agent may read the next slot. That batch is then fetched twice in a day
and one batch waits a day. It is rare and harmless; fixing it would mean the orchestrator
passing the slot to the agent.

A non-session day (reachable only by `--dry-run`, since a real run stops at the window
check) has no slots of its own, so it takes the next session's first slot.

Counting slots per past session is a loop over a few hundred days: no caching, no state.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from trading_agent.opportunistic_identifier.config import Slots
from trading_agent.risk import calendar

# The first XNYS session of 2026. Fixed, so a run index never changes meaning.
EPOCH = date(2026, 1, 2)
NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ScanSlice:
    run_index: int
    batch: int
    batches: int  # 0 for an empty scan list
    symbols: tuple[str, ...]


def day_slots(day: date, slots: Slots) -> list[datetime]:
    """The day's slot times, aware ET: the orchestrator's `oi_slots` rule."""
    if not calendar.is_session(day):
        return []
    last = min(
        datetime.combine(day, slots.last, tzinfo=NEW_YORK),
        calendar.close_time(day) - timedelta(minutes=slots.before_close_minutes),
    )
    found = []
    when = datetime.combine(day, slots.first, tzinfo=NEW_YORK)
    while when <= last:
        found.append(when)
        when += timedelta(minutes=slots.every_minutes)
    return found


def slot_number(now: datetime, slots: Slots) -> int:
    """The index of the latest of today's slots at or before `now`; 0 before the first
    slot, and on a day with none."""
    today = day_slots(calendar.trading_day(now), slots)
    due = [index for index, when in enumerate(today) if when <= now]
    return due[-1] if due else 0


def run_index(today: date, now: datetime, slots: Slots) -> int:
    """Slots on every session from the epoch up to the day before `today`, plus today's
    slot number. A non-session `today` gives the next session's first slot."""
    earlier = 0
    day = EPOCH
    while day < today:
        earlier += len(day_slots(day, slots))
        day += timedelta(days=1)
    return earlier + slot_number(now, slots)


def slice_at(universe: Sequence[str], index: int, slice_size: int) -> ScanSlice:
    """The batch run `index` fetches, from the sorted universe."""
    names = sorted(universe)
    if not names:
        return ScanSlice(index, 0, 0, ())
    batches = math.ceil(len(names) / slice_size)
    batch = index % batches
    return ScanSlice(index, batch, batches, tuple(names[batch * slice_size :][:slice_size]))


def slice_for(
    universe: Sequence[str], today: date, now: datetime, slots: Slots, slice_size: int
) -> ScanSlice:
    return slice_at(universe, run_index(today, now, slots), slice_size)
